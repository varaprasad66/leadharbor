"""Health + readiness probes (no auth, for load balancers)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_container, get_session

router = APIRouter(tags=["health"])


@router.get("/health")
def health(container=Depends(get_container)):
    s = container.settings
    return {
        "status": "ok",
        "service": "leadharbor",
        "environment": s.environment,
    }


@router.get("/health/ready")
def ready(session: Session = Depends(get_session), container=Depends(get_container)):
    checks: dict = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"
    s = container.settings
    checks["twilio"] = "configured" if s.twilio_account_sid else "not_configured"
    checks["sendgrid"] = "configured" if s.sendgrid_api_key else "not_configured"
    checks["crm"] = s.crm_provider
    checks["llm"] = s.llm_provider
    if checks["database"] != "ok":
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail={"status": "degraded", "checks": checks})
    return {"status": "ok", "checks": checks}
