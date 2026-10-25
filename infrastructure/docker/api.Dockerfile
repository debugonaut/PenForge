FROM python:3.12.15-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
WORKDIR /app
# Apply current Debian security fixes, then discard package index files.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/app/apps/api:/app/packages/contracts \
    APP_ENV=production AUTH_BACKEND=disabled STORAGE_BACKEND=disabled \
    UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
# Preserve the project dependency graph; global installers are build-only.
RUN uv sync --locked --no-dev --no-group worker --no-group research \
    && /usr/local/bin/python -B -m pip uninstall --yes pip \
    && /usr/local/bin/python -B -c "import ensurepip, pathlib, shutil; shutil.rmtree(pathlib.Path(ensurepip.__file__).parent)" \
    && rm /usr/local/bin/uv
COPY apps/api apps/api
COPY packages/contracts/penforge_contracts packages/contracts/penforge_contracts
COPY packages/contracts/templates packages/contracts/templates
COPY packages/contracts/policies packages/contracts/policies
COPY packages/contracts/unicode packages/contracts/unicode
COPY packages/contracts/fixtures packages/contracts/fixtures
COPY scripts/image_smoke.py image_smoke.py
USER 65532:65532
EXPOSE 8000
CMD [".venv/bin/uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
