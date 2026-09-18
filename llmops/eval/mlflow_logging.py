import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow
from mlflow.tracking import MlflowClient

DEFAULT_EXPERIMENT_NAME = "docflow-evaluation"
DEFAULT_TRACKING_URI = "sqlite:///artifacts/mlflow.db"
DEFAULT_ARTIFACT_LOCATION = "./artifacts/mlruns"


def sanitize_metric_name(name: str) -> str:
    cleaned = []
    for char in name:
        if char.isalnum() or char in {"_", "-", "."}:
            cleaned.append(char)
        else:
            cleaned.append("_")
    result = "".join(cleaned)
    while "__" in result:
        result = result.replace("__", "_")
    return result.strip("_") or "metric"


def _prepare_tracking_uri(tracking_uri: str) -> None:
    if tracking_uri.startswith("sqlite:///"):
        db_path = Path(tracking_uri.removeprefix("sqlite:///"))
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)


def _ensure_experiment(
    *,
    experiment_name: str,
    artifact_location: str,
) -> None:
    client = MlflowClient()
    existing = client.get_experiment_by_name(experiment_name)
    if existing is None:
        client.create_experiment(
            experiment_name,
            artifact_location=artifact_location,
        )
    mlflow.set_experiment(experiment_name)


def build_mlflow_params(report: dict[str, Any]) -> dict[str, str]:
    return {
        "dataset_version": str(report.get("dataset_version", "")),
        "provider": str(report.get("provider", "")),
        "fixture": str(report.get("fixture", "")),
        "model_name": str(report.get("model_name", "")),
        "prompt_version": str(report.get("prompt_version", "")),
        "application_version": str(report.get("application_version", "")),
        "case_count": str(report.get("case_count", "")),
    }


def build_mlflow_metrics(report: dict[str, Any]) -> dict[str, float]:
    metrics = report.get("metrics", {})
    if not isinstance(metrics, dict):
        return {}
    output: dict[str, float] = {}
    for key in (
        "schema_validity_rate",
        "field_accuracy",
        "decision_accuracy",
        "case_success_rate",
        "provider_failure_count",
        "expected_provider_failure_count",
        "unexpected_provider_failure_count",
        "unexpected_evaluator_error_count",
        "schema_validity_numerator",
        "schema_validity_denominator",
        "field_accuracy_numerator",
        "field_accuracy_denominator",
        "decision_accuracy_numerator",
        "decision_accuracy_denominator",
        "case_success_numerator",
        "case_success_denominator",
    ):
        value = metrics.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            output[sanitize_metric_name(key)] = float(value)
    per_field = metrics.get("per_field_accuracy")
    if isinstance(per_field, dict):
        for field_name, value in per_field.items():
            if isinstance(value, (int, float)):
                output[
                    sanitize_metric_name(f"field_accuracy_{field_name}")
                ] = float(value)
    return output


def log_evaluation_report(
    report: dict[str, Any],
    *,
    tracking_uri: str = DEFAULT_TRACKING_URI,
    experiment_name: str = DEFAULT_EXPERIMENT_NAME,
    artifact_location: str = DEFAULT_ARTIFACT_LOCATION,
) -> str:
    report_path = report.get("report_path")
    if not isinstance(report_path, str) or not report_path:
        raise ValueError("Evaluation report is missing report_path")
    path = Path(report_path)
    if not path.is_file():
        raise FileNotFoundError(f"Evaluation report file not found: {path}")

    _prepare_tracking_uri(tracking_uri)
    Path(artifact_location).mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_uri)
    _ensure_experiment(
        experiment_name=experiment_name,
        artifact_location=artifact_location,
    )
    with mlflow.start_run() as run:
        mlflow.set_tags(
            {
                "dataset_version": str(report.get("dataset_version", "")),
                "provider": str(report.get("provider", "")),
                "fixture": str(report.get("fixture", "")),
            },
        )
        mlflow.log_params(build_mlflow_params(report))
        metrics = build_mlflow_metrics(report)
        if metrics:
            mlflow.log_metrics(metrics)
        mlflow.log_artifact(str(path), artifact_path="evaluation_report")
        return str(run.info.run_id)


def list_experiment_runs(
    *,
    tracking_uri: str = DEFAULT_TRACKING_URI,
    experiment_name: str = DEFAULT_EXPERIMENT_NAME,
) -> list[dict[str, Any]]:
    _prepare_tracking_uri(tracking_uri)
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return []
    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        order_by=["attributes.start_time DESC"],
    )
    results: list[dict[str, Any]] = []
    for run in runs:
        results.append(
            {
                "run_id": run.info.run_id,
                "params": dict(run.data.params),
                "metrics": dict(run.data.metrics),
                "tags": dict(run.data.tags),
            },
        )
    return results


def list_run_artifacts(
    run_id: str,
    *,
    tracking_uri: str = DEFAULT_TRACKING_URI,
) -> list[str]:
    _prepare_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    artifacts = client.list_artifacts(run_id, path="evaluation_report")
    return [artifact.path for artifact in artifacts]
