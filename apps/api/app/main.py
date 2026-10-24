import asyncio
import hashlib
import hmac
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from penforge_contracts import ContractRegistry, contract_registry, register_contract_schemas

from app.adapters.auth.base import AuthVerifier
from app.adapters.auth.fake import FakeAuthVerifier
from app.adapters.storage.local_fs import LocalObjectStorage
from app.core.config import Settings
from app.core.errors import ErrorEnvelope, register_errors
from app.core.metrics import Metrics
from app.core.middleware import RequestMiddleware
from app.core.operational_metrics import collect as collect_operational_metrics
from app.core.ratelimit import InMemoryRateLimiter
from app.db.session import make_async_engine
from app.modules.accountability.router import router as accountability_router
from app.modules.accountability.service import AccountabilityService
from app.modules.analysis.router import router as analysis_router
from app.modules.analysis.service import AnalysisService
from app.modules.documents.router import router as documents_router
from app.modules.documents.service import DocumentsService
from app.modules.exports.router import router as exports_router
from app.modules.exports.service import ExportsService
from app.modules.glyphs.router import router as glyphs_router
from app.modules.glyphs.service import GlyphsService
from app.modules.health.schemas import HealthResponse
from app.modules.identity.router import router as identity_router
from app.modules.identity.service import IdentityService
from app.modules.jobs.router import router as jobs_router
from app.modules.privacy.router import router as privacy_router
from app.modules.privacy.service import PrivacyService
from app.modules.projects.router import router as projects_router
from app.modules.projects.service import ProjectsService
from app.modules.uploads.factory import capture_services
from app.modules.uploads.router import router as uploads_router
from app.modules.uploads.service import UploadsService
from app.modules.workspaces.router import router as workspaces_router
from app.modules.workspaces.service import WorkspacesService


def create_app(
    settings: Settings | None = None,
    *,
    auth_verifier: AuthVerifier | None = None,
    identity_service: IdentityService | None = None,
    projects_service: ProjectsService | None = None,
    workspaces_service: WorkspacesService | None = None,
    documents_service: DocumentsService | None = None,
    uploads_service: UploadsService | None = None,
    analysis_service: AnalysisService | None = None,
    glyphs_service: GlyphsService | None = None,
    exports_service: ExportsService | None = None,
    privacy_service: PrivacyService | None = None,
    accountability_service: AccountabilityService | None = None,
) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # Storage is opened only on startup; OpenAPI export has no side effects.
        storage = (
            LocalObjectStorage(
                config.storage_root,
                secret=(
                    hashlib.sha256(
                        b"penforge-local-storage-v1\0"
                        + config.hmac_secret.get_secret_value().encode()
                    ).digest()
                    if config.hmac_secret
                    else None
                ),
                max_upload_bytes=config.upload_max_bytes,
            )
            if config.storage_backend == "local_fs"
            else None
        )
        application.state.storage = storage
        engine = None
        owned_verifier = None
        if config.auth_backend == "firebase":
            if application.state.auth_verifier is None:
                from app.adapters.auth.firebase import FirebaseAuthVerifier

                application.state.auth_verifier = FirebaseAuthVerifier(config)
                owned_verifier = application.state.auth_verifier
            if application.state.identity_service is None:
                engine = make_async_engine(
                    config.database_url.get_secret_value(), pool_size=config.database_pool_size
                )
                application.state.identity_service = IdentityService(
                    engine, claim_max_age_seconds=config.claim_max_age_seconds
                )
                application.state.projects_service = ProjectsService(
                    engine,
                    application.state.identity_service,
                    cursor_secret=application.state.project_cursor_secret,
                    idempotency_ttl_seconds=config.idempotency_retention_seconds,
                )
                application.state.workspaces_service = WorkspacesService(
                    engine, application.state.identity_service
                )
                application.state.documents_service = DocumentsService(
                    engine,
                    application.state.identity_service,
                    cursor_secret=application.state.project_cursor_secret,
                    idempotency_ttl_seconds=config.idempotency_retention_seconds,
                )
        if application.state.accountability_service is None and isinstance(
            application.state.identity_service, IdentityService
        ):
            application.state.accountability_service = AccountabilityService(
                application.state.identity_service, config
            )
        if (
            storage
            and application.state.uploads_service is None
            and isinstance(application.state.identity_service, IdentityService)
        ):
            application.state.uploads_service, application.state.queue_service = capture_services(
                config,
                application.state.identity_service.engine,
                application.state.identity_service,
                storage,
            )
        if isinstance(application.state.uploads_service, UploadsService):
            captures = application.state.uploads_service
            application.state.analysis_service = (
                application.state.analysis_service
                or AnalysisService(
                    captures.engine,
                    captures.identity_service,
                    captures,
                    captures.storage,
                    lease_seconds=config.lease_seconds,
                )
            )
            application.state.glyphs_service = application.state.glyphs_service or GlyphsService(
                captures.engine, captures.identity_service, captures.storage
            )
        if isinstance(application.state.uploads_service, UploadsService):
            captures = application.state.uploads_service
            application.state.exports_service = application.state.exports_service or ExportsService(
                captures.engine,
                captures.identity_service,
                captures,
                captures.storage,
                lease_seconds=config.lease_seconds,
            )
        if (
            application.state.privacy_service is None
            and isinstance(application.state.uploads_service, UploadsService)
            and config.privacy_key
        ):
            captures = application.state.uploads_service
            application.state.privacy_service = PrivacyService(
                captures.engine,
                captures.identity_service,
                captures,
                captures.storage,
                config.privacy_key,
                identity_admin=application.state.auth_verifier,
                lease_seconds=config.lease_seconds,
            )
        if application.state.privacy_service:
            privacy = application.state.privacy_service
            await privacy.restore_reconcile()

            async def erasure_check(uid):
                import asyncio

                return await asyncio.to_thread(privacy.journal.account_blocked, uid)

            privacy.identity.erasure_check = erasure_check
        try:
            yield
        finally:
            if engine:
                await engine.dispose()
            if owned_verifier:
                owned_verifier.close()
            if storage:
                storage.close()

    app = FastAPI(
        title="Penforge API",
        version="1.0.0",
        lifespan=lifespan,
        responses={400: {"model": ErrorEnvelope}, 500: {"model": ErrorEnvelope}},
    )
    app.state.settings = config
    app.state.metrics = Metrics()
    app.state.auth_verifier = auth_verifier or (
        FakeAuthVerifier(config) if config.auth_backend == "fake" else None
    )
    app.state.accountability_service = accountability_service or (
        AccountabilityService(identity_service, config)
        if isinstance(identity_service, IdentityService)
        else None
    )
    app.state.identity_service = identity_service
    app.state.projects_service = projects_service
    app.state.workspaces_service = workspaces_service
    app.state.documents_service = documents_service
    app.state.uploads_service = uploads_service
    app.state.queue_service = None
    app.state.analysis_service = analysis_service
    app.state.glyphs_service = glyphs_service
    app.state.exports_service = exports_service
    app.state.privacy_service = privacy_service
    app.state.identity_hmac_secret = (
        config.hmac_secret.get_secret_value().encode()
        if config.hmac_secret
        else secrets.token_bytes(32)
    )
    cursor_key = (
        config.hmac_secret.get_secret_value()
        if config.hmac_secret
        else config.database_url.get_secret_value()
    )
    app.state.project_cursor_secret = hashlib.sha256(
        b"penforge-project-cursor-v1\0" + cursor_key.encode()
    ).digest()
    if isinstance(identity_service, IdentityService) and projects_service is None:
        app.state.projects_service = ProjectsService(
            identity_service.engine,
            identity_service,
            cursor_secret=app.state.project_cursor_secret,
            idempotency_ttl_seconds=config.idempotency_retention_seconds,
        )
    if isinstance(identity_service, IdentityService) and workspaces_service is None:
        app.state.workspaces_service = WorkspacesService(identity_service.engine, identity_service)
    if isinstance(identity_service, IdentityService) and documents_service is None:
        app.state.documents_service = DocumentsService(
            identity_service.engine,
            identity_service,
            cursor_secret=app.state.project_cursor_secret,
            idempotency_ttl_seconds=config.idempotency_retention_seconds,
        )
    app.state.rate_limiter = (
        InMemoryRateLimiter(app_env=config.app_env)
        if config.rate_limit_backend == "local" and config.app_env in ("local", "test")
        else None
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "X-Request-ID",
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "If-None-Match",
            "Last-Event-ID",
            "X-Deletion-Receipt",
        ],
        expose_headers=[
            "X-Request-ID",
            "ETag",
            "Retry-After",
            "RateLimit-Limit",
            "RateLimit-Remaining",
            "RateLimit-Reset",
        ],
    )
    app.add_middleware(
        RequestMiddleware,
        max_bytes=config.request_max_bytes,
        upload_max_bytes=config.upload_max_bytes,
        metrics=app.state.metrics,
    )
    register_errors(app)
    app.include_router(identity_router)
    app.include_router(accountability_router)
    app.include_router(projects_router)
    app.include_router(workspaces_router)
    app.include_router(documents_router)
    app.include_router(uploads_router)
    app.include_router(jobs_router)
    app.include_router(analysis_router)
    app.include_router(glyphs_router)
    app.include_router(exports_router)
    app.include_router(privacy_router)

    @app.get("/api/v1/health", response_model=HealthResponse, operation_id="getHealth")
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/v1/contracts", response_model=ContractRegistry, operation_id="getContracts")
    async def contracts() -> ContractRegistry:
        return contract_registry()

    @app.get("/internal/metrics", include_in_schema=False)
    async def internal_metrics(request: Request) -> PlainTextResponse:
        """Token-gated scrape; 404 for every unauthorised or unconfigured caller."""
        expected = config.readiness_token
        supplied = request.headers.get("authorization", "")
        if expected is None or not hmac.compare_digest(
            supplied.encode(), ("Bearer " + expected.get_secret_value()).encode()
        ):
            return PlainTextResponse("not found", status_code=404)
        owner = app.state.uploads_service
        engine = getattr(owner, "engine", None) or getattr(
            app.state.identity_service, "engine", None
        )
        extra: list[str] = []
        if engine is not None:
            try:
                async with asyncio.timeout(3):
                    async with engine.connect() as connection:
                        extra = await collect_operational_metrics(connection)
            except Exception:
                extra = ["penforge_scrape_db_ok 0"]
        else:
            extra.append("penforge_scrape_db_ok 0")
        return PlainTextResponse(
            app.state.metrics.render(extra), media_type="text/plain; version=0.0.4"
        )

    base_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        schema = base_openapi()
        paths = schema.get("paths", {})
        for path, methods in paths.items():
            glyph_resource = (
                path.endswith("/glyphs/{glyph_id}")
                or path.endswith("/select-candidate")
                or path.endswith("/glyphs/space")
            )
            if (
                path not in {"/api/v1/projects", "/api/v1/projects/{project_id}"}
                and not glyph_resource
            ):
                continue
            for method, operation in methods.items():
                if method.lower() in {"patch", "delete"} or path.endswith("/select-candidate"):
                    for parameter in operation.get("parameters", []):
                        if parameter.get("name") == "If-Match" and parameter.get("in") == "header":
                            parameter["required"] = True
                            parameter["description"] = (
                                'Required strong ETag returned by GET or a prior mutation, e.g. "v3".'
                            )
                success = next(
                    (code for code in ("200", "201") if code in operation.get("responses", {})),
                    None,
                )
                if success is not None and method.lower() in {"get", "post", "patch"}:
                    operation["responses"][success].setdefault("headers", {})["ETag"] = {
                        "description": "Strong resource row-version ETag.",
                        "schema": {"type": "string", "pattern": '^"v[1-9][0-9]*"$'},
                    }
                if method.lower() == "post" and "201" in operation.get("responses", {}):
                    operation["responses"]["201"].setdefault("headers", {})[
                        "Idempotency-Replayed"
                    ] = {
                        "description": "Present as true when the original create response was replayed.",
                        "schema": {"type": "string", "enum": ["true"]},
                    }
        return register_contract_schemas(schema)

    app.openapi = openapi  # type: ignore[method-assign]
    return app
