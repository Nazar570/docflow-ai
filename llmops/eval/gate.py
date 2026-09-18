import argparse
import os
import subprocess
import sys
from pathlib import Path

from llmops.eval.mlflow_logging import DEFAULT_TRACKING_URI, log_evaluation_report
from llmops.eval.runner import print_summary, run_evaluation
from llmops.eval.thresholds import (
    compare_report_to_thresholds,
    load_threshold_config,
)

DEFAULT_BASELINE_PATH = Path("llmops/baselines/v1_fake_baseline.json")


def run_command(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def run_static_checks() -> None:
    run_command([sys.executable, "-m", "ruff", "check", "."])
    run_command([sys.executable, "-m", "mypy", "."])
    run_command([sys.executable, "-m", "pytest"])


def evaluate_against_thresholds(
    *,
    dataset_version: str,
    provider: str,
    fixture: str,
    baseline_path: Path,
    report_dir: Path,
    log_mlflow: bool,
    tracking_uri: str,
) -> int:
    config = load_threshold_config(baseline_path)
    if config.dataset_version != dataset_version:
        print(
            "quality_gate_error baseline dataset_version "
            f"{config.dataset_version} does not match requested {dataset_version}",
        )
        return 1
    if config.provider != provider:
        print(
            "quality_gate_error baseline provider "
            f"{config.provider} does not match requested {provider}",
        )
        return 1

    report = run_evaluation(
        dataset_version=dataset_version,
        provider_name=provider,
        fixture=fixture,
        report_dir=report_dir,
    )
    print_summary(report)
    if log_mlflow:
        run_id = log_evaluation_report(report, tracking_uri=tracking_uri)
        print(f"mlflow_run_id={run_id}")

    comparison = compare_report_to_thresholds(report, config)
    metrics = report["metrics"]
    print(
        "quality_gate_result "
        f"dataset={dataset_version} "
        f"fixture={fixture} "
        f"passed={comparison.passed} "
        f"schema_validity_rate={metrics.get('schema_validity_rate')} "
        f"field_accuracy={metrics.get('field_accuracy')} "
        f"decision_accuracy={metrics.get('decision_accuracy')} "
        f"case_success_rate={metrics.get('case_success_rate')} "
        f"report={report.get('report_path')}",
    )
    if comparison.passed:
        print("quality_gate_status PASS")
        return 0
    for failure in comparison.failures:
        print(f"quality_gate_failure {failure.message}")
    print("quality_gate_status FAIL")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llmops.eval.gate")
    parser.add_argument("--provider", default="fake", choices=["fake"])
    parser.add_argument("--dataset-version", default="v1")
    parser.add_argument(
        "--fixture",
        default="baseline",
        choices=["baseline", "degraded"],
    )
    parser.add_argument(
        "--baseline-config",
        default=str(DEFAULT_BASELINE_PATH),
    )
    parser.add_argument("--report-dir", default="artifacts/evaluations")
    parser.add_argument("--skip-static-checks", action="store_true")
    parser.add_argument("--log-mlflow", action="store_true")
    parser.add_argument("--mlflow-tracking-uri", default=None)
    args = parser.parse_args(argv)

    if not args.skip_static_checks:
        run_static_checks()

    tracking_uri = (
        args.mlflow_tracking_uri
        or os.environ.get("MLFLOW_TRACKING_URI")
        or DEFAULT_TRACKING_URI
    )
    return evaluate_against_thresholds(
        dataset_version=args.dataset_version,
        provider=args.provider,
        fixture=args.fixture,
        baseline_path=Path(args.baseline_config),
        report_dir=Path(args.report_dir),
        log_mlflow=args.log_mlflow,
        tracking_uri=tracking_uri,
    )


if __name__ == "__main__":
    raise SystemExit(main())
