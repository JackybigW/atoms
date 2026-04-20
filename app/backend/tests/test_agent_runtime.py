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
    async def ensure_runtime(self, user_id, project_id, host_root):
        return None  # signal: no container available, use local bash

    async def exec(self, container_name, command):
        return 0, "", ""


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
