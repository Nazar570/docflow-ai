FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY services ./services

RUN pip install --no-cache-dir -e .

EXPOSE 8001

CMD ["uvicorn", "services.erp_mock.app:app", "--host", "0.0.0.0", "--port", "8001"]
