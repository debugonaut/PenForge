# Independent verification runtime with pinned compiler and validator dependencies.
FROM python:3.12.15-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
WORKDIR /app
# Apply current Debian security fixes, then discard package index files.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
# Preserve the project dependency graph; global installers are build-only.
RUN uv sync --locked --no-dev --group worker --no-group research \
    && /usr/local/bin/python -B -m pip uninstall --yes pip \
    && /usr/local/bin/python -B -c "import ensurepip, pathlib, shutil; shutil.rmtree(pathlib.Path(ensurepip.__file__).parent)" \
    && rm /usr/local/bin/uv
COPY services/worker services/worker
COPY apps/api apps/api
COPY packages/contracts/penforge_contracts packages/contracts/penforge_contracts
COPY packages/capture_safety packages/capture_safety
COPY packages/extraction packages/extraction
COPY packages/ml_core packages/ml_core
COPY packages/font-engine packages/font-engine
COPY packages/contracts/templates packages/contracts/templates
COPY packages/contracts/policies packages/contracts/policies
COPY packages/contracts/unicode packages/contracts/unicode
COPY packages/contracts/fixtures packages/contracts/fixtures
COPY scripts/image_smoke.py image_smoke.py
ENV PYTHONPATH=/app/apps/api:/app/packages/contracts:/app/packages/font-engine:/app
USER 65532:65532
CMD [".venv/bin/python", "-m", "services.worker"]
