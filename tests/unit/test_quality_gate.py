from pathlib import Path

from llmops.eval.thresholds import (
    MetricFailure,
    ThresholdConfig,
    compare_metrics,
    compare_report_to_thresholds,
    load_threshold_config,
)


def test_threshold_above_and_equal_pass(tmp_path: Path) -> None:
    config = ThresholdConfig(
        dataset_version="v1",
        provider="fake",
        fixture="baseline",
        minimum_metrics={"field_accuracy": 1.0, "decision_accuracy": 0.9},
    )
    path = tmp_path / "baseline.json"
    path.write_text(config.model_dump_json(), encoding="utf-8")
    loaded = load_threshold_config(path)
    assert loaded.minimum_metrics["field_accuracy"] == 1.0
    above = compare_metrics(
        {"field_accuracy": 1.0, "decision_accuracy": 0.95},
        loaded.minimum_metrics,
    )
    equal = compare_metrics(
        {"field_accuracy": 1.0, "decision_accuracy": 0.9},
        loaded.minimum_metrics,
    )
    assert above.passed is True
    assert equal.passed is True


def test_threshold_below_fails() -> None:
    result = compare_metrics(
        {"field_accuracy": 0.5, "decision_accuracy": 1.0},
        {"field_accuracy": 1.0, "decision_accuracy": 1.0},
    )
    assert result.passed is False
    assert any(failure.metric == "field_accuracy" for failure in result.failures)


def test_missing_and_invalid_metrics_fail() -> None:
    missing = compare_metrics({}, {"field_accuracy": 1.0})
    assert missing.passed is False
    assert missing.failures[0].metric == "field_accuracy"
    invalid = compare_metrics({"field_accuracy": "bad"}, {"field_accuracy": 1.0})
    assert invalid.passed is False
    assert "non-numeric" in invalid.failures[0].message


def test_multiple_metric_failures_are_reported() -> None:
    result = compare_metrics(
        {"field_accuracy": 0.1, "decision_accuracy": 0.2},
        {"field_accuracy": 1.0, "decision_accuracy": 1.0},
    )
    assert len(result.failures) == 2


def test_latency_not_in_default_baseline_config() -> None:
    from pathlib import Path

    config = load_threshold_config(Path("llmops/baselines/v1_fake_baseline.json"))
    assert "latency_ms_mean" not in config.minimum_metrics
    assert "latency_ms_p95" not in config.minimum_metrics


def test_compare_report_missing_metrics() -> None:
    config = ThresholdConfig(
        dataset_version="v1",
        provider="fake",
        fixture="baseline",
        minimum_metrics={"field_accuracy": 1.0},
    )
    result = compare_report_to_thresholds({}, config)
    assert result.passed is False
    assert result.failures[0].metric == "metrics"


def test_metric_failure_model() -> None:
    failure = MetricFailure(metric="x", message="y", actual=0.1, required=1.0)
    assert failure.metric == "x"
