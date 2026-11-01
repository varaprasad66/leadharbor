"""Cadence runner endpoint — called by the n8n scheduler every 15 minutes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import api_key, get_container, get_session
from app.services.cadence import run_cadence

router = APIRouter(tags=["cadence"], dependencies=[Depends(api_key)])


@router.post("/cadence/run")
def run(session: Session = Depends(get_session), container=Depends(get_container)):
    summary = run_cadence(session, container)
    session.commit()
    return {"ok": True, "summary": summary}
