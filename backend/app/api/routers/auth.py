from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import Bus, CurrentUser, DbSession
from app.core.config import settings
from app.schemas.auth import RegisterRequest, TokenResponse, UserRead
from app.services.auth_service import AuthService
from app.services.exceptions import AuthenticationError, EmailAlreadyRegisteredError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=UserRead, status_code=status.HTTP_201_CREATED
)
async def register(data: RegisterRequest, session: DbSession) -> UserRead:
    try:
        user = await AuthService(session).register(data.email, data.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.detail) from None
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSession,
    bus: Bus,
    request: Request,
) -> TokenResponse:
    """OAuth2 password flow; `username` carries the email."""
    # Per-IP fixed window against credential stuffing. Fails open if Redis
    # is down — throttling is protection, not correctness.
    client_ip = request.client.host if request.client else "unknown"
    allowed = await bus.check_rate_limit(
        f"login:{client_ip}",
        limit=settings.login_rate_limit_per_minute,
        window_seconds=60,
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts; try again in a minute",
        )
    try:
        token = await AuthService(session).login(form.username, form.password)
    except AuthenticationError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail=exc.detail
        ) from None
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
