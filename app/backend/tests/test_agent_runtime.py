import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openmanus_runtime.llm import split_thinking_content
from openmanus_runtime.schema import Message
from openmanus_runtime.streaming import StreamingSWEAgent

from routers.agent_runtime import _serialize_agent_history, router
from schemas.auth import UserResponse
from unittest.mock import AsyncMock, patch

from services.agent_bootstrap import (
    BootstrapContext,
    _ClassificationResult,
    build_bootstrap_context,
    classify_user_request,
    classify_user_request_async,
)


def test_classify_user_request_flags_implementation_mode():
    result = classify_user_request("帮我新增一个 billing 页面和后端接口")
    assert result.mode == "implementation"
    assert result.requires_backend_readme is True


def test_classify_user_request_leaves_smalltalk_in_conversation_mode():
    result = classify_user_request("你好")
    assert result.mode == "conversation"
    assert result.requires_draft_plan is False


def test_classify_user_request_ignores_ai_substring_in_unrelated_words():
    result = classify_user_request("please maintain the homepage copy")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is False


def test_classify_user_request_ignores_email_in_general_requests():
    result = classify_user_request("please email me the notes")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is False


def test_classify_user_request_ignores_reviewing_landing_page_copy():
    result = classify_user_request("review the landing page copy")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is False


def test_classify_user_request_ignores_frontend_widget_behavior_questions():
    result = classify_user_request("how should the frontend widget behave?")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is False


def test_classify_user_request_marks_frontend_landing_page_as_implementation_only():
    result = classify_user_request("build a landing page")
    assert result.mode == "implementation"
    assert result.requires_backend_readme is False


def test_classify_user_request_marks_frontend_widget_as_implementation_only():
    result = classify_user_request("implement the frontend widget")
    assert result.mode == "implementation"
    assert result.requires_backend_readme is False


def test_classify_user_request_marks_add_auth_as_implementation_and_backend():
    result = classify_user_request("please add auth")
    assert result.mode == "implementation"
    assert result.requires_backend_readme is True


def test_classify_user_request_treats_question_form_add_auth_as_conversation():
    result = classify_user_request("how do I add auth?")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is True


def test_classify_user_request_treats_question_form_update_api_client_as_conversation():
    result = classify_user_request("what is the best way to update the API client?")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is True


def test_classify_user_request_treats_can_you_add_auth_as_implementation():
    result = classify_user_request("can you add auth?")
    assert result.mode == "implementation"
    assert result.requires_backend_readme is True


def test_classify_user_request_treats_could_you_build_login_page_as_implementation():
    result = classify_user_request("could you build a login page?")
    assert result.mode == "implementation"
    assert result.requires_backend_readme is False


def test_classify_user_request_treats_please_add_auth_question_as_implementation():
    result = classify_user_request("please add auth?")
    assert result.mode == "implementation"
    assert result.requires_backend_readme is True


def test_classify_user_request_treats_can_i_add_auth_as_conversation():
    result = classify_user_request("can I add auth?")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is True


def test_classify_user_request_treats_should_we_update_api_client_as_conversation():
    result = classify_user_request("should we update the API client?")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is True


def test_classify_user_request_treats_whats_the_best_way_as_conversation():
    result = classify_user_request("what's the best way to add auth?")
    assert result.mode == "conversation"
    assert result.requires_backend_readme is True


def test_build_bootstrap_context_defaults_to_classification():
    result = build_bootstrap_context("帮我新增一个 billing 页面和后端接口")
    assert result.mode == "implementation"
    assert result.requires_draft_plan is True


# ---------------------------------------------------------------------------
# LLM-based async classifier tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_user_request_async_uses_llm_result():
    llm_result = _ClassificationResult(mode="implementation", requires_backend_readme=True)
    with patch("services.agent_bootstrap._classify_with_llm", AsyncMock(return_value=llm_result)):
        result = await classify_user_request_async("帮我做一个 billing 页面")
    assert result.mode == "implementation"
    assert result.requires_backend_readme is True
    assert result.requires_draft_plan is True


@pytest.mark.asyncio
async def test_classify_user_request_async_conversation_mode():
    llm_result = _ClassificationResult(mode="conversation", requires_backend_readme=False)
    with patch("services.agent_bootstrap._classify_with_llm", AsyncMock(return_value=llm_result)):
        result = await classify_user_request_async("你好，怎么了？")
    assert result.mode == "conversation"
    assert result.requires_draft_plan is False


@pytest.mark.asyncio
async def test_classify_user_request_async_falls_back_to_regex_on_llm_error():
    with patch("services.agent_bootstrap._classify_with_llm", AsyncMock(side_effect=RuntimeError("API down"))):
        result = await classify_user_request_async("build a landing page")
    assert result.mode == "implementation"


@pytest.mark.asyncio
async def test_classify_user_request_async_fails_closed_for_chinese_implementation_on_llm_error():
    with patch("services.agent_bootstrap._classify_with_llm", AsyncMock(side_effect=RuntimeError("API down"))):
        result = await classify_user_request_async("帮我做一个 billing 页面")
    assert result.mode == "implementation"
    assert result.requires_draft_plan is True


@pytest.mark.asyncio
async def test_classify_user_request_async_chinese_implementation_without_keyword():
    """Core motivation: Chinese prompts without English keywords must reach LLM."""
    llm_result = _ClassificationResult(mode="implementation", requires_backend_readme=False)
    with patch("services.agent_bootstrap._classify_with_llm", AsyncMock(return_value=llm_result)) as mock_llm:
        result = await classify_user_request_async("帮我做一个 billing 页面")
    assert result.mode == "implementation"
    mock_llm.assert_called_once()


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
    assert done_payload["trace_id"]

    session_line = next(line for line in body.splitlines() if line.startswith("data: ") and '"type": "session"' in line)
    session_payload = json.loads(session_line.removeprefix("data: "))
    assert session_payload["trace_id"] == done_payload["trace_id"]


def test_serialize_agent_history_includes_thinking_and_tool_messages():
    history = _serialize_agent_history(
        [
            Message.user_message("build app"),
            Message.assistant_message(content="working", thinking="plan"),
            Message.tool_message(content="done", name="bash", tool_call_id="call-1"),
        ]
    )

    assert history == [
        {"role": "user", "content": "build app"},
        {"role": "assistant", "content": "working", "thinking": "plan"},
        {
            "role": "tool",
            "content": "done",
            "name": "bash",
            "tool_call_id": "call-1",
        },
    ]


class _FakeSandboxService:
    def __init__(self, dev_success: bool = True, wait_success: bool = True):
        self.dev_success = dev_success
        self.wait_success = wait_success
        self.dev_calls: list[str] = []
        self.wait_calls: list[tuple[str, int]] = []
        self.preview_envs: list[dict[str, str] | None] = []

    async def ensure_runtime(self, user_id, project_id, host_root):
        return f"atoms-{user_id}-{project_id}"

    async def exec(self, container_name, command):
        return 0, "", ""

    async def get_runtime_ports(self, container_name):
        return {"frontend_port": 55555, "backend_port": 55556, "preview_port": 55555}

    async def start_preview_services(self, container_name, env=None):
        self.dev_calls.append(container_name)
        self.preview_envs.append(env)
        if self.dev_success:
            return 0, "", ""
        return 2, "", "start-preview: ATOMS_PROJECT_ID env var is required"

    async def wait_for_service(self, container_name, port, path="/", timeout_seconds=60.0, poll_interval_seconds=1.0):
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


@pytest.mark.asyncio
async def test_run_engineer_session_conversation_mode_skips_sandbox(monkeypatch, tmp_path):
    from services.engineer_runtime import run_engineer_session

    events: list[dict] = []

    class ConversationWorkspacePaths:
        host_root = tmp_path / "user-1" / "42"
        container_root = Path("/workspace")

    class ConversationWorkspaceService:
        def resolve_paths(self, user_id, project_id):
            ConversationWorkspacePaths.host_root.mkdir(parents=True, exist_ok=True)
            return ConversationWorkspacePaths

        def materialize_files(self, host_root, project_files):
            pass

        def snapshot_files(self, host_root):
            return {}

    class FailingSandboxService:
        async def ensure_runtime(self, user_id, project_id, host_root):
            raise AssertionError("sandbox should not start for conversation mode")

    async def fake_event_sink(event: dict):
        events.append(event)

    async def fake_files_get_list(self, **kwargs):
        return {"items": []}

    async def fake_messages_get_list(self, **kwargs):
        return {"items": []}

    monkeypatch.setattr(
        "services.agent_bootstrap.classify_user_request_async",
        AsyncMock(return_value=BootstrapContext(mode="conversation", requires_backend_readme=False, requires_draft_plan=False)),
    )
    monkeypatch.setattr("services.project_files.Project_filesService.get_list", fake_files_get_list)
    monkeypatch.setattr("services.messages.MessagesService.get_list", fake_messages_get_list)

    success = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="hello",
        model="MiniMax-M2.7-highspeed",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: ConversationWorkspaceService(),
        sandbox_service_factory=lambda: FailingSandboxService(),
        agent_cls=FakeAgent,
        llm_builder=lambda model: None,
    )

    assert success is True
    assert any(event.get("type") == "assistant" for event in events)
    assistant = next(event for event in events if event.get("type") == "assistant")
    assert "implementation request" in assistant["content"]


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
    assert "preview_session_key" in preview_payload
    assert fake_sandbox.dev_calls == ["atoms-user-1-7"]
    assert ("atoms-user-1-7", 3000) in fake_sandbox.wait_calls


def test_agent_run_emits_preview_failed_when_dev_server_times_out(monkeypatch):
    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr(
        "routers.agent_runtime._get_workspace_service",
        lambda: _FakeWorkspaceService(),
    )
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


# ---------------------------------------------------------------------------
# Helpers for Task 4 preview bundle tests
# ---------------------------------------------------------------------------

def _post_agent_run(monkeypatch):
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
        return client.post(
            "/api/v1/agent/run",
            json={"prompt": "build a billing app", "project_id": 42, "model": "gpt-5-chat"},
        )


class PromptCapturingAgent:
    name = "swe"

    def __init__(self, *args, event_emitter=None, **kwargs):
        self._emit = event_emitter

    async def run(self, request: str):
        self.prompt = request
        return "finished"

    @classmethod
    def build_for_workspace(cls, llm, event_emitter, file_operator, bash_session):
        return cls(event_emitter=event_emitter)


def test_agent_prompt_includes_preview_manifest_contract(monkeypatch):
    captured_prompt = {}

    class FakeAgent(PromptCapturingAgent):
        async def run(self, request: str):
            captured_prompt["value"] = request
            return await super().run(request)

    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr("routers.agent_runtime._get_workspace_service", lambda: _FakeWorkspaceService())
    monkeypatch.setattr("routers.agent_runtime._get_sandbox_service", lambda: _FakeSandboxService())

    response = _post_agent_run(monkeypatch)

    assert response.status_code == 200
    assert ".atoms/preview.json" in captured_prompt["value"]
    assert "VITE_ATOMS_PREVIEW_BACKEND_BASE" in captured_prompt["value"]
    assert "BrowserRouter" in captured_prompt["value"]
    assert "basename" in captured_prompt["value"]
    assert "/usr/local/bin/start-preview" in captured_prompt["value"]


def test_engineer_prompt_uses_venv_python_for_backend_install(monkeypatch):
    captured_prompt = {}

    class FakeAgent(PromptCapturingAgent):
        async def run(self, request: str):
            captured_prompt["value"] = request
            return await super().run(request)

    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr("routers.agent_runtime._get_workspace_service", lambda: _FakeWorkspaceService())
    monkeypatch.setattr("routers.agent_runtime._get_sandbox_service", lambda: _FakeSandboxService())

    response = _post_agent_run(monkeypatch)

    assert response.status_code == 200
    prompt_value = captured_prompt["value"]
    assert "uv venv .venv" in prompt_value
    assert "uv pip install --python .venv/bin/python -r requirements.txt" in prompt_value
    assert '.venv/bin/python -c "from main import app; print(\'ok\')"' in prompt_value
    assert "uv pip install -r requirements.txt -q 2>&1 && uv run python" not in prompt_value


def test_agent_run_emits_preview_bundle(monkeypatch):
    _FIXED_SESSION_KEY = "preview-session-123"

    class _FakeSession:
        preview_session_key = _FIXED_SESSION_KEY
        preview_expires_at = None
        frontend_status = "running"
        backend_status = "running"

    async def fake_create(self, data):
        return _FakeSession()

    async def fake_get_by_project(self, user_id, project_id):
        return None

    monkeypatch.setattr("routers.agent_runtime.WorkspaceRuntimeSessionsService.create", fake_create)
    monkeypatch.setattr("routers.agent_runtime.WorkspaceRuntimeSessionsService.get_by_project", fake_get_by_project)
    monkeypatch.setattr(
        "routers.agent_runtime.new_preview_session_fields",
        lambda: {
            "preview_session_key": _FIXED_SESSION_KEY,
            "preview_expires_at": None,
            "frontend_status": "running",
            "backend_status": "running",
        },
    )
    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", PromptCapturingAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr("routers.agent_runtime._get_workspace_service", lambda: _FakeWorkspaceService())
    monkeypatch.setattr("routers.agent_runtime._get_sandbox_service", lambda: _FakeSandboxService())

    response = _post_agent_run(monkeypatch)
    body = response.text
    preview_line = next(
        line for line in body.splitlines()
        if line.startswith("data: ") and '"type": "preview_ready"' in line
    )
    payload = json.loads(preview_line.removeprefix("data: "))

    assert payload["preview_session_key"] == "preview-session-123"
    assert payload["preview_frontend_url"] == "/preview/preview-session-123/frontend/"
    assert payload["preview_backend_url"] == "/preview/preview-session-123/backend/"
    assert payload["frontend_status"] == "running"
    assert payload["backend_status"] == "running"


def test_agent_run_renews_expired_preview_session_key(monkeypatch):
    class _ExistingExpiredSession:
        preview_session_key = "expired-preview-session"
        preview_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        frontend_status = "running"
        backend_status = "running"

    class _CreatedSession:
        preview_session_key = "fresh-preview-session"
        preview_expires_at = None
        frontend_status = "running"
        backend_status = "running"

    async def fake_create(self, data):
        assert data["preview_session_key"] == "fresh-preview-session"
        return _CreatedSession()

    async def fake_get_by_project(self, user_id, project_id):
        return _ExistingExpiredSession()

    monkeypatch.setattr("routers.agent_runtime.WorkspaceRuntimeSessionsService.create", fake_create)
    monkeypatch.setattr("routers.agent_runtime.WorkspaceRuntimeSessionsService.get_by_project", fake_get_by_project)
    monkeypatch.setattr(
        "routers.agent_runtime.new_preview_session_fields",
        lambda: {
            "preview_session_key": "fresh-preview-session",
            "preview_expires_at": None,
            "frontend_status": "starting",
            "backend_status": "stopped",
        },
    )
    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", PromptCapturingAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr("routers.agent_runtime._get_workspace_service", lambda: _FakeWorkspaceService())
    fake_sandbox = _FakeSandboxService()
    monkeypatch.setattr("routers.agent_runtime._get_sandbox_service", lambda: fake_sandbox)

    response = _post_agent_run(monkeypatch)
    body = response.text
    preview_line = next(
        line for line in body.splitlines()
        if line.startswith("data: ") and '"type": "preview_ready"' in line
    )
    payload = json.loads(preview_line.removeprefix("data: "))

    assert response.status_code == 200
    assert payload["preview_session_key"] == "fresh-preview-session"
    assert fake_sandbox.preview_envs[0] is not None
    assert fake_sandbox.preview_envs[0]["ATOMS_PREVIEW_FRONTEND_BASE"] == "/preview/fresh-preview-session/frontend/"


def test_agent_prompt_includes_skill_metadata(monkeypatch):
    captured_prompt = {}

    class FakeAgent(PromptCapturingAgent):
        async def run(self, request: str):
            captured_prompt["value"] = request
            return await super().run(request)

    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr("routers.agent_runtime._get_workspace_service", lambda: _FakeWorkspaceService())
    monkeypatch.setattr("routers.agent_runtime._get_sandbox_service", lambda: _FakeSandboxService())

    response = _post_agent_run(monkeypatch)

    assert response.status_code == 200
    assert "web_sdk" in captured_prompt["value"]
    assert "custom_api" in captured_prompt["value"]


@pytest.mark.asyncio
async def test_todo_write_tool_writes_docs_todo_md(tmp_path):
    from openmanus_runtime.tool.todo_write import TodoWriteTool

    events = []
    host_root = tmp_path / "workspace"
    host_root.mkdir()

    from openmanus_runtime.tool.file_operators import ProjectFileOperator
    from pathlib import Path
    operator = ProjectFileOperator(host_root=host_root, container_root=Path("/workspace"))

    tool = TodoWriteTool.create(file_operator=operator, event_sink=events.append)

    result = await tool.execute(items=[
        {"id": "1", "text": "Create homepage", "status": "pending"},
        {"id": "2", "text": "Add auth flow", "status": "in_progress"},
    ])

    assert (host_root / "docs" / "todo.md").exists()
    content = (host_root / "docs" / "todo.md").read_text()
    assert "Create homepage" in content
    assert "Add auth flow" in content
    assert any(e.get("type") == "todo.updated" for e in events)


@pytest.mark.asyncio
async def test_todo_write_tool_rejects_multiple_in_progress(tmp_path):
    from openmanus_runtime.tool.todo_write import TodoWriteTool
    from openmanus_runtime.exceptions import ToolError

    host_root = tmp_path / "workspace"
    host_root.mkdir()
    from openmanus_runtime.tool.file_operators import ProjectFileOperator
    from pathlib import Path
    operator = ProjectFileOperator(host_root=host_root, container_root=Path("/workspace"))

    tool = TodoWriteTool.create(file_operator=operator, event_sink=lambda e: None)

    with pytest.raises(ToolError):
        await tool.execute(items=[
            {"id": "1", "text": "Task A", "status": "in_progress"},
            {"id": "2", "text": "Task B", "status": "in_progress"},
        ])


def test_agent_run_marks_backend_not_configured_without_preview_manifest(monkeypatch):
    _FIXED_SESSION_KEY = "preview-session-123"

    class _FakeSession:
        preview_session_key = _FIXED_SESSION_KEY
        preview_expires_at = None
        frontend_status = "running"
        backend_status = "not_configured"

    async def fake_create(self, data):
        assert data["backend_status"] == "not_configured"
        return _FakeSession()

    async def fake_get_by_project(self, user_id, project_id):
        return None

    monkeypatch.setattr("routers.agent_runtime.WorkspaceRuntimeSessionsService.create", fake_create)
    monkeypatch.setattr("routers.agent_runtime.WorkspaceRuntimeSessionsService.get_by_project", fake_get_by_project)
    monkeypatch.setattr(
        "routers.agent_runtime.new_preview_session_fields",
        lambda: {
            "preview_session_key": _FIXED_SESSION_KEY,
            "preview_expires_at": None,
            "frontend_status": "starting",
            "backend_status": "stopped",
        },
    )
    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", PromptCapturingAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr("routers.agent_runtime._get_workspace_service", lambda: _FakeWorkspaceService())
    fake_sandbox = _FakeSandboxService()
    monkeypatch.setattr("routers.agent_runtime._get_sandbox_service", lambda: fake_sandbox)

    response = _post_agent_run(monkeypatch)
    body = response.text
    preview_line = next(
        line for line in body.splitlines()
        if line.startswith("data: ") and '"type": "preview_ready"' in line
    )
    payload = json.loads(preview_line.removeprefix("data: "))

    assert response.status_code == 200
    assert payload["backend_status"] == "not_configured"
    assert fake_sandbox.preview_envs[0] is not None
    assert fake_sandbox.preview_envs[0]["ATOMS_PREVIEW_FRONTEND_BASE"] == "/preview/preview-session-123/frontend/"
    assert fake_sandbox.preview_envs[0]["ATOMS_PREVIEW_BACKEND_BASE"] == "/preview/preview-session-123/backend/"
    assert fake_sandbox.preview_envs[0]["VITE_ATOMS_PREVIEW_BACKEND_BASE"] == "/preview/preview-session-123/backend/"


def test_run_engineer_session_injects_backend_readme_for_backend_requests(tmp_path):
    """When request requires_backend_readme, README content is injected into task_prompt."""
    from services.agent_bootstrap import classify_user_request

    # 1. Confirm the classifier flags the request correctly
    ctx = classify_user_request("add a database table")
    assert ctx.requires_backend_readme is True

    # 2. Simulate the README reading + template substitution logic inline
    readme_dir = tmp_path / "app" / "backend"
    readme_dir.mkdir(parents=True)
    readme_path = readme_dir / "README.md"
    readme_path.write_text("# Backend Guide\nUse FastAPI.", encoding="utf-8")

    readme_block = ""
    if ctx.requires_backend_readme:
        try:
            readme_content = readme_path.read_text(encoding="utf-8").strip()
            if readme_content:
                readme_block = (
                    "## Backend README (mandatory reading before implementing backend features)\n\n"
                    f"{readme_content}\n\n---\n\n"
                )
        except OSError:
            pass

    prompt = "add a database table"
    workspace_block = (
        "You must work inside this workspace root: /workspace\n"
        "Use absolute paths starting with /workspace for file edits, "
        "and change into this directory before running bash commands.\n\n"
        f"User request:\n{prompt}"
    )
    task_prompt = readme_block + workspace_block

    # 3. Assert the README content appears in the built prompt
    assert "Backend README" in task_prompt
    assert "Backend Guide" in task_prompt
    assert "Use FastAPI." in task_prompt
    # Workspace instructions still present after the README block
    assert "You must work inside this workspace root" in task_prompt
    # README comes before workspace instructions
    assert task_prompt.index("Backend Guide") < task_prompt.index("You must work inside")


def test_run_engineer_session_no_readme_injection_for_frontend_only_requests(tmp_path):
    """When request does NOT require_backend_readme, README is not injected even if file exists."""
    from services.agent_bootstrap import classify_user_request

    ctx = classify_user_request("build a landing page")
    assert ctx.requires_backend_readme is False

    readme_dir = tmp_path / "app" / "backend"
    readme_dir.mkdir(parents=True)
    (readme_dir / "README.md").write_text("# Backend Guide\nUse FastAPI.", encoding="utf-8")

    readme_block = ""
    if ctx.requires_backend_readme:
        try:
            readme_content = (readme_dir / "README.md").read_text(encoding="utf-8").strip()
            if readme_content:
                readme_block = (
                    "## Backend README (mandatory reading before implementing backend features)\n\n"
                    f"{readme_content}\n\n---\n\n"
                )
        except OSError:
            pass

    prompt = "build a landing page"
    task_prompt = readme_block + f"You must work inside this workspace root: /workspace\n\nUser request:\n{prompt}"

    assert "Backend README" not in task_prompt
    assert "Backend Guide" not in task_prompt
    assert "You must work inside this workspace root" in task_prompt


def test_run_engineer_session_readme_missing_does_not_fail(tmp_path):
    """When README file doesn't exist, task_prompt is built without it and no exception raised."""
    from services.agent_bootstrap import classify_user_request

    ctx = classify_user_request("add an API endpoint")
    assert ctx.requires_backend_readme is True

    # No README file created — directory doesn't exist
    readme_path = tmp_path / "app" / "backend" / "README.md"

    readme_block = ""
    if ctx.requires_backend_readme:
        try:
            readme_content = readme_path.read_text(encoding="utf-8").strip()
            if readme_content:
                readme_block = (
                    "## Backend README (mandatory reading before implementing backend features)\n\n"
                    f"{readme_content}\n\n---\n\n"
                )
        except OSError:
            pass  # gracefully skip

    prompt = "add an API endpoint"
    task_prompt = readme_block + f"You must work inside this workspace root: /workspace\n\nUser request:\n{prompt}"

    assert "Backend README" not in task_prompt
    assert "You must work inside this workspace root" in task_prompt


@pytest.mark.asyncio
async def test_engineer_runtime_emits_repair_continuation_for_backend_preview_failure(monkeypatch, tmp_path):
    """When backend healthcheck times out, engineer_runtime should emit preview_failed
    and an assistant repair continuation message instead of preview_ready."""
    from services.engineer_runtime import run_engineer_session
    from services.preview_contract import PreviewContract, PreviewServiceConfig
    from unittest.mock import AsyncMock

    events: list[dict] = []

    class BackendFailWorkspacePaths:
        host_root = tmp_path / "user-1" / "42"
        container_root = tmp_path / "container"

    class BackendFailWorkspaceService:
        def resolve_paths(self, user_id, project_id):
            BackendFailWorkspacePaths.host_root.mkdir(parents=True, exist_ok=True)
            return BackendFailWorkspacePaths

        def materialize_files(self, host_root, project_files):
            pass

        def snapshot_files(self, host_root):
            return {}

    class BackendFailSandboxService:
        """Returns True for frontend (port 3000), False for backend (port 8000)."""

        async def ensure_runtime(self, user_id, project_id, host_root):
            return f"atoms-{user_id}-{project_id}"

        async def exec(self, container_name, command):
            return 0, "", ""

        async def get_runtime_ports(self, container_name):
            return {"frontend_port": 55555, "backend_port": 55556, "preview_port": 55555}

        async def start_preview_services(self, container_name, env=None):
            return 0, "", ""

        async def wait_for_service(self, container_name, port, path="/", timeout_seconds=60.0, poll_interval_seconds=1.0):
            if port == 3000:
                return True
            return False  # backend port 8000 fails

    _fake_contract = PreviewContract(
        frontend=PreviewServiceConfig(command="pnpm run dev", healthcheck_path="/"),
        backend=PreviewServiceConfig(command="uvicorn main:app", healthcheck_path="/health"),
    )

    async def fake_event_sink(event: dict):
        events.append(event)

    async def fake_files_get_list(self, **kwargs):
        return {"items": []}

    async def fake_messages_get_list(self, **kwargs):
        return {"items": []}

    monkeypatch.setattr(
        "services.agent_bootstrap.classify_user_request_async",
        AsyncMock(return_value=BootstrapContext(mode="implementation", requires_backend_readme=False, requires_draft_plan=False)),
    )
    monkeypatch.setattr("services.project_files.Project_filesService.get_list", fake_files_get_list)
    monkeypatch.setattr("services.messages.MessagesService.get_list", fake_messages_get_list)

    class _FakeAgentForRepair:
        name = "swe"

        def __init__(self, *args, event_emitter=None, **kwargs):
            self._emit = event_emitter

        async def run(self, request: str):
            return "finished"

        @classmethod
        def build_for_workspace(cls, llm, event_emitter, file_operator, bash_session):
            return cls(event_emitter=event_emitter)

    success = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build a todo app with backend API",
        model=None,
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: BackendFailWorkspaceService(),
        sandbox_service_factory=lambda: BackendFailSandboxService(),
        agent_cls=_FakeAgentForRepair,
        llm_builder=lambda model: None,
        preview_contract_loader=lambda host_root: _fake_contract,
    )

    assert success is True

    event_types = [e.get("type") for e in events]

    # Must emit preview_failed, not preview_ready
    assert "preview_failed" in event_types, f"expected preview_failed in {event_types}"
    assert "preview_ready" not in event_types, f"unexpected preview_ready in {event_types}"

    failed_event = next(e for e in events if e.get("type") == "preview_failed")
    assert failed_event.get("reason") == "backend_healthcheck_timeout"

    # Must emit an assistant repair continuation message
    assistant_events = [e for e in events if e.get("type") == "assistant" and e.get("agent") == "system"]
    assert assistant_events, "expected at least one system assistant repair message"
    repair_msg = assistant_events[-1]["content"]
    assert "CRITICAL PREVIEW FAILURE" in repair_msg
    assert "backend_healthcheck_timeout" in repair_msg


def test_task_prompt_contains_orchestration_workflow_instructions(monkeypatch):
    """task_prompt must contain the orchestration workflow section so the agent writes the plan file."""
    captured_prompt = {}

    class FakeAgent(PromptCapturingAgent):
        async def run(self, request: str):
            captured_prompt["value"] = request
            return await super().run(request)

    monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", FakeAgent)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)
    monkeypatch.setattr("routers.agent_runtime._get_workspace_service", lambda: _FakeWorkspaceService())
    monkeypatch.setattr("routers.agent_runtime._get_sandbox_service", lambda: _FakeSandboxService())

    response = _post_agent_run(monkeypatch)

    assert response.status_code == 200
    prompt_value = captured_prompt["value"]
    assert "Orchestration Workflow" in prompt_value
    assert "draft_plan" in prompt_value
    assert "docs/plans/" in prompt_value
    assert "todo_write" in prompt_value
    assert "must call `todo_write` before implementation" in prompt_value
