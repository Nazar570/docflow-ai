import os
from collections.abc import Callable
from uuid import UUID

from celery import Celery  # type: ignore[import-untyped]


def create_celery_app(broker_url: str | None = None) -> Celery:
    resolved_url = broker_url or os.environ.get(
        "REDIS_URL",
        "redis://localhost:6379/0",
    )
    app = Celery(
        "docflow",
        broker=resolved_url,
        backend=resolved_url,
        include=["services.worker.tasks"],
    )
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
        task_default_queue="docflow",
        task_track_started=True,
    )
    return app


celery_app = create_celery_app()


def enqueue_process_document(
    document_id: UUID,
    correlation_id: UUID,
    *,
    app: Celery | None = None,
) -> str:
    target = app or celery_app
    result = target.send_task(
        "services.worker.tasks.process_document_task",
        args=[str(document_id), str(correlation_id)],
        queue="docflow",
    )
    return str(result.id)


EnqueueFn = Callable[[UUID, UUID], None]
