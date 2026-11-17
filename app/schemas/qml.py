"""Pydantic Schemas for Experimental Quantum ML (QML) Benchmark (EXP-03, FR-07).

Defines request and response schemas for QML circuit benchmarks, classical-vs-quantum
comparisons, and architectural firewall metadata.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class QMLBenchmarkRequest(BaseModel):
    """Configuration payload for initiating an experimental QML benchmark run."""

    n_train_samples: int = Field(
        default=100,
        ge=20,
        le=500,
        description="Number of reference training samples for quantum circuit optimization.",
    )
    n_eval_samples: int = Field(
        default=50,
        ge=10,
        le=200,
        description="Number of validation samples for quantum evaluation.",
    )
    epochs: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Optimization epochs for the variational quantum circuit.",
    )
    learning_rate: float = Field(
        default=0.05,
        ge=0.001,
        le=0.5,
        description="Optimizer learning rate.",
    )


class QMLCircuitMetadata(BaseModel):
    """Metadata describing quantum simulation parameters and circuit topology."""

    framework: str = Field(..., description="Quantum machine learning library (e.g. PennyLane).")
    framework_version: str = Field(..., description="Version of the QML library.")
    simulator_device: str = Field(..., description="Local statevector simulator backend.")
    is_hardware: bool = Field(default=False, description="Whether execution occurred on physical QPU.")
    n_qubits: int = Field(..., description="Number of simulated qubits in the register.")
    n_layers: int = Field(..., description="Depth / number of variational ansatz layers.")
    embedding_type: str = Field(..., description="Quantum feature encoding scheme (e.g. AngleEmbedding).")
    ansatz: str = Field(..., description="Parameterized variational circuit ansatz.")
    selected_features: List[str] = Field(..., description="Subset of input features encoded into qubits.")


class QMLComparisonMetrics(BaseModel):
    """Comparative performance metrics between classical models and the QML benchmark."""

    model_name: str = Field(..., description="Model identifier.")
    paradigm: str = Field(..., description="'Classical' or 'Quantum (Simulated)'.")
    accuracy: Optional[float] = Field(None, description="Classification accuracy on evaluation split.")
    f1_score: Optional[float] = Field(None, description="F1-score.")
    training_time_sec: float = Field(..., description="Total training execution time in seconds.")
    inference_latency_ms: float = Field(..., description="Mean per-sample inference latency in milliseconds.")
    features_used_count: int = Field(..., description="Total feature count consumed by the model.")


class QMLBenchmarkResponse(BaseModel):
    """Comprehensive benchmark results, comparison scorecard, and firewall validation."""

    status: str = Field(default="SUCCESS", description="Benchmark execution status.")
    benchmark_only: bool = Field(
        default=True,
        description="Immutable flag confirming model is restricted from production promotion.",
    )
    circuit_metadata: QMLCircuitMetadata = Field(..., description="Quantum circuit configuration.")
    quantum_metrics: Dict[str, Any] = Field(..., description="Quantum model evaluation metrics.")
    classical_comparison: List[QMLComparisonMetrics] = Field(
        ...,
        description="Side-by-side performance comparison against classical baselines.",
    )
    firewall_enforced: bool = Field(
        default=True,
        description="Confirms architectural firewall blocks production promotion.",
    )
    notes: List[str] = Field(default_factory=list, description="Observations and limitations.")
