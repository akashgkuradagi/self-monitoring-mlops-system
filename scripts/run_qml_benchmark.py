"""Standalone Script to Run the Experimental Quantum ML (QML) Benchmark (EXP-03, FR-07).

Executes:
1. Loads reference credit risk dataset splits (train.csv and val.csv).
2. Runs the Variational Quantum Classifier (VQC) benchmark using PennyLane on 'default.qubit'.
3. Benchmarks against classical Random Forest on identical 4-feature subset and samples.
4. Serializes comparison scorecard to models/qml_benchmark_comparison.json.
5. Logs experiment parameters, circuit topology, and comparative metrics to MLflow under 'qml-benchmarks'.
6. Enforces Architectural Firewall: verifies model cannot be promoted to production.
"""

import json
import logging
from pathlib import Path
import sys
import time

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from app.models.qml_benchmark import qml_benchmark  # noqa: E402
from app.registry.mlflow_adapter import mlflow_adapter  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("scripts.run_qml_benchmark")


def main() -> None:
    """Execute end-to-end QML benchmark, comparison, and MLflow experiment logging."""
    logger.info("Initiating Experimental QML Benchmark execution (EXP-03, FR-07)...")

    train_path = Path("data/reference/train.csv")
    val_path = Path("data/reference/val.csv")

    if not train_path.exists() or not val_path.exists():
        logger.error("Reference datasets not found. Ensure DATA-01 datasets are generated.")
        sys.exit(1)

    train_df = pd.read_csv(train_path)
    val_df = pd.read_csv(val_path)

    # 1. Execute Benchmark & Classical Comparison
    t0 = time.perf_counter()
    benchmark_res = qml_benchmark.run_benchmark(
        train_df=train_df,
        eval_df=val_df,
        n_train=150,
        n_eval=75,
        epochs=5,
        lr=0.05,
    )
    total_duration = time.perf_counter() - t0

    # 2. Persist Comparison Scorecard to models/
    models_dir = Path("models")
    comparison_path = models_dir / "qml_benchmark_comparison.json"
    qml_benchmark.save_comparison(comparison_path)

    # 3. Log to MLflow under 'qml-benchmarks'
    circuit_meta = benchmark_res["circuit_metadata"]
    q_metrics = benchmark_res["quantum_metrics"]

    mlflow_params = {
        "framework": circuit_meta["framework"],
        "framework_version": circuit_meta["framework_version"],
        "simulator_device": circuit_meta["simulator_device"],
        "is_hardware": circuit_meta["is_hardware"],
        "n_qubits": circuit_meta["n_qubits"],
        "n_layers": circuit_meta["n_layers"],
        "embedding_type": circuit_meta["embedding_type"],
        "ansatz": circuit_meta["ansatz"],
        "selected_features": ",".join(circuit_meta["selected_features"]),
        "benchmark_only": True,
        "firewall_enforced": True,
    }

    mlflow_metrics = {
        "qml_accuracy": q_metrics["accuracy"],
        "qml_precision": q_metrics["precision"],
        "qml_recall": q_metrics["recall"],
        "qml_f1_score": q_metrics["f1_score"],
        "qml_training_time_sec": q_metrics["training_time_seconds"],
        "qml_inference_latency_ms": q_metrics["mean_inference_latency_ms"],
        "benchmark_duration_sec": round(total_duration, 4),
    }

    # Add classical baseline metrics for direct comparison
    for comp in benchmark_res["classical_comparison"]:
        if comp["paradigm"] == "Classical":
            mlflow_metrics["classical_accuracy"] = comp["accuracy"]
            mlflow_metrics["classical_f1_score"] = comp["f1_score"]
            mlflow_metrics["classical_training_time_sec"] = comp["training_time_sec"]
            mlflow_metrics["classical_inference_latency_ms"] = comp["inference_latency_ms"]

    run_id = mlflow_adapter.log_qml_experiment_run(
        experiment_name="qml-benchmarks",
        run_name="vqc-pennylane-default-qubit",
        params=mlflow_params,
        metrics=mlflow_metrics,
        tags={
            "stage": "sprint-2-stage-3",
            "issue": "#10",
            "requirement": "FR-07",
            "benchmark_only": "true",
            "production_eligible": "false",
            "firewall_enforced": "true",
        },
        artifact_paths=[str(comparison_path)],
    )

    logger.info("Logged QML benchmark to MLflow experiment 'qml-benchmarks' (Run ID: %s)", run_id)

    # 4. Print Comparison Scorecard
    print("\n" + "=" * 75)
    print("EXP-03 EXPERIMENTAL QUANTUM ML (QML) BENCHMARK SCORECARD")
    print("=" * 75)
    print(f"MLflow Run ID:          {run_id}")
    print(f"Simulator Device:       {circuit_meta['simulator_device']} (Physical QPU: {circuit_meta['is_hardware']})")
    print(f"Circuit Topology:       {circuit_meta['n_qubits']} qubits, {circuit_meta['n_layers']} layers, {circuit_meta['ansatz']}")
    print(f"Feature Subset:         {', '.join(circuit_meta['selected_features'])}")
    print(f"Benchmark Only Flag:    {benchmark_res['benchmark_only']} (Firewall Enforced: {benchmark_res['firewall_enforced']})")
    print("-" * 75)
    print(f"{'Metric':<25} | {'Classical (Random Forest)':<24} | {'Quantum (VQC Simulated)':<22}")
    print("-" * 75)

    comp_dict = {c["model_name"]: c for c in benchmark_res["classical_comparison"]}
    rf_c = comp_dict.get("Classical_Random_Forest", {})
    q_c = comp_dict.get("Variational_Quantum_Classifier", {})

    print(f"{'Accuracy':<25} | {rf_c.get('accuracy', 0.0):<24.4f} | {q_c.get('accuracy', 0.0):<22.4f}")
    print(f"{'F1-Score':<25} | {rf_c.get('f1_score', 0.0):<24.4f} | {q_c.get('f1_score', 0.0):<22.4f}")
    print(f"{'Training Time (sec)':<25} | {rf_c.get('training_time_sec', 0.0):<24.4f} | {q_c.get('training_time_sec', 0.0):<22.4f}")
    print(f"{'Inference Latency (ms)':<25} | {rf_c.get('inference_latency_ms', 0.0):<24.3f} | {q_c.get('inference_latency_ms', 0.0):<22.3f}")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    main()
