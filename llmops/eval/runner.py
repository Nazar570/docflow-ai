import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from importlib import metadata
from pathlib import Path
from typing import Any
from uuid import uuid4

from llmops.eval.degraded import build_eval_provider
from llmops.eval.metrics import aggregate_metrics, score_fields
from llmops.eval.models import DatasetManifest, GoldenCase
from services.worker.extraction.prompt_versions import PROMPT_VERSION
from services.worker.providers.contract import ExtractionRequest, GenerationParameters
from services.worker.providers.errors import (
    ProviderOutputError,
    RecoverableProviderError,
)
from services.worker.providers.fake import FakeScenario
from services.worker.rules.business_rules import RuleContext, decide_workflow

DATASETS_ROOT = Path(__file__).resolve().parents[1] / "datasets"
DEFAULT_REPORT_DIR = Path("artifacts/evaluations")


def application_version() -> str:
    try:
        return metadata.version("docflow-ai")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def load_dataset(dataset_version: str) -> tuple[DatasetManifest, list[GoldenCase]]:
    dataset_dir = DATASETS_ROOT / dataset_version
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Dataset manifest not found: {manifest_path}")
    manifest = DatasetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8"),
    )
    if manifest.dataset_version != dataset_version:
        raise ValueError(
            "Manifest dataset_version does not match requested version "
            f"{dataset_version}",
        )
    cases: list[GoldenCase] = []
    for relative in manifest.case_files:
        case_path = dataset_dir / relative
        if not case_path.is_file():
            raise FileNotFoundError(f"Missing golden case file: {case_path}")
        cases.append(
            GoldenCase.model_validate_json(case_path.read_text(encoding="utf-8")),
        )
    return manifest, cases


def _build_rule_context(case: GoldenCase) -> RuleContext:
    overrides = case.rule_overrides
    return RuleContext(
        supported_currencies=overrides.supported_currencies
        or ["EUR", "USD", "PLN"],
        auto_approval_confidence_threshold=(
            overrides.auto_approval_confidence_threshold
            if overrides.auto_approval_confidence_threshold is not None
            else 0.90
        ),
        auto_approval_limit=(
            overrides.auto_approval_limit
            if overrides.auto_approval_limit is not None
            else Decimal("1000.00")
        ),
        totals_tolerance=(
            overrides.totals_tolerance
            if overrides.totals_tolerance is not None
            else Decimal("0.01")
        ),
        supplier_exists=overrides.supplier_exists,
        purchase_order_exists=overrides.purchase_order_exists,
        is_duplicate=overrides.is_duplicate,
    )


def _map_final_state(decision: str | None) -> str | None:
    if decision == "auto_approve":
        return "submitted"
    if decision == "needs_review":
        return "needs_review"
    if decision == "rejected":
        return "rejected"
    if decision == "provider_failure":
        return "failed"
    return None


def evaluate_case(
    case: GoldenCase,
    *,
    provider: Any,
) -> dict[str, Any]:
    started = time.perf_counter()
    result: dict[str, Any] = {
        "case_id": case.case_id,
        "scenario_tags": case.scenario_tags,
        "fake_scenario": case.fake_scenario,
        "schema_applicable": case.schema_applicable,
        "field_comparison_applicable": case.field_comparison_applicable,
        "decision_applicable": case.decision_applicable,
        "expected_decision": case.expected_decision,
        "expected_final_state": case.expected_final_state,
        "expected_error_class": case.expected_error_class,
        "schema_valid": None,
        "actual_decision": None,
        "actual_final_state": None,
        "decision_match": None,
        "field_scores": {},
        "outcome_class": "ok",
        "error_class": None,
        "case_success": False,
        "latency_ms": None,
        "provider": None,
        "model_name": None,
        "prompt_version": None,
    }
    try:
        FakeScenario(case.fake_scenario)
    except ValueError:
        result["outcome_class"] = "unexpected_evaluator_error"
        result["error_class"] = "invalid_fake_scenario"
        result["latency_ms"] = (time.perf_counter() - started) * 1000.0
        return result

    try:
        extraction = provider.extract(
            ExtractionRequest(
                document_text=case.document_text,
                correlation_id=uuid4(),
                document_id=uuid4(),
                generation_parameters=GenerationParameters(),
            ),
        )
    except ProviderOutputError:
        result["schema_valid"] = False
        result["outcome_class"] = "schema_invalid"
        result["error_class"] = "provider_output_error"
        result["actual_decision"] = "needs_review"
        result["actual_final_state"] = "needs_review"
        result["provider"] = getattr(provider, "PROVIDER_NAME", "fake")
        result["model_name"] = getattr(provider, "MODEL_NAME", "unknown")
        result["prompt_version"] = getattr(provider, "_prompt_version", PROMPT_VERSION)
        result["latency_ms"] = (time.perf_counter() - started) * 1000.0
        if case.decision_applicable:
            result["decision_match"] = (
                result["actual_decision"] == case.expected_decision
                and result["actual_final_state"] == case.expected_final_state
            )
        result["field_scores"] = {
            field: score.model_dump()
            for field, score in score_fields(
                case.expected_invoice,
                None,
                applicable=False,
            ).items()
        }
        result["case_success"] = (
            case.expected_error_class == "provider_output_error"
            and case.expected_decision == "needs_review"
            and case.expected_final_state == "needs_review"
        )
        return result
    except RecoverableProviderError as exc:
        result["schema_valid"] = False if case.schema_applicable else None
        result["outcome_class"] = "provider_failure"
        result["error_class"] = exc.error_class.value
        result["actual_decision"] = "provider_failure"
        result["actual_final_state"] = "failed"
        result["provider"] = exc.provider
        result["prompt_version"] = getattr(provider, "_prompt_version", PROMPT_VERSION)
        result["latency_ms"] = (time.perf_counter() - started) * 1000.0
        if case.decision_applicable:
            result["decision_match"] = (
                case.expected_decision == "provider_failure"
                and case.expected_final_state == "failed"
            )
        result["case_success"] = (
            case.expected_error_class == exc.error_class.value
            and case.expected_decision == "provider_failure"
        )
        result["field_scores"] = {
            field: score.model_dump()
            for field, score in score_fields(
                case.expected_invoice,
                None,
                applicable=False,
            ).items()
        }
        return result
    except Exception:
        result["outcome_class"] = "unexpected_evaluator_error"
        result["error_class"] = "unexpected_evaluator_error"
        result["latency_ms"] = (time.perf_counter() - started) * 1000.0
        return result

    result["schema_valid"] = True
    result["provider"] = extraction.provider
    result["model_name"] = extraction.model_name
    result["prompt_version"] = extraction.prompt_version
    result["latency_ms"] = extraction.latency_ms
    actual_invoice = extraction.invoice.model_dump(mode="json")
    result["field_scores"] = {
        field: score.model_dump()
        for field, score in score_fields(
            case.expected_invoice,
            actual_invoice,
            applicable=case.field_comparison_applicable,
        ).items()
    }
    decision = decide_workflow(extraction.invoice, _build_rule_context(case))
    result["actual_decision"] = decision.decision.value
    result["actual_final_state"] = _map_final_state(decision.decision.value)
    if case.decision_applicable:
        result["decision_match"] = (
            result["actual_decision"] == case.expected_decision
            and result["actual_final_state"] == case.expected_final_state
        )
    field_ok = True
    if case.field_comparison_applicable and case.expected_invoice is not None:
        field_ok = all(
            score["compared"] == 0 or score["correct"] == score["compared"]
            for score in result["field_scores"].values()
        )
    decision_ok = (
        result["decision_match"] is True if case.decision_applicable else True
    )
    result["case_success"] = (
        result["schema_valid"] is True
        and field_ok
        and decision_ok
        and case.expected_error_class is None
    )
    result["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
    return result


def run_evaluation(
    *,
    dataset_version: str,
    provider_name: str = "fake",
    fixture: str = "baseline",
    report_dir: Path | None = None,
) -> dict[str, Any]:
    if provider_name != "fake":
        raise ValueError(
            "Default evaluation supports only provider=fake in Step 7",
        )
    manifest, cases = load_dataset(dataset_version)
    provider = build_eval_provider(fixture=fixture, prompt_version=PROMPT_VERSION)
    started = time.perf_counter()
    case_results = [evaluate_case(case, provider=provider) for case in cases]
    metrics = aggregate_metrics(case_results=case_results)
    finished = datetime.now(UTC)
    report = {
        "evaluation_id": str(uuid4()),
        "created_at": finished.isoformat(),
        "dataset_version": manifest.dataset_version,
        "dataset_description": manifest.description,
        "dataset_limitations": manifest.limitations,
        "provider": provider_name,
        "fixture": fixture,
        "model_name": getattr(provider, "MODEL_NAME", "fake-deterministic-v1"),
        "prompt_version": getattr(provider, "_prompt_version", PROMPT_VERSION),
        "generation_parameters": GenerationParameters().model_dump(mode="json"),
        "application_version": application_version(),
        "case_count": len(case_results),
        "metrics": metrics.model_dump(mode="json"),
        "cases": case_results,
        "wall_time_ms": (time.perf_counter() - started) * 1000.0,
    }
    target_dir = report_dir or DEFAULT_REPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = finished.strftime("%Y%m%dT%H%M%SZ")
    report_path = target_dir / (
        f"eval_{manifest.dataset_version}_{provider_name}_{fixture}_{stamp}.json"
    )
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def print_summary(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    print(
        "evaluation_summary "
        f"dataset={report['dataset_version']} "
        f"provider={report['provider']} "
        f"fixture={report['fixture']} "
        f"cases={report['case_count']} "
        f"schema_validity={metrics['schema_validity_rate']} "
        f"field_accuracy={metrics['field_accuracy']} "
        f"decision_accuracy={metrics['decision_accuracy']} "
        f"case_success_rate={metrics['case_success_rate']} "
        f"provider_failures={metrics['provider_failure_count']} "
        f"latency_ms_mean={metrics['latency_ms_mean']} "
        f"latency_ms_p95={metrics['latency_ms_p95']} "
        f"report={report['report_path']}",
    )
