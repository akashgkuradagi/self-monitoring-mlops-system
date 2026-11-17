"""PyTorch LSTM Sequence / Temporal Shift Anomaly Detector (EXP-02, FR-06).

Implements an unsupervised LSTM Autoencoder that reconstructs rolling temporal
feature sequences. Evaluates sliding window reconstruction loss to detect
temporal covariate and concept drift independently of ground truth labels.
Decoupled from the production serving pipeline (SAD Section 1.2, Issue #9).
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from app.data.schema import CATEGORICAL_FEATURES, FEATURE_COLUMNS, NUMERICAL_FEATURES

logger = logging.getLogger("mlops.dl_monitor")


class LSTMEncoder(nn.Module):
    """Encodes a temporal sequence into a compressed latent representation."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 32,
        latent_dim: int = 16,
        num_layers: int = 1,
    ):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
        )
        self.fc = nn.Linear(hidden_dim, latent_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass encoding sequence (batch_size, seq_len, input_dim) -> (batch_size, latent_dim)."""
        _, (h_n, _) = self.lstm(x)
        # h_n[-1] corresponds to the top LSTM layer at the final time step
        latent = self.fc(h_n[-1])
        return latent


class LSTMDecoder(nn.Module):
    """Decodes a latent representation back into the original sequence dimension."""

    def __init__(
        self,
        latent_dim: int,
        hidden_dim: int = 32,
        output_dim: int = 18,
        seq_len: int = 10,
        num_layers: int = 1,
    ):
        super().__init__()
        self.seq_len = seq_len
        self.fc = nn.Linear(latent_dim, hidden_dim)
        self.lstm = nn.LSTM(
            input_size=hidden_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
        )
        self.out_fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, latent: torch.Tensor) -> torch.Tensor:
        """Forward pass decoding (batch_size, latent_dim) -> (batch_size, seq_len, output_dim)."""
        h = torch.relu(self.fc(latent))
        repeated = h.unsqueeze(1).repeat(1, self.seq_len, 1)
        lstm_out, _ = self.lstm(repeated)
        recon = self.out_fc(lstm_out)
        return recon


class LSTMAutoencoder(nn.Module):
    """Full sequence autoencoder combining encoder and decoder modules."""

    def __init__(
        self,
        input_dim: int = 18,
        hidden_dim: int = 32,
        latent_dim: int = 16,
        seq_len: int = 10,
        num_layers: int = 1,
    ):
        super().__init__()
        self.seq_len = seq_len
        self.input_dim = input_dim
        self.encoder = LSTMEncoder(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            latent_dim=latent_dim,
            num_layers=num_layers,
        )
        self.decoder = LSTMDecoder(
            latent_dim=latent_dim,
            hidden_dim=hidden_dim,
            output_dim=input_dim,
            seq_len=seq_len,
            num_layers=num_layers,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Pass input sequence through encoder and decoder."""
        latent = self.encoder(x)
        recon = self.decoder(latent)
        return recon


class DLSequenceMonitor:
    """Manages training, sliding-window scoring, and evaluation of the LSTM temporal shift monitor."""

    def __init__(
        self,
        window_size: int = 10,
        hidden_dim: int = 32,
        latent_dim: int = 16,
        num_layers: int = 1,
        lr: float = 0.003,
        batch_size: int = 64,
        epochs: int = 15,
        random_state: int = 42,
        device: Optional[str] = None,
    ):
        self.window_size = window_size
        self.hidden_dim = hidden_dim
        self.latent_dim = latent_dim
        self.num_layers = num_layers
        self.lr = lr
        self.batch_size = batch_size
        self.epochs = epochs
        self.random_state = random_state

        if device:
            self.device = torch.device(device)
        else:
            self.device = torch.device("cpu")

        self.model: Optional[LSTMAutoencoder] = None
        self.preprocessor: Optional[ColumnTransformer] = None
        self.input_dim: Optional[int] = None
        self.threshold: Optional[float] = None
        self.is_fitted: bool = False
        self.training_metadata: Dict[str, Any] = {}

    def _init_preprocessor(self) -> ColumnTransformer:
        """Construct scikit-learn ColumnTransformer for numerical scaling and one-hot encoding."""
        return ColumnTransformer(
            transformers=[
                ("num", StandardScaler(), NUMERICAL_FEATURES),
                (
                    "cat",
                    OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    CATEGORICAL_FEATURES,
                ),
            ]
        )

    def _create_sequences(
        self,
        features_array: np.ndarray,
        window_size: Optional[int] = None,
        step_size: int = 1,
    ) -> torch.Tensor:
        """Create sliding sequential temporal windows preserving chronological order without shuffling.

        Args:
            features_array: 2D numpy array of shape (N, D).
            window_size: Length of each sequential window (defaults to self.window_size).
            step_size: Stride between consecutive windows.

        Returns:
            torch.Tensor of shape (num_windows, window_size, D).
        """
        w = window_size or self.window_size
        n_samples = len(features_array)
        if n_samples < w:
            raise ValueError(
                f"Insufficient sample count: got {n_samples} samples, but window_size={w} requires at least {w}."
            )

        sequences = []
        for i in range(0, n_samples - w + 1, step_size):
            sequences.append(features_array[i : i + w])

        return torch.tensor(np.array(sequences), dtype=torch.float32, device=self.device)

    def fit_sequence_model(
        self,
        train_df: pd.DataFrame,
        val_df: Optional[pd.DataFrame] = None,
        epochs: Optional[int] = None,
        lr: Optional[float] = None,
        batch_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Train the unsupervised LSTM Autoencoder on tabular baseline reference features.

        Preserves chronological ordering, avoids target leakage, and calculates an anomaly
        reconstruction threshold on validation data.

        Args:
            train_df: Baseline training dataset (features only, target excluded).
            val_df: Optional baseline validation dataset for threshold calibration.
            epochs: Optional epoch override.
            lr: Optional learning rate override.
            batch_size: Optional batch size override.

        Returns:
            Dictionary of training metrics, thresholds, and execution details.
        """
        torch.manual_seed(self.random_state)
        np.random.seed(self.random_state)

        # Ensure required features are present
        missing = [col for col in FEATURE_COLUMNS if col not in train_df.columns]
        if missing:
            raise ValueError(f"Missing required feature columns for training: {missing}")

        num_epochs = epochs or self.epochs
        learning_rate = lr or self.lr
        bs = batch_size or self.batch_size

        start_time = time.perf_counter()

        # 1. Feature Preprocessing (ColumnTransformer)
        self.preprocessor = self._init_preprocessor()
        X_train_raw = train_df[FEATURE_COLUMNS]
        X_train = self.preprocessor.fit_transform(X_train_raw).astype(np.float32)
        self.input_dim = X_train.shape[1]

        # 2. Sequence Construction
        train_sequences = self._create_sequences(X_train, window_size=self.window_size, step_size=1)

        # 3. Initialize PyTorch LSTM Autoencoder
        self.model = LSTMAutoencoder(
            input_dim=self.input_dim,
            hidden_dim=self.hidden_dim,
            latent_dim=self.latent_dim,
            seq_len=self.window_size,
            num_layers=self.num_layers,
        ).to(self.device)

        optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=learning_rate,
            weight_decay=1e-5,
        )
        criterion = nn.MSELoss()

        dataset = TensorDataset(train_sequences)
        loader = DataLoader(dataset, batch_size=bs, shuffle=True)

        # 4. Training Loop
        loss_history: List[float] = []
        for epoch in range(num_epochs):
            self.model.train()
            epoch_loss = 0.0
            for (batch_x,) in loader:
                optimizer.zero_grad()
                recon = self.model(batch_x)
                loss = criterion(recon, batch_x)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item() * len(batch_x)

            epoch_loss /= len(train_sequences)
            loss_history.append(epoch_loss)

        # 5. Validation and Threshold Calibration
        self.model.eval()
        if val_df is not None:
            missing_val = [col for col in FEATURE_COLUMNS if col not in val_df.columns]
            if missing_val:
                raise ValueError(f"Missing required feature columns in validation dataset: {missing_val}")
            X_val = self.preprocessor.transform(val_df[FEATURE_COLUMNS]).astype(np.float32)
            val_sequences = self._create_sequences(X_val, window_size=self.window_size, step_size=1)
        else:
            # Fall back to training sequences
            val_sequences = train_sequences

        with torch.no_grad():
            val_recon = self.model(val_sequences)
            # Per-window mean squared error across time steps and features
            val_errors = torch.mean((val_recon - val_sequences) ** 2, dim=(1, 2)).cpu().numpy()

        # Calibration: 95th percentile of validation reconstruction error
        self.threshold = float(np.percentile(val_errors, 95))
        val_mean_loss = float(np.mean(val_errors))
        val_anomaly_ratio = float(np.mean(val_errors >= self.threshold))

        duration_sec = time.perf_counter() - start_time
        self.is_fitted = True

        self.training_metadata = {
            "model_type": "LSTMAutoencoder",
            "framework": "PyTorch",
            "device": str(self.device),
            "input_dim": self.input_dim,
            "hidden_dim": self.hidden_dim,
            "latent_dim": self.latent_dim,
            "window_size": self.window_size,
            "epochs": num_epochs,
            "learning_rate": learning_rate,
            "batch_size": bs,
            "random_state": self.random_state,
            "train_samples_count": len(train_df),
            "train_sequences_count": len(train_sequences),
            "val_sequences_count": len(val_sequences),
            "final_train_loss": float(loss_history[-1]),
            "val_mean_reconstruction_error": val_mean_loss,
            "reconstruction_threshold": self.threshold,
            "val_anomaly_ratio": val_anomaly_ratio,
            "training_duration_seconds": round(duration_sec, 3),
            "fitted_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Trained LSTMAutoencoder in %.2fs: TrainLoss=%.4f, ValLoss=%.4f, Threshold=%.4f",
            duration_sec,
            loss_history[-1],
            val_mean_loss,
            self.threshold,
        )
        return self.training_metadata

    def score_window(
        self,
        window_data: Union[pd.DataFrame, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Score an individual sequential temporal observation window.

        Args:
            window_data: DataFrame or list of dicts with length >= self.window_size.

        Returns:
            Dict containing reconstruction_error, threshold, is_shift, and anomaly_score.
        """
        if not self.is_fitted or self.model is None or self.preprocessor is None:
            raise RuntimeError("DLSequenceMonitor is not fitted. Call fit_sequence_model() or load() first.")

        if isinstance(window_data, list):
            df = pd.DataFrame(window_data)
        elif isinstance(window_data, pd.DataFrame):
            df = window_data
        else:
            raise TypeError("window_data must be a pandas DataFrame or a list of dictionaries.")

        missing = [col for col in FEATURE_COLUMNS if col not in df.columns]
        if missing:
            raise ValueError(f"Input records missing required feature columns: {missing}")

        if len(df) < self.window_size:
            raise ValueError(
                f"Window length {len(df)} is insufficient; minimum sequence length is {self.window_size}."
            )

        # Slice exactly the last self.window_size records if more are provided
        window_slice = df[FEATURE_COLUMNS].iloc[-self.window_size :]
        X_encoded = self.preprocessor.transform(window_slice).astype(np.float32)

        # Convert to tensor (1, window_size, input_dim)
        tensor_in = torch.tensor(X_encoded, dtype=torch.float32, device=self.device).unsqueeze(0)

        self.model.eval()
        with torch.no_grad():
            recon = self.model(tensor_in)
            error = float(torch.mean((recon - tensor_in) ** 2).item())

        threshold = self.threshold or 0.5
        is_shift = bool(error >= threshold)
        # Normalized anomaly severity score relative to threshold
        anomaly_score = float(min(1.0, error / (2.0 * threshold)))

        return {
            "window_size": self.window_size,
            "reconstruction_error": round(error, 5),
            "threshold": round(threshold, 5),
            "is_shift": is_shift,
            "anomaly_score": round(anomaly_score, 5),
            "details": {
                "input_dim": self.input_dim,
                "evaluated_records_count": len(df),
            },
        }

    def evaluate(
        self,
        dataset: Union[pd.DataFrame, List[Dict[str, Any]]],
        window_size: Optional[int] = None,
        step_size: int = 1,
    ) -> Dict[str, Any]:
        """Evaluate a continuous stream of observations by sliding temporal windows.

        Args:
            dataset: DataFrame or list of dicts.
            window_size: Sequence window size (defaults to self.window_size).
            step_size: Step stride between consecutive sliding windows.

        Returns:
            Structured summary of sliding window reconstruction errors, shift counts, and ratios.
        """
        if not self.is_fitted or self.model is None or self.preprocessor is None:
            raise RuntimeError("DLSequenceMonitor is not fitted. Call fit_sequence_model() or load() first.")

        if isinstance(dataset, list):
            df = pd.DataFrame(dataset)
        elif isinstance(dataset, pd.DataFrame):
            df = dataset
        else:
            raise TypeError("dataset must be a pandas DataFrame or a list of dictionaries.")

        missing = [col for col in FEATURE_COLUMNS if col not in df.columns]
        if missing:
            raise ValueError(f"Dataset missing required feature columns: {missing}")

        w = window_size or self.window_size
        if len(df) < w:
            raise ValueError(f"Dataset contains {len(df)} records, less than window size {w}.")

        X_encoded = self.preprocessor.transform(df[FEATURE_COLUMNS]).astype(np.float32)
        sequences = self._create_sequences(X_encoded, window_size=w, step_size=step_size)

        self.model.eval()
        with torch.no_grad():
            recon = self.model(sequences)
            errors = torch.mean((recon - sequences) ** 2, dim=(1, 2)).cpu().numpy()

        threshold = self.threshold or 0.5
        shift_mask = errors >= threshold
        shift_count = int(np.sum(shift_mask))
        shift_ratio = float(np.mean(shift_mask))
        mean_error = float(np.mean(errors))

        window_scores = []
        for i, err in enumerate(errors):
            window_scores.append(
                {
                    "window_size": w,
                    "window_index": i,
                    "reconstruction_error": round(float(err), 5),
                    "threshold": round(threshold, 5),
                    "is_shift": bool(err >= threshold),
                    "anomaly_score": round(float(min(1.0, float(err) / (2.0 * threshold))), 5),
                }
            )

        return {
            "total_records": len(df),
            "window_size": w,
            "step_size": step_size,
            "total_windows": len(sequences),
            "threshold": round(threshold, 5),
            "mean_reconstruction_error": round(mean_error, 5),
            "shift_windows_count": shift_count,
            "shift_ratio": round(shift_ratio, 5),
            "windows": window_scores,
        }

    def save(self, model_dir: Union[str, Path]) -> Tuple[Path, Path]:
        """Serialize PyTorch weights and preprocessor to disk.

        Args:
            model_dir: Target directory path.

        Returns:
            Tuple of (model_weights_path, metadata_path).
        """
        if not self.is_fitted or self.model is None or self.preprocessor is None:
            raise RuntimeError("Cannot save unfitted DLSequenceMonitor.")

        directory = Path(model_dir)
        directory.mkdir(parents=True, exist_ok=True)

        weights_path = directory / "dl_sequence_monitor.pt"
        preprocessor_path = directory / "dl_sequence_preprocessor.joblib"
        metadata_path = directory / "dl_sequence_metadata.json"

        # Save PyTorch state dict
        torch.save(
            {
                "state_dict": self.model.state_dict(),
                "input_dim": self.input_dim,
                "hidden_dim": self.hidden_dim,
                "latent_dim": self.latent_dim,
                "seq_len": self.window_size,
                "num_layers": self.num_layers,
            },
            weights_path,
        )

        # Save scikit-learn preprocessor
        joblib.dump(self.preprocessor, preprocessor_path)

        # Save metadata and calibration
        metadata = {
            "window_size": self.window_size,
            "input_dim": self.input_dim,
            "hidden_dim": self.hidden_dim,
            "latent_dim": self.latent_dim,
            "num_layers": self.num_layers,
            "threshold": self.threshold,
            "training_metadata": self.training_metadata,
        }
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        logger.info("Saved DLSequenceMonitor to %s", directory)
        return weights_path, metadata_path

    def load(self, model_dir: Union[str, Path]) -> None:
        """Load PyTorch weights, preprocessor, and configuration metadata from disk.

        Args:
            model_dir: Directory containing serialized model artifacts.
        """
        directory = Path(model_dir)
        weights_path = directory / "dl_sequence_monitor.pt"
        preprocessor_path = directory / "dl_sequence_preprocessor.joblib"
        metadata_path = directory / "dl_sequence_metadata.json"

        if not weights_path.exists() or not preprocessor_path.exists() or not metadata_path.exists():
            raise FileNotFoundError(f"Missing required model artifacts in {directory}")

        with open(metadata_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        self.window_size = meta["window_size"]
        self.input_dim = meta["input_dim"]
        self.hidden_dim = meta["hidden_dim"]
        self.latent_dim = meta["latent_dim"]
        self.num_layers = meta["num_layers"]
        self.threshold = meta["threshold"]
        self.training_metadata = meta.get("training_metadata", {})

        # Load preprocessor
        self.preprocessor = joblib.load(preprocessor_path)

        # Initialize and load model weights
        checkpoint = torch.load(weights_path, map_location=self.device, weights_only=True)
        self.model = LSTMAutoencoder(
            input_dim=self.input_dim,
            hidden_dim=self.hidden_dim,
            latent_dim=self.latent_dim,
            seq_len=self.window_size,
            num_layers=self.num_layers,
        ).to(self.device)
        self.model.load_state_dict(checkpoint["state_dict"])
        self.model.eval()

        self.is_fitted = True
        logger.info("Loaded DLSequenceMonitor from %s (Threshold: %.4f)", directory, self.threshold or 0.0)


# Module-level singleton instance for application use
dl_sequence_monitor = DLSequenceMonitor()
