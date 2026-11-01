"""LeadHarbor API entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from app.api.routes import api_router, root_router
from app.container import build_container
from app.core.config import get_settings
from app.core.db import Base
from app.core.logging import configure_logging, get_logger

log = get_logger("leadharbor")


def create_app(settings=None, transports: dict | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("DEBUG" if settings.environment == "development" else "INFO")
    container = build_container(settings, transports)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(container.engine)
        log.info("leadharbor started (env=%s crm=%s llm=%s)",
                 settings.environment, settings.crm_provider, settings.llm_provider)
        yield
        container.close()

    app = FastAPI(title="LeadHarbor API", version="1.0.0", lifespan=lifespan)
    app.state.container = container

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id", uuid4().hex[:12])
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    app.include_router(api_router, prefix="/api/v1")
    app.include_router(root_router)
    return app


app = create_app()
