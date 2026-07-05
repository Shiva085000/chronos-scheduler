"""Role-based access control.

Roles form a strict hierarchy: owner > admin > member > viewer.
Each API endpoint declares its minimum required role, and the check
compares ordinal positions — no set intersection needed.
"""

from app.models import User
from app.models.user import UserRole
from app.services.exceptions import ForbiddenError

# Ordered from most to least privileged.
_HIERARCHY: list[UserRole] = [
    UserRole.OWNER,
    UserRole.ADMIN,
    UserRole.MEMBER,
    UserRole.VIEWER,
]
_RANK = {role: i for i, role in enumerate(_HIERARCHY)}


def require_role(user: User, minimum: UserRole) -> None:
    """Raise ForbiddenError if the user's role is below ``minimum``."""
    user_rank = _RANK.get(user.role, len(_HIERARCHY))
    min_rank = _RANK.get(minimum, 0)
    if user_rank > min_rank:
        raise ForbiddenError(
            f"this action requires at least the '{minimum.value}' role; "
            f"your role is '{user.role.value}'"
        )
