UV ?= uv
PYTHON := .venv/bin/python
PYTHONPATH := apps/api:packages/contracts:packages/font-engine:.
export PYTHONPATH
DATABASE_URL ?= postgresql+psycopg://penforge:penforge@127.0.0.1:5433/penforge
export DATABASE_URL
# The application gate is portable and cannot execute owner-run research suites.
# Private research metadata/results remain outside the public checkout.
PUBLIC_PYTEST_EXCLUDES := --ignore-glob='tests/backend/test_english*' \
	--ignore-glob='tests/backend/test_volunteer*' \
	--ignore-glob='tests/backend/test_pretrained*' \
	--ignore-glob='tests/backend/test_corpus*' \
	--ignore-glob='tests/backend/test_cvl*' \
	--ignore=tests/backend/test_research.py \
	--ignore=tests/backend/test_deva_recognition.py \
	--ignore=tests/backend/test_production_deva_gate.py \
	--ignore=tests/backend/test_evaluation_harness.py \
	--ignore=tests/backend/test_font_polish.py
.PHONY: setup db-up db-down migrate api readiness test test-frontend test-api test-integration lint contracts contracts-check check research-metadata-check
setup:
	$(UV) sync --locked --group dev --group worker --group research
	npm ci

db-up:
	docker compose -f infrastructure/docker/compose.yaml up -d --wait postgres

db-down:
	docker compose -f infrastructure/docker/compose.yaml down

migrate:
	.venv/bin/alembic -c infrastructure/migrations/alembic.ini upgrade head

api:
	PYTHONPATH=$(PYTHONPATH) .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers

readiness:
	PYTHONPATH=$(PYTHONPATH) .venv/bin/uvicorn app.internal:create_internal_app --factory --host 127.0.0.1 --port 8001 --no-access-log --no-proxy-headers

worker:
	$(PYTHON) -m services.worker

test: test-frontend test-api test-integration

test-frontend:
	npm test

test-api:
	$(PYTHON) -m pytest $(PUBLIC_PYTEST_EXCLUDES) -m 'not integration' -x -v --tb=short -o faulthandler_timeout=60

test-integration:
	@test -n "$$TEST_DATABASE_URL" || (echo 'Set TEST_DATABASE_URL to a disposable real PostgreSQL database.'; exit 1)
	$(PYTHON) -m pytest $(PUBLIC_PYTEST_EXCLUDES) -m 'integration and not emulator' -q

lint:
	.venv/bin/ruff check apps/api packages/contracts/penforge_contracts packages/contracts/scripts packages/capture_safety packages/extraction packages/ml_core packages/font-engine/penforge_font_engine services/worker research tests/backend scripts infrastructure/migrations infrastructure/docker
	.venv/bin/ruff format --check apps/api packages/contracts/penforge_contracts packages/contracts/scripts packages/capture_safety packages/extraction packages/ml_core packages/font-engine/penforge_font_engine services/worker research tests/backend scripts infrastructure/migrations infrastructure/docker
	.venv/bin/mypy apps/api/app packages/contracts/penforge_contracts packages/capture_safety packages/extraction packages/ml_core packages/font-engine/penforge_font_engine services/worker research infrastructure/migrations infrastructure/docker
	$(PYTHON) scripts/check_boundaries.py
	$(PYTHON) scripts/check_docs.py

contracts:
	$(PYTHON) packages/contracts/scripts/export.py
	npm run contracts:generate
	$(PYTHON) -m research.schemas

contracts-check:
	$(PYTHON) packages/contracts/scripts/export.py --check
	$(PYTHON) packages/contracts/scripts/check_compatibility.py
	npm run contracts:check
	./node_modules/.bin/tsc -p packages/contracts/tsconfig.json
	$(PYTHON) -m research.schemas --check

# Local metadata validation only. Real training/inference remains owner-run.
research-metadata-check:
	$(PYTHON) -m research.validate

check: lint contracts-check test
	npm run typecheck
	npm run lint
	npm run build

# Run with the real Auth emulator; use a demo- project only.
test-identity-emulator:
	@test -n "$$TEST_DATABASE_URL" -a -n "$$PENFORGE_TEST_AUTH_EMULATOR_HOST" || (echo "Set TEST_DATABASE_URL and PENFORGE_TEST_AUTH_EMULATOR_HOST."; exit 1)
	$(PYTHON) -m pytest $(PUBLIC_PYTEST_EXCLUDES) -m emulator -q
