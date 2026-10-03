.PHONY: install test lint typecheck schema docker docker-test staging-run

# ── local dev ───────────────────────────────────────────────────────────
install:
	uv pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests_internal

typecheck:
	mypy src/apitest

schema:
	python -m apitest.schema.export

# ── staging suite (uv) ───────────────────────────────────────────────────
# `staging-run SERVICE=<name>` runs one logical service against staging.
# Relies on .env for creds.
SERVICE ?=
PROFILE ?= staging

staging-run:
	@if [ -z "$(SERVICE)" ]; then echo "SERVICE=<name> required"; exit 2; fi
	uv run apitest run --service "$(SERVICE)" --profile "$(PROFILE)" \
	  --report both --report-dir "reports/$(SERVICE)"

# ── docker (local) ──────────────────────────────────────────────────────
docker:
	docker build --network host -t apitest:dev .

docker-test: docker
	@docker run --rm \
	  -v "$(CURDIR)/tests:/work/tests:ro" \
	  -v "$(CURDIR)/profiles:/work/profiles:ro" \
	  -v "$(CURDIR)/reports:/work/reports" \
	  --env-file .env \
	  --network host \
	  apitest:dev run --service "$(SERVICE)" --profile "$(PROFILE)" \
	    --report both --report-dir /work/reports/$(SERVICE)
