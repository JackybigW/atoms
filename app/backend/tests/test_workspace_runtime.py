import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.workspace_runtime import router
from schemas.auth import UserResponse


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

fake_user = UserResponse(id="user-1", email="test@example.com", name="Test", role="user")


class FakeDB:
    """Minimal async DB session stub that has no records."""

    async def execute(self, *args, **kwargs):
        return _FakeResult([])

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    def add(self, obj):
        pass

    async def rollback(self):
        pass


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalar_one_or_none(self):
        return None

    def scalar(self):
        return 0

    def scalars(self):
        return self

    def all(self):
        return self._items


def _make_client() -> TestClient:
    from dependencies.auth import get_current_user
    from core.database import get_db

    app = FastAPI()
    app.include_router(router)

    async def fake_get_current_user():
        return fake_user

    async def fake_get_db():
        yield FakeDB()

    app.dependency_overrides[get_current_user] = fake_get_current_user
    app.dependency_overrides[get_db] = fake_get_db

    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ensure_workspace_runtime_returns_preview_url(monkeypatch):
    async def _fake_ensure(*args, **kwargs):
        return _FakeSession()

    monkeypatch.setattr(
        "routers.workspace_runtime.ensure_runtime_for_project",
        _fake_ensure,
    )

    # Mock ProjectsService.get_by_id to simulate ownership check passing
    from services import projects as projects_module

    class _FakeProject:
        id = 42
        user_id = "user-1"

    async def _fake_get_by_id(self, obj_id, user_id=None):
        return _FakeProject()

    monkeypatch.setattr(projects_module.ProjectsService, "get_by_id", _fake_get_by_id)

    client = _make_client()
    response = client.post("/api/v1/workspace-runtime/projects/42/ensure")
    assert response.status_code == 200
    assert response.json()["preview_frontend_url"].endswith("/frontend/")
    assert response.json()["preview_backend_url"].endswith("/backend/")


def test_preview_proxy_requires_running_session():
    client = _make_client()
    response = client.get("/api/v1/workspace-runtime/projects/42/preview/")
    assert response.status_code == 404


def test_ensure_workspace_runtime_returns_preview_bundle(monkeypatch):
    async def _fake_ensure(*args, **kwargs):
        return _FakeSession()

    monkeypatch.setattr("routers.workspace_runtime.ensure_runtime_for_project", _fake_ensure)

    from services import projects as projects_module

    class _FakeProject:
        id = 42
        user_id = "user-1"

    async def _fake_get_by_id(self, obj_id, user_id=None):
        return _FakeProject()

    monkeypatch.setattr(projects_module.ProjectsService, "get_by_id", _fake_get_by_id)

    client = _make_client()
    response = client.post("/api/v1/workspace-runtime/projects/42/ensure")
    payload = response.json()

    assert response.status_code == 200
    assert payload["preview_session_key"] == "preview-session-123"
    assert payload["preview_frontend_url"] == "/preview/preview-session-123/frontend/"
    assert payload["preview_backend_url"] == "/preview/preview-session-123/backend/"
    assert payload["frontend_status"] == "running"
    assert payload["backend_status"] == "starting"


import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from routers.workspace_runtime import ensure_runtime_for_project
from services.workspace_runtime_sessions import WorkspaceRuntimeSessionsService


@pytest_asyncio.fixture
async def memory_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        import models  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as db:
            yield db
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_runtime_calls_start_preview_services(memory_db):
    """start_preview_services must be called so the Vite dev server starts."""
    start_preview_mock = AsyncMock(return_value=(0, "", ""))

    with (
        patch("routers.workspace_runtime.SandboxRuntimeService") as MockSandbox,
        patch("routers.workspace_runtime.ProjectWorkspaceService") as MockWorkspace,
        patch("routers.workspace_runtime.load_preview_contract", return_value=None),
    ):
        mock_sandbox = MockSandbox.return_value
        mock_sandbox.ensure_runtime = AsyncMock(return_value="atoms-user-1-42")
        mock_sandbox.get_runtime_ports = AsyncMock(
            return_value={"frontend_port": 32000, "backend_port": None, "preview_port": 32000}
        )
        mock_sandbox.start_preview_services = start_preview_mock
        mock_sandbox.wait_for_service = AsyncMock(return_value=True)

        mock_paths = MagicMock()
        mock_paths.host_root = Path("/tmp/fake")
        MockWorkspace.return_value.resolve_paths.return_value = mock_paths

        await ensure_runtime_for_project(memory_db, user_id="user-1", project_id=42)

    start_preview_mock.assert_called_once()
    call_kwargs = start_preview_mock.call_args
    env = call_kwargs.kwargs.get("env") or call_kwargs.args[1]
    assert "ATOMS_PREVIEW_FRONTEND_BASE" in env
    assert "/preview/" in env["ATOMS_PREVIEW_FRONTEND_BASE"]
    assert env["ATOMS_PREVIEW_FRONTEND_BASE"].endswith("/frontend/")


@pytest.mark.asyncio
async def test_ensure_runtime_embeds_preview_session_key_in_session(memory_db):
    """Session must be created with the preview_session_key so the gateway can look it up."""
    with (
        patch("routers.workspace_runtime.SandboxRuntimeService") as MockSandbox,
        patch("routers.workspace_runtime.ProjectWorkspaceService") as MockWorkspace,
        patch("routers.workspace_runtime.load_preview_contract", return_value=None),
    ):
        mock_sandbox = MockSandbox.return_value
        mock_sandbox.ensure_runtime = AsyncMock(return_value="atoms-user-1-42")
        mock_sandbox.get_runtime_ports = AsyncMock(
            return_value={"frontend_port": 32000, "backend_port": None, "preview_port": 32000}
        )
        mock_sandbox.start_preview_services = AsyncMock(return_value=(0, "", ""))
        mock_sandbox.wait_for_service = AsyncMock(return_value=True)

        mock_paths = MagicMock()
        mock_paths.host_root = Path("/tmp/fake")
        MockWorkspace.return_value.resolve_paths.return_value = mock_paths

        session = await ensure_runtime_for_project(memory_db, user_id="user-1", project_id=42)

    assert session.preview_session_key is not None
    assert len(session.preview_session_key) > 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeSession:
    status = "running"
    container_name = "atoms-user-1-42"
    frontend_port = 3000
    backend_port = 8000
    preview_session_key = "preview-session-123"
    preview_expires_at = None
    frontend_status = "running"
    backend_status = "starting"
