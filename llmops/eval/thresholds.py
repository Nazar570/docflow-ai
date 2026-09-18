from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

FLOAT_EPS = 1e-9


class ThresholdConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    fixture: str = Field(min_length=1)
    minimum_metrics: dict[str, float]


class MetricFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    message: str
    actual: float | None = None
    required: float | None = None


class ThresholdComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    failures: list[MetricFailure] = Field(default_factory=list)


def load_threshold_config(path: Path) -> ThresholdConfig:
    return ThresholdConfig.model_validate_json(path.read_text(encoding="utf-8"))


def compare_metrics(
    actual_metrics: dict[str, Any],
    minimum_metrics: dict[str, float],
) -> ThresholdComparisonResult:
    failures: list[MetricFailure] = []
    for metric_name, required in minimum_metrics.items():
        if metric_name not in actual_metrics:
            failures.append(
                MetricFailure(
                    metric=metric_name,
                    message=f"Missing metric: {metric_name}",
                    required=required,
                ),
            )
            continue
        raw_value = actual_metrics[metric_name]
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            failures.append(
                MetricFailure(
                    metric=metric_name,
                    message=(
                        f"Invalid non-numeric metric value for {metric_name}: "
                        f"{raw_value!r}"
                    ),
                    required=required,
                ),
            )
            continue
        actual = float(raw_value)
        if actual + FLOAT_EPS < required:
            failures.append(
                MetricFailure(
                    metric=metric_name,
                    message=(
                        f"Metric {metric_name}={actual} is below required "
                        f"threshold {required}"
                    ),
                    actual=actual,
                    required=required,
                ),
            )
    return ThresholdComparisonResult(passed=len(failures) == 0, failures=failures)


def compare_report_to_thresholds(
    report: dict[str, Any],
    config: ThresholdConfig,
) -> ThresholdComparisonResult:
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        return ThresholdComparisonResult(
            passed=False,
            failures=[
                MetricFailure(
                    metric="metrics",
                    message="Evaluation report is missing metrics object",
                ),
            ],
        )
    return compare_metrics(metrics, config.minimum_metrics)
