from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.container import Container
from app.core.security import require_api_key


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_session(container: Container = Depends(get_container)) -> Iterator[Session]:
    session = container.sessionmaker()
    try:
        yield session
    finally:
        session.close()


def api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    require_api_key(request, x_api_key)
