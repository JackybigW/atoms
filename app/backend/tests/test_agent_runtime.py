import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openmanus_runtime.llm import split_thinking_content
from openmanus_runtime.schema import Message
from openmanus_runtime.streaming import StreamingSWEAgent

from routers.agent_runtime import router
from schemas.auth import UserResponse


class FakeAgent:
    name = "swe"

    def __init__(self, *args, event_emitter=None, **kwargs):
        self._emit = event_emitter

    async def run(self, request: str):
        await self._emit({"type": "assistant", "agent": "swe", "content": f"Working on: {request}"})
        return "finished"

    @classmethod
    def build_for_workspace(cls, llm, event_emitter, file_operator, bash_session):
        return cls(event_emitter=event_emitter)


class FakeDB:
    """Minimal async DB session stub."""

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


def test_agent_run_sse_stream(monkeypatch):
    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    # Stub workspace / sandbox so no filesystem or docker calls happen
    monkeypatch.setattr(
        "routers.agent_runtime._get_workspace_service",
        lambda: _FakeWorkspaceService(),
    )
    monkeypatch.setattr(
        "routers.agent_runtime._get_sandbox_service",
        lambda: _FakeSandboxService(),
    )

    fake_user = UserResponse(id="user-1", email="test@example.com", name="Test", role="user")

    app = FastAPI()
    app.include_router(router)

    # Override auth and DB dependencies
    from dependencies.auth import get_current_user
    from core.database import get_db

    async def fake_get_current_user():
        return fake_user

    async def fake_get_db():
        yield FakeDB()

    app.dependency_overrides[get_current_user] = fake_get_current_user
    app.dependency_overrides[get_db] = fake_get_db

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/run",
            json={"prompt": "build a todo app", "project_id": 1},
        )

    assert response.status_code == 200
    body = response.text
    assert 'event: session' in body
    assert 'event: assistant' in body
    assert 'event: done' in body

    done_line = next(line for line in body.splitlines() if line.startswith("data: ") and '"type": "done"' in line)
    done_payload = json.loads(done_line.removeprefix("data: "))
    assert done_payload["status"] == "success"


class _FakeSandboxService:
    def __init__(self, dev_success: bool = True, wait_success: bool = True):
        self.dev_success = dev_success
        self.wait_success = wait_success
        self.dev_calls: list[str] = []
        self.wait_calls: list[tuple[str, int]] = []

    async def ensure_runtime(self, user_id, project_id, host_root):
        return f"atoms-{user_id}-{project_id}"

    async def exec(self, container_name, command):
        return 0, "", ""

    async def get_runtime_ports(self, container_name):
        return {"frontend_port": 55555, "backend_port": 55556, "preview_port": 55555}

    async def start_dev_server(self, container_name):
        self.dev_calls.append(container_name)
        if self.dev_success:
            return 0, "", ""
        return 2, "", "start-dev: ATOMS_PROJECT_ID env var is required"

    async def wait_for_service(self, container_name, port, timeout_seconds=60.0, poll_interval_seconds=1.0):
        self.wait_calls.append((container_name, port))
        return self.wait_success


class _FakeWorkspacePaths:
    from pathlib import Path as _Path

    host_root = _Path("/tmp/fake_workspace/user-1/1")
    container_root = _Path("/workspace")


class _FakeWorkspaceService:
    def resolve_paths(self, user_id, project_id):
        return _FakeWorkspacePaths()

    def materialize_files(self, host_root, project_files):
        pass

    def snapshot_files(self, host_root):
        return {}


def test_agent_run_emits_workspace_sync(monkeypatch):
    class FakeAgentWithSync:
        name = "swe"

        def __init__(self, *args, event_emitter=None, **kwargs):
            self._emit = event_emitter

        async def run(self, request: str):
            await self._emit({"type": "assistant", "agent": "swe", "content": "Working"})
            await self._emit({"type": "workspace_sync", "changed_files": ["src/App.tsx"]})
            await self._emit(
                {
                    "type": "preview_ready",
                    "preview_url": "/api/v1/workspace-runtime/projects/42/preview/",
                }
            )
            return "finished"

        @classmethod
        def build_for_workspace(cls, llm, event_emitter, file_operator, bash_session):
            return cls(event_emitter=event_emitter)

    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgentWithSync)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr(
        "routers.agent_runtime._get_workspace_service",
        lambda: _FakeWorkspaceService(),
    )
    monkeypatch.setattr(
        "routers.agent_runtime._get_sandbox_service",
        lambda: _FakeSandboxService(),
    )

    fake_user = UserResponse(id="user-1", email="test@example.com", name="Test", role="user")

    app = FastAPI()
    app.include_router(router)

    from dependencies.auth import get_current_user
    from core.database import get_db

    async def fake_get_current_user():
        return fake_user

    async def fake_get_db():
        yield FakeDB()

    app.dependency_overrides[get_current_user] = fake_get_current_user
    app.dependency_overrides[get_db] = fake_get_db

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/run",
            json={"prompt": "build a todo app", "project_id": 42},
        )

    assert response.status_code == 200
    body = response.text

    # Verify workspace_sync event is present with changed_files
    assert "event: workspace_sync" in body
    ws_line = next(
        line
        for line in body.splitlines()
        if line.startswith("data: ") and '"type": "workspace_sync"' in line
    )
    ws_payload = json.loads(ws_line.removeprefix("data: "))
    assert "changed_files" in ws_payload
    assert ws_payload["changed_files"] == ["src/App.tsx"]

    # Verify preview_ready event is present with preview_url
    assert "event: preview_ready" in body
    pr_line = next(
        line
        for line in body.splitlines()
        if line.startswith("data: ") and '"type": "preview_ready"' in line
    )
    pr_payload = json.loads(pr_line.removeprefix("data: "))
    assert "preview_url" in pr_payload
    assert pr_payload["preview_url"] == "/api/v1/workspace-runtime/projects/42/preview/"


def test_agent_run_emits_preview_ready_after_agent_completion(monkeypatch):
    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr(
        "routers.agent_runtime._get_workspace_service",
        lambda: _FakeWorkspaceService(),
    )
    session_creates = []

    async def fake_create(self, data):
        session_creates.append(data)
        return data

    monkeypatch.setattr("routers.agent_runtime.WorkspaceRuntimeSessionsService.create", fake_create)
    fake_sandbox = _FakeSandboxService()
    monkeypatch.setattr(
        "routers.agent_runtime._get_sandbox_service",
        lambda: fake_sandbox,
    )

    fake_user = UserResponse(id="user-1", email="test@example.com", name="Test", role="user")

    app = FastAPI()
    app.include_router(router)

    from dependencies.auth import get_current_user
    from core.database import get_db

    async def fake_get_current_user():
        return fake_user

    async def fake_get_db():
        yield FakeDB()

    app.dependency_overrides[get_current_user] = fake_get_current_user
    app.dependency_overrides[get_db] = fake_get_db

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/run",
            json={"prompt": "build a todo app", "project_id": 7},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: preview_ready" in body

    preview_line = next(
        line
        for line in body.splitlines()
        if line.startswith("data: ") and '"type": "preview_ready"' in line
    )
    preview_payload = json.loads(preview_line.removeprefix("data: "))
    assert preview_payload["type"] == "preview_ready"
    assert preview_payload["preview_url"] == "/api/v1/workspace-runtime/projects/7/preview/"
    assert fake_sandbox.dev_calls == ["atoms-user-1-7"]
    assert fake_sandbox.wait_calls == [("atoms-user-1-7", 3000)]
    assert session_creates == [
        {
            "user_id": "user-1",
            "project_id": 7,
            "container_name": "atoms-user-1-7",
            "status": "running",
            "preview_port": 55555,
            "frontend_port": 55555,
            "backend_port": 55556,
        }
    ]


def test_agent_run_emits_preview_failed_when_dev_server_times_out(monkeypatch):
    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr(
        "routers.agent_runtime._get_workspace_service",
        lambda: _FakeWorkspaceService(),
    )
    session_creates = []

    async def fake_create(self, data):
        session_creates.append(data)
        return data

    monkeypatch.setattr("routers.agent_runtime.WorkspaceRuntimeSessionsService.create", fake_create)
    fake_sandbox = _FakeSandboxService(dev_success=True, wait_success=False)
    monkeypatch.setattr(
        "routers.agent_runtime._get_sandbox_service",
        lambda: fake_sandbox,
    )

    fake_user = UserResponse(id="user-1", email="test@example.com", name="Test", role="user")

    app = FastAPI()
    app.include_router(router)

    from dependencies.auth import get_current_user
    from core.database import get_db

    async def fake_get_current_user():
        return fake_user

    async def fake_get_db():
        yield FakeDB()

    app.dependency_overrides[get_current_user] = fake_get_current_user
    app.dependency_overrides[get_db] = fake_get_db

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/run",
            json={"prompt": "build a todo app", "project_id": 7},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: preview_failed" in body
    assert "event: done" in body

    preview_line = next(
        line
        for line in body.splitlines()
        if line.startswith("data: ") and '"type": "preview_failed"' in line
    )
    preview_payload = json.loads(preview_line.removeprefix("data: "))
    assert preview_payload["type"] == "preview_failed"
    assert preview_payload["reason"] == "timeout"
    assert session_creates == [
        {
            "user_id": "user-1",
            "project_id": 7,
            "container_name": "atoms-user-1-7",
            "status": "starting",
            "preview_port": 55555,
            "frontend_port": 55555,
            "backend_port": 55556,
        }
    ]


def test_split_thinking_content_extracts_visible_content():
    thinking, content = split_thinking_content("<think>\ninspect files\n</think>\n\nFinal answer")

    assert thinking == "inspect files"
    assert content == "Final answer"


@pytest.mark.asyncio
async def test_streaming_agent_emits_thinking():
    events = []

    class FakeStreamingAgent(StreamingSWEAgent):
        async def think(self):
            self.memory.add_message(
                Message.assistant_message("Visible output", thinking="Hidden chain of thought")
            )
            return False

    async def emit(event):
        events.append(event)

    agent = FakeStreamingAgent(event_emitter=emit)
    result = await agent.step()

    assert result == "Thinking complete - no action needed"
    assert events == [
        {
            "type": "assistant",
            "content": "Visible output",
            "thinking": "Hidden chain of thought",
            "agent": "swe",
        }
    ]
