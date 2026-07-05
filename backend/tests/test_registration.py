"""Integration test: registration provisions the whole tenancy chain.

Requires a running Postgres (set TEST_DATABASE_URL); skipped otherwise.

Regression note: an earlier version built Project(org_id=org.id) before any
flush — org.id is assigned at INSERT time, so it was still None and every
registration died with a NOT NULL violation misreported as "email already
registered". The migration backfill had provisioned the demo users' orgs,
which masked it. This test pins the real code path.
"""

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)


@pytest.fixture
async def session_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def test_register_creates_user_org_and_default_project(session_factory):
    from sqlalchemy import delete, select

    from app.models import Organization, Project
    from app.services.auth_service import AuthService
    from app.services.exceptions import EmailAlreadyRegisteredError

    email = f"regtest-{uuid.uuid4()}@example.com"
    async with session_factory() as session:
        auth = AuthService(session)
        user = await auth.register(email, "a-strong-password")

        assert user.org_id is not None
        # Plain uuid, captured now: the failed duplicate below rolls the
        # session back, expiring ORM objects — touching org.id afterwards
        # would demand a sync refresh (MissingGreenlet in an async session).
        org_id = user.org_id
        org = await session.get(Organization, org_id)
        assert org is not None and org.name == email
        project = (
            await session.execute(
                select(Project).where(Project.org_id == org_id)
            )
        ).scalar_one()
        assert project.name == "default"

        # Same email again must be the duplicate error, not a 500.
        with pytest.raises(EmailAlreadyRegisteredError):
            await auth.register(email, "another-password")

        await session.execute(delete(Organization).where(Organization.id == org_id))
        await session.commit()
