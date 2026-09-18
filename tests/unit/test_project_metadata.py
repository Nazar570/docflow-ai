from importlib.metadata import metadata

import services.erp_mock
import services.ingestion
import services.worker


def test_distribution_name_is_docflow_ai() -> None:
    assert metadata("docflow-ai")["Name"] == "docflow-ai"


def test_requires_python_includes_312() -> None:
    requires_python = metadata("docflow-ai")["Requires-Python"]
    assert requires_python is not None
    assert "3.12" in requires_python


def test_service_packages_are_importable() -> None:
    assert services.ingestion.__name__ == "services.ingestion"
    assert services.erp_mock.__name__ == "services.erp_mock"
    assert services.worker.__name__ == "services.worker"
