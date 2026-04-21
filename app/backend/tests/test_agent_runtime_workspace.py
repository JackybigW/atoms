"""Tests for project-scoped agent workspace integration."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.tool.bash import ContainerBashSession
from openmanus_runtime.tool.file_operators import ProjectFileOperator
from openmanus_runtime.tool.str_replace_editor import StrReplaceEditor
from routers.agent_runtime import router


# ---------------------------------------------------------------------------
# ProjectFileOperator tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_project_file_operator_maps_container_paths(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-123" / "42",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    (operator.host_root / "app" / "frontend" / "src").mkdir(parents=True)
    (operator.host_root / "app" / "frontend" / "src" / "App.tsx").write_text(
        "export default function App() {}", encoding="utf-8"
    )
    content = await operator.read_file("/workspace/app/frontend/src/App.tsx")
    assert content == "export default function App() {}"


@pytest.mark.asyncio
async def test_project_file_operator_write_maps_container_paths(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    await operator.write_file("/workspace/docs/hello.txt", "hello world")
    assert (operator.host_root / "docs" / "hello.txt").read_text(encoding="utf-8") == "hello world"


@pytest.mark.asyncio
async def test_project_file_operator_rejects_protected_backend_paths(tmp_path):
    operator = ProjectFileOperator(host_root=tmp_path, container_root=Path("/workspace"))
    with pytest.raises(ToolError):
        await operator.write_file("/workspace/app/backend/core/config.py", "bad")


@pytest.mark.asyncio
async def test_project_file_operator_allows_reads_for_protected_backend_paths(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    protected_file = operator.host_root / "app" / "backend" / "core" / "config.py"
    protected_file.parent.mkdir(parents=True, exist_ok=True)
    protected_file.write_text("safe read", encoding="utf-8")

    content = await operator.read_file("/workspace/app/backend/core/config.py")

    assert content == "safe read"


@pytest.mark.asyncio
async def test_project_file_operator_allows_docs_and_frontend_paths(tmp_path):
    operator = ProjectFileOperator(host_root=tmp_path, container_root=Path("/workspace"))
    await operator.write_file("/workspace/docs/todo.md", "# Todo")
    await operator.write_file(
        "/workspace/app/frontend/src/App.tsx",
        "export default function App() { return null }",
    )


@pytest.mark.asyncio
async def test_str_replace_editor_rejects_protected_backend_paths(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    editor = StrReplaceEditor.with_operator(operator)

    with pytest.raises(ToolError):
        await editor.execute(
            command="create",
            path="/workspace/app/backend/core/config.py",
            file_text="bad",
        )


@pytest.mark.asyncio
async def test_str_replace_editor_rejects_str_replace_on_protected_backend_paths(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    editor = StrReplaceEditor.with_operator(operator)

    with pytest.raises(ToolError):
        await editor.execute(
            command="str_replace",
            path="/workspace/app/backend/models/schema.py",
            old_str="old",
            new_str="new",
        )


@pytest.mark.asyncio
async def test_str_replace_editor_rejects_insert_on_non_allowlisted_path(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    editor = StrReplaceEditor.with_operator(operator)

    with pytest.raises(ToolError):
        await editor.execute(
            command="insert",
            path="/workspace/tmp/x.txt",
            insert_line=0,
            new_str="bad",
        )


@pytest.mark.asyncio
async def test_str_replace_editor_rejects_traversal_into_protected_path(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    editor = StrReplaceEditor.with_operator(operator)

    with pytest.raises(ToolError):
        await editor.execute(
            command="create",
            path="/workspace/app/frontend/../backend/core/config.py",
            file_text="bad",
        )


@pytest.mark.asyncio
async def test_str_replace_editor_allows_view_on_protected_backend_paths(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    protected_file = operator.host_root / "app" / "backend" / "core" / "config.py"
    protected_file.parent.mkdir(parents=True, exist_ok=True)
    protected_file.write_text("safe read", encoding="utf-8")

    editor = StrReplaceEditor.with_operator(operator)
    result = await editor.execute(
        command="view",
        path="/workspace/app/backend/core/config.py",
    )

    assert "safe read" in result


@pytest.mark.asyncio
async def test_container_bash_session_blocks_workspace_write_targets():
    class FakeRuntimeService:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        async def exec(self, container_name, command):
            self.calls.append((container_name, command))
            return 0, "ok", ""

    runtime_service = FakeRuntimeService()
    session = ContainerBashSession(runtime_service, "container-1")

    with pytest.raises(ToolError):
        await session.run("echo hi >/workspace/tmp/x.txt")

    assert runtime_service.calls == []


@pytest.mark.asyncio
async def test_container_bash_session_blocks_nested_shell_write_targets():
    class FakeRuntimeService:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        async def exec(self, container_name, command):
            self.calls.append((container_name, command))
            return 0, "ok", ""

    runtime_service = FakeRuntimeService()
    session = ContainerBashSession(runtime_service, "container-1")

    with pytest.raises(ToolError):
        await session.run("sh -c 'echo x >/workspace/app/backend/core/pwn.py'")

    with pytest.raises(ToolError):
        await session.run("bash -lc 'mv a /workspace/app/backend/models/x.py'")

    assert runtime_service.calls == []


@pytest.mark.asyncio
async def test_container_bash_session_blocks_interpreter_write_targets():
    class FakeRuntimeService:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        async def exec(self, container_name, command):
            self.calls.append((container_name, command))
            return 0, "ok", ""

    runtime_service = FakeRuntimeService()
    session = ContainerBashSession(runtime_service, "container-1")

    with pytest.raises(ToolError):
        await session.run(
            'python -c "open(\'/workspace/app/backend/core/pwn.py\',\'w\').write(\'x\')"'
        )

    with pytest.raises(ToolError):
        await session.run(
            'python -c "p=\'/workspace/app/backend\'; open(p + \'/core/pwn.py\',\'w\').write(\'x\')"'
        )

    assert runtime_service.calls == []


@pytest.mark.asyncio
async def test_container_bash_session_allows_read_only_workspace_commands():
    class FakeRuntimeService:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        async def exec(self, container_name, command):
            self.calls.append((container_name, command))
            return 0, "ok", ""

    runtime_service = FakeRuntimeService()
    session = ContainerBashSession(runtime_service, "container-1")

    result = await session.run("cat /workspace/app/backend/core/config.py")

    assert runtime_service.calls == [
        ("container-1", "cd /workspace && cat /workspace/app/backend/core/config.py")
    ]
    assert result.output == "ok"


@pytest.mark.asyncio
async def test_container_bash_session_allows_read_only_interpreter_workspace_inspection():
    class FakeRuntimeService:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        async def exec(self, container_name, command):
            self.calls.append((container_name, command))
            return 0, "/workspace/app/backend/core/config.py", ""

    runtime_service = FakeRuntimeService()
    session = ContainerBashSession(runtime_service, "container-1")

    result = await session.run('python -c "print(\'/workspace/app/backend/core/config.py\')"')

    assert runtime_service.calls == [
        (
            "container-1",
            'cd /workspace && python -c "print(\'/workspace/app/backend/core/config.py\')"',
        )
    ]
    assert result.output == "/workspace/app/backend/core/config.py"


@pytest.mark.asyncio
async def test_project_file_operator_emits_snapshot_on_write(tmp_path):
    events: list[dict[str, object]] = []
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
        event_sink=events.append,
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)

    await operator.write_file(
        "/workspace/app/frontend/src/App.tsx", "export default function App() {}"
    )

    assert events == [
        {
            "type": "file.snapshot",
            "path": "app/frontend/src/App.tsx",
            "content": "export default function App() {}",
        }
    ]


@pytest.mark.asyncio
async def test_project_file_operator_rejects_path_outside_workspace(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ToolError):
        await operator.read_file("/etc/passwd")


@pytest.mark.asyncio
async def test_project_file_operator_exists(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    (operator.host_root / "docs" / "exists.txt").parent.mkdir(parents=True, exist_ok=True)
    (operator.host_root / "docs" / "exists.txt").write_text("hi", encoding="utf-8")
    assert await operator.exists("/workspace/docs/exists.txt") is True
    assert await operator.exists("/workspace/docs/missing.txt") is False


@pytest.mark.asyncio
async def test_project_file_operator_is_directory(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)
    (operator.host_root / "app" / "frontend" / "src").mkdir(parents=True)
    assert await operator.is_directory("/workspace/app/frontend/src") is True
    (operator.host_root / "docs" / "afile.txt").parent.mkdir(parents=True, exist_ok=True)
    (operator.host_root / "docs" / "afile.txt").write_text("", encoding="utf-8")
    assert await operator.is_directory("/workspace/docs/afile.txt") is False


@pytest.mark.asyncio
async def test_project_file_operator_run_command_maps_workspace_paths(tmp_path):
    operator = ProjectFileOperator(
        host_root=tmp_path / "user-1" / "1",
        container_root=Path("/workspace"),
    )
    operator.host_root.mkdir(parents=True, exist_ok=True)

    returncode, stdout, stderr = await operator.run_command(
        "printf '%s' /workspace/app/frontend/src"
    )

    assert returncode == 0
    assert stdout == str(operator.host_root / "app" / "frontend" / "src")
    assert stderr == ""


# ---------------------------------------------------------------------------
# Schema / route validation tests
# ---------------------------------------------------------------------------

def _make_app_with_fake_deps(monkeypatch, fake_agent_cls=None):
    """Build a FastAPI app with mocked auth, DB, workspace, and sandbox."""
    from dependencies.auth import get_current_user
    from core.database import get_db
    from schemas.auth import UserResponse

    fake_user = UserResponse(id="user-1", email="test@example.com", name="Test", role="user")

    class FakeDB:
        async def execute(self, *args, **kwargs):
            class R:
                def scalar_one_or_none(self):
                    return None
                def scalar(self):
                    return 0
                def scalars(self):
                    return self
                def all(self):
                    return []
            return R()

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

        def add(self, obj):
            pass

        async def rollback(self):
            pass

    class _FakeWorkspacePaths:
        host_root = Path("/tmp/fake_workspace/user-1/1")
        container_root = Path("/workspace")

    class _FakeWorkspaceService:
        def resolve_paths(self, user_id, project_id):
            return _FakeWorkspacePaths()

        def materialize_files(self, host_root, project_files):
            pass

        def snapshot_files(self, host_root):
            return {}

    class _FakeSandboxService:
        async def ensure_runtime(self, user_id, project_id, host_root):
            return None

        async def exec(self, container_name, command):
            return 0, "", ""

    monkeypatch.setattr("routers.agent_runtime._get_workspace_service", lambda: _FakeWorkspaceService())
    monkeypatch.setattr("routers.agent_runtime._get_sandbox_service", lambda: _FakeSandboxService())
    if fake_agent_cls is not None:
        monkeypatch.setattr("routers.agent_runtime.StreamingSWEAgent", fake_agent_cls)
    monkeypatch.setattr("routers.agent_runtime.build_agent_llm", lambda model: None)

    app = FastAPI()
    app.include_router(router)

    async def fake_get_current_user():
        return fake_user

    async def fake_get_db():
        yield FakeDB()

    app.dependency_overrides[get_current_user] = fake_get_current_user
    app.dependency_overrides[get_db] = fake_get_db
    return app


def test_agent_run_requires_project_id(monkeypatch):
    app = _make_app_with_fake_deps(monkeypatch)
    client = TestClient(app)
    response = client.post("/api/v1/agent/run", json={"prompt": "build app", "agent": "swe"})
    assert response.status_code == 422


def test_agent_run_accepts_project_id(monkeypatch):
    """Smoke test: route accepts project_id and streams events."""

    class FakeAgent:
        name = "swe"

        def __init__(self, *args, event_emitter=None, **kwargs):
            self._emit = event_emitter

        async def run(self, request: str):
            await self._emit({"type": "assistant", "agent": "swe", "content": "ok"})
            return "finished"

        @classmethod
        def build_for_workspace(cls, llm, event_emitter, file_operator, bash_session):
            return cls(event_emitter=event_emitter)

    app = _make_app_with_fake_deps(monkeypatch, fake_agent_cls=FakeAgent)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/run",
            json={"prompt": "build a todo app", "project_id": 42},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: session" in body
    assert "event: done" in body
