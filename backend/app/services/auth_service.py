import uuid

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.repositories.users import UserRepository
from app.services.exceptions import (
    AuthenticationError,
    EmailAlreadyRegisteredError,
    NotFoundError,
)

logger = structlog.get_logger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(self, email: str, password: str) -> User:
        normalized = email.strip().lower()
        user = User(email=normalized, password_hash=hash_password(password))
        self.users.add(user)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise EmailAlreadyRegisteredError("email already registered") from None
        logger.info("auth.user_registered", user_id=str(user.id))
        return user

    async def login(self, email: str, password: str) -> str:
        user = await self.users.get_by_email(email.strip().lower())
        if user is None or not verify_password(password, user.password_hash):
            raise AuthenticationError("invalid email or password")
        logger.info("auth.login", user_id=str(user.id))
        return create_access_token(str(user.id))

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("user not found")
        return user
