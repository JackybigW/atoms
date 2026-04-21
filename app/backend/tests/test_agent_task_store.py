import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from models.agent_tasks import AgentTasks
from services.agent_task_store import AgentTaskStore


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_task_store_creates_and_lists_project_tasks(db_session):
    store = AgentTaskStore(db_session)
    task = await store.create_task(
        project_id=42,
        request_key="req-1",
        subject="Create homepage",
        description="Implement landing page shell",
    )
    listed = await store.list_tasks(project_id=42, request_key="req-1")

    assert task.subject == "Create homepage"
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_agent_task_store_serializes_blocked_by_as_json_text(db_session):
    store = AgentTaskStore(db_session)
    created = await store.create_task(
        project_id=42,
        request_key="req-2",
        subject="Create homepage",
        description="Implement landing page shell",
        blocked_by=["design", "approval"],
    )

    assert created.blocked_by == '["design", "approval"]'

    result = await db_session.execute(select(AgentTasks).where(AgentTasks.id == created.id))
    stored = result.scalar_one()
    assert stored.blocked_by == '["design", "approval"]'
