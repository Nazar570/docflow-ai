FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY services ./services
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir -e .

ENV APP_ENV=development
ENV DOCUMENT_STORAGE_DIR=/data/documents

RUN mkdir -p /data/documents

CMD ["celery", "-A", "services.worker.celery_app.celery_app", "worker", "--loglevel=INFO", "-Q", "docflow", "--pool=solo"]
