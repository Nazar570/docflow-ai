.PHONY: install lint typecheck test check compose-validate migrate up down ps logs eval eval-degraded quality-gate seed-demo run-demo smoke load-test

QUALITY_GATE_FIXTURE ?= baseline

install:
	python3.12 -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"

lint:
	. .venv/bin/activate && ruff check .

typecheck:
	. .venv/bin/activate && mypy .

test:
	. .venv/bin/activate && pytest

check: lint typecheck test

compose-validate:
	docker compose config

migrate:
	. .venv/bin/activate && alembic upgrade head

up:
	docker compose up -d --build

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs --tail=200

eval:
	. .venv/bin/activate && python -m llmops.eval.run --provider fake --dataset-version v1

eval-degraded:
	. .venv/bin/activate && python -m llmops.eval.run --provider fake --dataset-version v1 --fixture degraded

quality-gate:
	. .venv/bin/activate && python -m llmops.eval.gate --provider fake --dataset-version v1 --fixture $(QUALITY_GATE_FIXTURE)

seed-demo:
	. .venv/bin/activate && python -m scripts.seed_demo

run-demo:
	. .venv/bin/activate && python -m scripts.run_demo

smoke:
	. .venv/bin/activate && python -m scripts.smoke

load-test:
	. .venv/bin/activate && python -m scripts.load_test
