import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.user import UserRole


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    def add(self, user: User) -> User:
        self.session.add(user)
        return user

    async def list_for_org(self, org_id: uuid.UUID) -> list[User]:
        result = await self.session.execute(
            select(User)
            .where(User.org_id == org_id)
            .order_by(User.created_at.asc())
        )
        return list(result.scalars().all())

    async def update_role(
        self, user_id: uuid.UUID, org_id: uuid.UUID, role: UserRole
    ) -> User | None:
        result = await self.session.execute(
            update(User)
            .where(User.id == user_id, User.org_id == org_id)
            .values(role=role)
            .returning(User)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none()
