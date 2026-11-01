from fastapi import APIRouter

from app.api.routes import appointments, cadence, internal, leads, metrics, webhooks

api_router = APIRouter()
api_router.include_router(webhooks.router)
api_router.include_router(leads.router)
api_router.include_router(leads.public_router)
api_router.include_router(appointments.router)
api_router.include_router(cadence.router)
api_router.include_router(metrics.router)
api_router.include_router(internal.router)

# health probes live at the root (load balancer convention), not under /api/v1
from app.api.routes import health  # noqa: E402

root_router = APIRouter()
root_router.include_router(health.router)
