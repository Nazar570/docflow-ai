from pathlib import Path

import pytest

from llmops.eval.gate import evaluate_against_thresholds
from llmops.eval.mlflow_logging import (
    build_mlflow_metrics,
    build_mlflow_params,
    list_experiment_runs,
    list_run_artifacts,
    log_evaluation_report,
    sanitize_metric_name,
)
from llmops.eval.runner import run_evaluation


def test_sanitize_metric_name() -> None:
    assert sanitize_metric_name("field accuracy/supplier") == "field_accuracy_supplier"


def test_mlflow_param_and_metric_mapping() -> None:
    report = {
        "dataset_version": "v1",
        "provider": "fake",
        "fixture": "baseline",
        "model_name": "fake-deterministic-v1",
        "prompt_version": "invoice_extraction_v1",
        "application_version": "0.1.0",
        "case_count": 14,
        "metrics": {
            "field_accuracy": 1.0,
            "decision_accuracy": 1.0,
            "per_field_accuracy": {"supplier_name": 1.0},
            "provider_failure_count": 1,
        },
    }
    params = build_mlflow_params(report)
    metrics = build_mlflow_metrics(report)
    assert params["fixture"] == "baseline"
    assert metrics["field_accuracy"] == 1.0
    assert metrics["field_accuracy_supplier_name"] == 1.0
    assert "latency_ms_mean" not in metrics


def test_mlflow_logs_baseline_and_degraded_comparably(tmp_path: Path) -> None:
    tracking = f"sqlite:///{tmp_path / 'mlflow.db'}"
    artifact_root = str(tmp_path / "mlruns")
    baseline = run_evaluation(
        dataset_version="v1",
        provider_name="fake",
        fixture="baseline",
        report_dir=tmp_path / "baseline",
    )
    degraded = run_evaluation(
        dataset_version="v1",
        provider_name="fake",
        fixture="degraded",
        report_dir=tmp_path / "degraded",
    )
    baseline_id = log_evaluation_report(
        baseline,
        tracking_uri=tracking,
        artifact_location=artifact_root,
    )
    degraded_id = log_evaluation_report(
        degraded,
        tracking_uri=tracking,
        artifact_location=artifact_root,
    )
    assert baseline_id != degraded_id
    runs = list_experiment_runs(tracking_uri=tracking)
    assert len(runs) >= 2
    fixtures = {run["params"].get("fixture") for run in runs}
    assert fixtures == {"baseline", "degraded"}
    by_fixture = {run["params"]["fixture"]: run for run in runs}
    assert (
        by_fixture["degraded"]["metrics"]["field_accuracy"]
        < by_fixture["baseline"]["metrics"]["field_accuracy"]
    )
    artifacts = list_run_artifacts(baseline_id, tracking_uri=tracking)
    assert any(path.endswith(".json") for path in artifacts)


def test_baseline_quality_gate_passes_without_static_checks(tmp_path: Path) -> None:
    code = evaluate_against_thresholds(
        dataset_version="v1",
        provider="fake",
        fixture="baseline",
        baseline_path=Path("llmops/baselines/v1_fake_baseline.json"),
        report_dir=tmp_path / "reports",
        log_mlflow=False,
        tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )
    assert code == 0


def test_degraded_quality_gate_fails_with_threshold_message(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = evaluate_against_thresholds(
        dataset_version="v1",
        provider="fake",
        fixture="degraded",
        baseline_path=Path("llmops/baselines/v1_fake_baseline.json"),
        report_dir=tmp_path / "reports",
        log_mlflow=False,
        tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )
    assert code == 1
    captured = capsys.readouterr().out
    assert "quality_gate_status FAIL" in captured
    assert "quality_gate_failure" in captured
    assert "field_accuracy" in captured or "decision_accuracy" in captured


def test_quality_gate_optional_mlflow_logging(tmp_path: Path) -> None:
    tracking = f"sqlite:///{tmp_path / 'mlflow.db'}"
    code = evaluate_against_thresholds(
        dataset_version="v1",
        provider="fake",
        fixture="baseline",
        baseline_path=Path("llmops/baselines/v1_fake_baseline.json"),
        report_dir=tmp_path / "reports",
        log_mlflow=True,
        tracking_uri=tracking,
    )
    assert code == 0
    runs = list_experiment_runs(tracking_uri=tracking)
    assert len(runs) >= 1
    assert runs[0]["params"]["fixture"] == "baseline"
