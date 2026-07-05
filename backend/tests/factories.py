"""Shared builders for integration tests that need real rows.

Jobs now hang off a full org -> project -> queue chain; building it by
hand in every test obscures what the test is about. Deleting the returned
org cascades away everything the helper created (users, projects, queues,
jobs), so cleanup stays a one-liner.
"""

import uuid

from app.models import Organization, Project, Queue, User


async def create_owner_with_queue(
    session, queue_name: str, **queue_kwargs
) -> tuple[Organization, User, Queue]:
    """Create org -> default project -> queue -> user; flushed, not committed."""
    tag = uuid.uuid4()
    org = Organization(name=f"test-{tag}")
    session.add(org)
    await session.flush()
    project = Project(org_id=org.id, name="default")
    session.add(project)
    await session.flush()
    queue = Queue(project_id=project.id, name=queue_name, **queue_kwargs)
    session.add(queue)
    user = User(
        email=f"test-{tag}@example.com", password_hash="x", org_id=org.id
    )
    session.add(user)
    await session.flush()
    return org, user, queue
