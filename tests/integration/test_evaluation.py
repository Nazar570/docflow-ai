from pathlib import Path

from llmops.eval.runner import run_evaluation


def test_baseline_evaluation_produces_valid_report(tmp_path: Path) -> None:
    report = run_evaluation(
        dataset_version="v1",
        provider_name="fake",
        fixture="baseline",
        report_dir=tmp_path,
    )
    assert report["dataset_version"] == "v1"
    assert report["provider"] == "fake"
    assert report["fixture"] == "baseline"
    assert report["case_count"] >= 12
    metrics = report["metrics"]
    assert metrics["schema_validity_denominator"] > 0
    assert metrics["decision_accuracy"] == 1.0
    assert metrics["case_success_rate"] == 1.0
    assert Path(report["report_path"]).is_file()


def test_baseline_is_deterministic_except_timing_fields(tmp_path: Path) -> None:
    first = run_evaluation(
        dataset_version="v1",
        provider_name="fake",
        fixture="baseline",
        report_dir=tmp_path / "a",
    )
    second = run_evaluation(
        dataset_version="v1",
        provider_name="fake",
        fixture="baseline",
        report_dir=tmp_path / "b",
    )
    for key in (
        "evaluation_id",
        "created_at",
        "report_path",
        "wall_time_ms",
    ):
        first.pop(key)
        second.pop(key)
    for case in first["cases"]:
        case.pop("latency_ms", None)
        case.pop("elapsed_ms", None)
    for case in second["cases"]:
        case.pop("latency_ms", None)
        case.pop("elapsed_ms", None)
    first["metrics"].pop("latency_ms_mean", None)
    first["metrics"].pop("latency_ms_p95", None)
    second["metrics"].pop("latency_ms_mean", None)
    second["metrics"].pop("latency_ms_p95", None)
    assert first == second


def test_degraded_fixture_regresses_accuracy(tmp_path: Path) -> None:
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
    assert degraded["metrics"]["field_accuracy"] < baseline["metrics"]["field_accuracy"]
    assert (
        degraded["metrics"]["decision_accuracy"]
        < baseline["metrics"]["decision_accuracy"]
    )


def test_default_evaluator_does_not_require_compose_network(tmp_path: Path) -> None:
    report = run_evaluation(
        dataset_version="v1",
        provider_name="fake",
        fixture="baseline",
        report_dir=tmp_path,
    )
    assert report["provider"] == "fake"
    assert all(
        case.get("provider") in {None, "fake"}
        or case.get("outcome_class") in {"schema_invalid", "provider_failure"}
        for case in report["cases"]
    )
