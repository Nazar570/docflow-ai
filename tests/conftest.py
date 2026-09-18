import os
from collections.abc import Callable, Generator
from pathlib import Path
from uuid import UUID

os.environ.setdefault("OTEL_TRACES_ENABLED", "false")

import psycopg
import pytest
import redis
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from services.ingestion.app import create_app
from services.persistence.db import (
    create_db_engine,
    create_session_factory,
    session_scope,
)
from services.persistence.models import Base, DocumentRecord
from services.settings import Settings
from services.worker.dlq import DeadLetterQueue
from services.worker.executor.erp_client import ErpClient
from services.worker.providers.fake import FakeLLMProvider
from services.worker.workflow.process_document import process_document


def postgres_available(database_url: str) -> bool:
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(dsn, connect_timeout=2) as connection:
            connection.execute("SELECT 1")
        return True
    except Exception:
        return False


def erp_available(base_url: str) -> bool:
    try:
        client = ErpClient(base_url=base_url, timeout_seconds=2.0)
        try:
            response = client._client.get("/health")
            return response.status_code == 200
        finally:
            client.close()
    except Exception:
        return False


def redis_available(redis_url: str) -> bool:
    try:
        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
    )


@pytest.fixture(scope="session")
def erp_base_url() -> str:
    return os.environ.get("ERP_BASE_URL", "http://localhost:8001")


@pytest.fixture(scope="session")
def redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture(scope="session")
def require_postgres(database_url: str) -> None:
    if not postgres_available(database_url):
        pytest.skip("PostgreSQL is not available on DATABASE_URL")


@pytest.fixture(scope="session")
def require_erp(erp_base_url: str) -> None:
    if not erp_available(erp_base_url):
        pytest.skip("ERP mock is not available on ERP_BASE_URL")


@pytest.fixture(scope="session")
def require_redis(redis_url: str) -> None:
    if not redis_available(redis_url):
        pytest.skip("Redis is not available on REDIS_URL")


@pytest.fixture
def settings(
    tmp_path: Path,
    database_url: str,
    erp_base_url: str,
    redis_url: str,
) -> Settings:
    return Settings(
        app_env="test",
        database_url=database_url,
        erp_base_url=erp_base_url,
        redis_url=redis_url,
        llm_mode="test",
        llm_primary_provider="fake",
        document_storage_dir=tmp_path / "documents",
        celery_worker_max_retries=3,
        celery_retry_backoff_base=2.0,
        celery_retry_backoff_max=1.0,
    )


@pytest.fixture
def migrated_db(
    require_postgres: None,
    database_url: str,
    erp_base_url: str,
    redis_url: str,
) -> Generator[sessionmaker[Session], None, None]:
    os.environ["DATABASE_URL"] = database_url
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_db_engine(
        Settings(
            app_env="test",
            database_url=database_url,
            erp_base_url=erp_base_url,
            redis_url=redis_url,
            document_storage_dir=Path("data/documents"),
        ),
    )
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def erp_client(
    require_erp: None,
    erp_base_url: str,
) -> Generator[ErpClient, None, None]:
    client = ErpClient(base_url=erp_base_url, timeout_seconds=5.0)
    yield client
    client.close()


@pytest.fixture
def cleared_dlq(
    require_redis: None,
    redis_url: str,
) -> Generator[DeadLetterQueue, None, None]:
    dlq = DeadLetterQueue(redis_url=redis_url)
    dlq.clear()
    yield dlq
    dlq.clear()
    dlq.close()


@pytest.fixture
def fake_provider() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def sync_worker_enqueue(
    settings: Settings,
    migrated_db: sessionmaker[Session],
    erp_client: ErpClient,
    fake_provider: FakeLLMProvider,
) -> Callable[[UUID, UUID], None]:
    def enqueue(document_id: UUID, correlation_id: UUID) -> None:
        with session_scope(migrated_db) as session:
            document = session.get(DocumentRecord, document_id)
            assert document is not None
            assert document.correlation_id == correlation_id
            process_document(
                session,
                document=document,
                settings=settings,
                provider=fake_provider,
                erp_client=erp_client,
            )

    return enqueue


@pytest.fixture
def ingestion_client(
    settings: Settings,
    migrated_db: sessionmaker[Session],
    sync_worker_enqueue: Callable[[UUID, UUID], None],
    erp_client: ErpClient,
) -> Generator[TestClient, None, None]:
    app = create_app(
        settings,
        session_factory=migrated_db,
        enqueue_document=sync_worker_enqueue,
        erp_client=erp_client,
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def ingestion_client_deferred(
    settings: Settings,
    migrated_db: sessionmaker[Session],
) -> Generator[tuple[TestClient, list[tuple[UUID, UUID]]], None, None]:
    queued: list[tuple[UUID, UUID]] = []

    def enqueue(document_id: UUID, correlation_id: UUID) -> None:
        queued.append((document_id, correlation_id))

    app = create_app(
        settings,
        session_factory=migrated_db,
        enqueue_document=enqueue,
    )
    with TestClient(app) as client:
        yield client, queued
