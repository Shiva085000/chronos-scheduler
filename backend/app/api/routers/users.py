"""User management (RBAC): list org users and update roles."""

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.models.user import UserRole
from app.repositories.users import UserRepository
from app.schemas.auth import UserRead
from app.services.exceptions import ForbiddenError, NotFoundError
from app.services.rbac import require_role

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=list[UserRead],
    summary="List users in your organization",
)
async def list_users(user: CurrentUser, session: DbSession) -> list:
    require_role(user, UserRole.ADMIN)
    return await UserRepository(session).list_for_org(user.org_id)


class RoleUpdate:
    """Inline body schema to avoid circular import."""
    pass


from pydantic import BaseModel, Field


class RoleUpdateBody(BaseModel):
    role: UserRole = Field(description="New role to assign")


@router.patch(
    "/{user_id}/role",
    response_model=UserRead,
    summary="Change a user's role (owner only)",
)
async def update_role(
    user_id: uuid.UUID,
    body: RoleUpdateBody,
    user: CurrentUser,
    session: DbSession,
) -> UserRead:
    require_role(user, UserRole.OWNER)
    if user_id == user.id:
        raise ForbiddenError("cannot change your own role")
    updated = await UserRepository(session).update_role(
        user_id, user.org_id, body.role
    )
    if updated is None:
        raise NotFoundError("user not found in your organization")
    await session.commit()
    return updated
