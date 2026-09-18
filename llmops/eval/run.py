import argparse
import os
from pathlib import Path

from llmops.eval.mlflow_logging import DEFAULT_TRACKING_URI, log_evaluation_report
from llmops.eval.runner import print_summary, run_evaluation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llmops.eval.run")
    parser.add_argument("--provider", default="fake", choices=["fake"])
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument(
        "--fixture",
        default="baseline",
        choices=["baseline", "degraded"],
    )
    parser.add_argument("--report-dir", default="artifacts/evaluations")
    parser.add_argument("--log-mlflow", action="store_true")
    parser.add_argument("--mlflow-tracking-uri", default=None)
    args = parser.parse_args(argv)
    report = run_evaluation(
        dataset_version=args.dataset_version,
        provider_name=args.provider,
        fixture=args.fixture,
        report_dir=Path(args.report_dir),
    )
    print_summary(report)
    if args.log_mlflow:
        tracking_uri = (
            args.mlflow_tracking_uri
            or os.environ.get("MLFLOW_TRACKING_URI")
            or DEFAULT_TRACKING_URI
        )
        run_id = log_evaluation_report(
            report,
            tracking_uri=tracking_uri,
        )
        print(f"mlflow_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
