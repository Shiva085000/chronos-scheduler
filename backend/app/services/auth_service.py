import uuid

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.models import Organization, Project, User
from app.repositories.tenancy import TenancyRepository
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
        """Create the user plus their personal organization and a "default"
        project, atomically — every user always has a place to put queues."""
        normalized = email.strip().lower()
        tenancy = TenancyRepository(self.session)
        org = tenancy.add_org(Organization(name=normalized))
        # Flush so org.id (assigned at INSERT, not construction) exists
        # before the rows that reference it are built. Orgs carry no unique
        # constraints, so this flush cannot raise the duplicate-email error
        # handled below — that still surfaces at commit.
        await self.session.flush()
        tenancy.add_project(Project(org_id=org.id, name="default"))
        user = User(
            email=normalized, password_hash=hash_password(password), org_id=org.id
        )
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
