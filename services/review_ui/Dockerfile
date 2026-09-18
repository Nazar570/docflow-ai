FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY services ./services
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir -e .

ENV APP_ENV=development
ENV REVIEW_API_BASE_URL=http://ingestion:8000

EXPOSE 8501

CMD ["streamlit", "run", "services/review_ui/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--browser.gatherUsageStats=false"]
