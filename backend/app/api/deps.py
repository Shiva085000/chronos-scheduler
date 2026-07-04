"""FastAPI dependencies: DB session, event bus, current user, services."""

import uuid
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db
from app.events import EventBus
from app.models import User
from app.services.auth_service import AuthService
from app.services.exceptions import NotFoundError
from app.services.job_service import JobService
from app.services.stats_service import StatsService
from app.services.worker_service import WorkerService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


Bus = Annotated[EventBus, Depends(get_event_bus)]


async def get_current_user(
    session: DbSession, token: Annotated[str, Depends(oauth2_scheme)]
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (pyjwt.PyJWTError, KeyError, ValueError):
        raise credentials_error from None
    try:
        return await AuthService(session).get_user(user_id)
    except NotFoundError:
        raise credentials_error from None


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_job_service(session: DbSession, bus: Bus) -> JobService:
    return JobService(session, bus)


def get_stats_service(session: DbSession, bus: Bus) -> StatsService:
    return StatsService(session, bus)


def get_worker_service(session: DbSession) -> WorkerService:
    return WorkerService(session)


Jobs = Annotated[JobService, Depends(get_job_service)]
Stats = Annotated[StatsService, Depends(get_stats_service)]
Workers = Annotated[WorkerService, Depends(get_worker_service)]
