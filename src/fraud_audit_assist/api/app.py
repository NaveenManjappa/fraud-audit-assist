"""The FastAPI application: lifespan, middleware, error mapping and routes."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from fraud_audit_assist.api.routes import health, v1
from fraud_audit_assist.config import Settings, get_settings
from fraud_audit_assist.container import Container, build_container
from fraud_audit_assist.errors import ConflictError, FraudAuditError, InvalidInputError, NotFoundError
from fraud_audit_assist.observability.logging import configure_logging, get_logger
from fraud_audit_assist.observability.tracing import configure_tracing

log = get_logger(__name__)

ContainerFactory = Callable[[Settings], AbstractAsyncContextManager[Container]]

_STATUS = {InvalidInputError: 422, NotFoundError: 404, ConflictError: 409}


def create_app(
    settings: Settings | None = None, container_factory: ContainerFactory = build_container
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with container_factory(settings) as container:
            if settings.environment != "production" and settings.database_url.startswith("sqlite"):
                # Development convenience; deployments run `alembic upgrade head` before starting.
                await container.db.create_all()
            app.state.container = container
            await container.worker.start()
            log.info(
                "service started",
                pack=container.pack.pack.id,
                rules_version=container.pack.rules.version,
                llm_provider=settings.llm.provider,
                model=container.model_name,
                auth_enabled=settings.auth_enabled,
            )
            if not settings.auth_enabled:
                log.warning("API keys are not set: every endpoint is open (development only)")
            try:
                yield
            finally:
                await container.worker.stop()

    configure_logging(settings.log_level, settings.log_json)
    configure_tracing(settings.service_name, settings.otel_exporter_otlp_endpoint)

    app = FastAPI(
        title="fraud-audit-assist",
        version="0.1.0",
        description="Rules-first fraud detection, LangGraph investigation agents, and a human analyst in the loop.",
        lifespan=lifespan,
    )
    app.middleware("http")(_request_context)
    app.add_exception_handler(FraudAuditError, _domain_error)  # type: ignore[arg-type]
    app.include_router(health)
    app.include_router(v1)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics")
    return app


async def _request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    incoming = request.headers.get("X-Request-ID", "")
    # Accept a caller's id only if it is short and plain, so it cannot inject into log lines.
    request_id = (
        incoming if 0 < len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id, method=request.method, path=request.url.path
    )
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error")
        response = JSONResponse({"detail": "internal error", "request_id": request_id}, status_code=500)
    response.headers["X-Request-ID"] = request_id
    return response


async def _domain_error(request: Request, error: FraudAuditError) -> JSONResponse:
    code = next(
        (c for t, c in _STATUS.items() if isinstance(error, t)), status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    if code >= 500:
        log.error("domain error", error_type=type(error).__name__, error=str(error))
        return JSONResponse({"detail": "internal error"}, status_code=code)
    return JSONResponse({"detail": str(error)}, status_code=code)
