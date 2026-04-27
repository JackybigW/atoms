import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent_run_logs import AgentRunLogStore
from services.engineer_runtime import (
    _WORKSPACES_ROOT,
    _build_backend_check_command,
    _extract_terminate_summary,
    _probe_backend_health,
    run_engineer_session,
)
from openmanus_runtime.schema import Function, Message, ToolCall

class FakeResult:
    def scalars(self): return self
    def all(self): return []
    def scalar_one_or_none(self): return None
    def scalar(self): return 0

class FakeDB:
    async def execute(self, *args, **kwargs):
        return FakeResult()

class FakeWorkspacePaths:
    host_root = Path("/tmp/fake_host")
    container_root = "/workspace"

class FakeWorkspaceService:
    def resolve_paths(self, user_id, project_id):
        return FakeWorkspacePaths()
    def materialize_files(self, host_root, file_records):
        pass
    def snapshot_files(self, host_root):
        return {}

class FakeSandboxService:
    async def ensure_runtime(self, user_id, project_id, host_root):
        return "fake-container"
    async def start_preview_services(self, container_name, env):
        return 0, "out", "err"
    async def get_runtime_ports(self, container_name):
        return {"frontend_port": 3000, "backend_port": 8000, "preview_port": 8001}
    async def wait_for_service(self, container_name, port, path="/", timeout_seconds=60.0, poll_interval_seconds=1.0):
        return True

class FakeAgent:
    name = "engineer"
    def __init__(self):
        self.run_calls = 0
        self.prompts = []

    async def run(self, prompt):
        self.run_calls += 1
        self.prompts.append(prompt)
        return "fake_result"

class FakeTask:
    def __init__(self, subject, status):
        self.subject = subject
        self.status = status

@pytest.mark.asyncio
async def test_engineer_runtime_surfaces_terminate_summary_before_done(monkeypatch):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)

    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "secret-preview-key"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "running"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)

    fake_task_store = MagicMock()
    fake_task_store.list_tasks = AsyncMock(return_value=[FakeTask("Task 1", "completed")])
    monkeypatch.setattr("services.agent_task_store.AgentTaskStore", lambda db: fake_task_store)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class FakeBashSession:
        def has_verification_run(self):
            return True

    monkeypatch.setattr("services.engineer_runtime.ContainerBashSession", lambda *args, **kwargs: FakeBashSession())

    class SummaryAgent(FakeAgent):
        async def run(self, prompt):
            self.run_calls += 1
            self.prompts.append(prompt)
            return "The interaction has been completed with status: success\nSummary: Built the app and verified preview."

    agent = SummaryAgent()

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return agent

    def fake_preview_url_builder(key):
        return {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"}

    await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build something",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: FakeWorkspaceService(),
        sandbox_service_factory=lambda: FakeSandboxService(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "secret-preview-key"},
        preview_url_builder=fake_preview_url_builder,
        preview_contract_loader=lambda p: None,
    )

    summary_index = next(
        index
        for index, event in enumerate(events)
        if event.get("type") == "assistant"
        and event.get("agent") == "engineer"
        and event.get("content") == "Built the app and verified preview."
    )
    done_index = next(index for index, event in enumerate(events) if event.get("type") == "done")

    assert summary_index < done_index
    terminal_logs = [event["content"] for event in events if event.get("type") == "terminal.log"]
    assert any("preview ready" in log for log in terminal_logs)
    assert not any("secret-preview-key" in log for log in terminal_logs)


def test_extract_terminate_summary_uses_last_terminate_output():
    result = (
        "tool output\n"
        "Summary: wrong\n"
        "...\n"
        "The interaction has been completed with status: success\n"
        "Summary: right"
    )

    assert _extract_terminate_summary(result) == "right"
    assert _extract_terminate_summary("tool output\nSummary: wrong") == ""
    assert _extract_terminate_summary(None) == ""


def test_extract_terminate_summary_ignores_later_tool_observation_summary():
    result = (
        "Step 1: Observed output of cmd `terminate` executed:\n"
        "The interaction has been completed with status: success\n\n"
        "Observed output of cmd `bash` executed:\n"
        "Summary: wrong"
    )

    assert _extract_terminate_summary(result) == ""


@pytest.mark.asyncio
async def test_engineer_runtime_emits_summary_before_preview_failure(monkeypatch):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)

    fake_task_store = MagicMock()
    fake_task_store.list_tasks = AsyncMock(return_value=[FakeTask("Task 1", "completed")])
    monkeypatch.setattr("services.agent_task_store.AgentTaskStore", lambda db: fake_task_store)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class FakeBashSession:
        def has_verification_run(self):
            return True

    monkeypatch.setattr("services.engineer_runtime.ContainerBashSession", lambda *args, **kwargs: FakeBashSession())

    class SummaryAgent(FakeAgent):
        async def run(self, prompt):
            self.run_calls += 1
            self.prompts.append(prompt)
            return "The interaction has been completed with status: success\nSummary: Built the app and verified preview."

    agent = SummaryAgent()

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return agent

    class PreviewFailureSandboxService(FakeSandboxService):
        async def start_preview_services(self, container_name, env):
            return 2, "", "start-preview failed"

    def fake_preview_url_builder(key):
        return {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"}

    await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build something",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: FakeWorkspaceService(),
        sandbox_service_factory=lambda: PreviewFailureSandboxService(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=fake_preview_url_builder,
        preview_contract_loader=lambda p: None,
    )

    summary_indexes = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "assistant"
        and event.get("agent") == "engineer"
        and event.get("content") == "Built the app and verified preview."
    ]
    preview_failed_index = next(
        index
        for index, event in enumerate(events)
        if event.get("type") == "preview_failed"
    )

    assert len(summary_indexes) == 1
    assert summary_indexes[0] < preview_failed_index
    assert all(index < preview_failed_index for index in summary_indexes)

@pytest.mark.asyncio
async def test_backend_probe_scans_lazy_imports(tmp_path):
    backend_dir = tmp_path / "app" / "backend"
    backend_dir.mkdir(parents=True)
    (backend_dir / "main.py").write_text(
        """
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "healthy"}

def scan_barcode(image):
    from pyzbar.pyzbar import decode
    return decode(image)
""".lstrip(),
        encoding="utf-8",
    )

    class Contract:
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    recorded = []

    class Sandbox:
        async def exec(self, container_name, command):
            recorded.append(command)
            return 0, "ATOMS_BACKEND_CHECK_OK\n", ""

    has_backend, error = await _probe_backend_health(
        Sandbox(),
        "fake-container",
        tmp_path,
        lambda root: Contract(),
    )

    assert has_backend is True
    assert error is None
    assert len(recorded) == 1
    assert "importlib.import_module(module_name)" in recorded[0]

@pytest.mark.asyncio
async def test_backend_probe_rejects_ok_stdout_when_command_fails(tmp_path):
    class Contract:
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    class Sandbox:
        async def exec(self, container_name, command):
            return 1, "some output\nok", "boom"

    has_backend, error = await _probe_backend_health(
        Sandbox(),
        "fake-container",
        tmp_path,
        lambda root: Contract(),
    )

    assert has_backend is True
    assert error is not None
    assert "Backend code check FAILED" in error
    assert "some output\nokboom" in error


@pytest.mark.asyncio
async def test_backend_probe_classifies_dependency_install_failure(tmp_path):
    class Contract:
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    class Sandbox:
        async def exec(self, container_name, command):
            return (
                2,
                "",
                "atoms-deps-cache: backend miss hash=abc\n"
                "error: Request failed after 3 retries in 45.1s\n"
                "Caused by: Failed to fetch: `https://pypi.tuna.tsinghua.edu.cn/simple/fastapi/`",
            )

    has_backend, error = await _probe_backend_health(
        Sandbox(),
        "fake-container",
        tmp_path,
        lambda root: Contract(),
    )

    assert has_backend is True
    assert error is not None
    assert "Backend dependency install FAILED" in error
    assert "not a generated app code failure" in error


def test_build_backend_check_command_quotes_backend_dir():
    backend_dir = "/workspace/app/backend; echo pwned"

    command = _build_backend_check_command(backend_dir, "/health")

    assert command.startswith(f"cd {shlex.quote(backend_dir)} && ")
    assert f"cd {backend_dir} && " not in command
    assert f"/usr/local/bin/atoms-deps-cache backend install {shlex.quote(backend_dir)}" in command
    assert "uv pip install --python .venv/bin/python -r requirements.txt" not in command
    assert "|| true" not in command


def test_build_backend_check_command_detects_lazy_missing_import(tmp_path):
    backend_dir = tmp_path / "app" / "backend"
    backend_dir.mkdir(parents=True)
    (backend_dir / "main.py").write_text(
        """
class Route:
    path = "/health"

class App:
    routes = [Route()]

app = App()

def lazily_import_dependency():
    from definitely_missing_atoms_dependency import thing
    return thing
""".lstrip(),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cache = bin_dir / "atoms-deps-cache"
    fake_cache.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "backend" ] && [ "$2" = "install" ]; then
  cd "$3"
  mkdir -p .venv/bin
  ln -sf {shlex.quote(sys.executable)} .venv/bin/python
  exit 0
fi
echo "unexpected atoms-deps-cache args: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_cache.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    command = _build_backend_check_command(str(backend_dir), "/health").replace(
        "/usr/local/bin/atoms-deps-cache", str(fake_cache)
    )

    result = subprocess.run(
        ["bash", "-lc", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "definitely_missing_atoms_dependency" in result.stdout + result.stderr


def test_build_backend_check_command_does_not_expand_healthcheck_shell_payload(tmp_path):
    backend_dir = tmp_path / "app" / "backend"
    backend_dir.mkdir(parents=True)
    marker = tmp_path / "pwned"
    (backend_dir / "main.py").write_text(
        """
class Route:
    path = "/health"

class App:
    routes = [Route()]

app = App()
""".lstrip(),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cache = bin_dir / "atoms-deps-cache"
    fake_cache.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "backend" ] && [ "$2" = "install" ]; then
  cd "$3"
  mkdir -p .venv/bin
  ln -sf {shlex.quote(sys.executable)} .venv/bin/python
  exit 0
fi
echo "unexpected atoms-deps-cache args: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_cache.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    command = _build_backend_check_command(str(backend_dir), f"/health$(touch {marker})").replace(
        "/usr/local/bin/atoms-deps-cache", str(fake_cache)
    )

    result = subprocess.run(
        [
            "bash",
            "-lc",
            command,
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Missing /health$(" in result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.asyncio
async def test_engineer_runtime_pushback_on_backend_probe_failure(monkeypatch, tmp_path):
    events = []
    async def fake_event_sink(event):
        events.append(event)
    
    agent = FakeAgent()
    
    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)
    
    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)
    
    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "123"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "running"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)
    
    # We need to mock gate.approved_request_key
    class FakeGate:
        approved_request_key = "req_123"
        def check_write(self, path): pass
    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class FakeBashSession:
        def has_verification_run(self):
            return True
    monkeypatch.setattr("services.engineer_runtime.ContainerBashSession", lambda *args, **kwargs: FakeBashSession())

    # Build agent mock
    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return agent
    
    # We need to mock the preview URL builder to not crash
    def fake_preview_url_builder(key):
        return {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"}

    class BackendProbeSandbox(FakeSandboxService):
        def __init__(self):
            self.probe_calls = 0

        async def exec(self, container_name, command):
            self.probe_calls += 1
            if self.probe_calls == 1:
                return 1, "import failed", ""
            return 0, "ATOMS_BACKEND_CHECK_OK\n", ""

    class Contract:
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    sandbox = BackendProbeSandbox()
        
    await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build something",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: FakeWorkspaceService(),
        sandbox_service_factory=lambda: sandbox,
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=fake_preview_url_builder,
        preview_contract_loader=lambda p: Contract()
    )
    
    # Agent should have been run twice (1 original + 1 pushback)
    assert agent.run_calls == 2

    # Check if pushback message was sent
    system_messages = [e for e in events if e.get("agent") == "system"]
    assert len(system_messages) > 0
    assert "COMPLETION GATE" in system_messages[0]["content"]
    assert "Backend code check FAILED" in system_messages[0]["content"]


@pytest.mark.asyncio
async def test_engineer_runtime_stops_on_dependency_install_failure(monkeypatch):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    agent = FakeAgent()

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class FakeBashSession:
        def has_verification_run(self):
            return True

    monkeypatch.setattr("services.engineer_runtime.ContainerBashSession", lambda *args, **kwargs: FakeBashSession())

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return agent

    class DependencyInstallFailureSandbox(FakeSandboxService):
        async def exec(self, container_name, command):
            return (
                2,
                "",
                "atoms-deps-cache: backend miss hash=abc\n"
                "error: Request failed after 3 retries in 45.1s\n"
                "Caused by: Failed to fetch: `https://pypi.tuna.tsinghua.edu.cn/simple/fastapi/`",
            )

    class Contract:
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    result = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build something",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: FakeWorkspaceService(),
        sandbox_service_factory=lambda: DependencyInstallFailureSandbox(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: MagicMock(),
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=lambda key: {
            "preview_frontend_url": "http://front",
            "preview_backend_url": "http://back",
        },
        preview_contract_loader=lambda p: Contract(),
    )

    assert result is False
    assert agent.run_calls == 1
    assert any(
        event.get("type") == "preview_failed"
        and event.get("reason") == "dependency_install_failed"
        for event in events
    )


@pytest.mark.asyncio
async def test_engineer_runtime_reuses_same_agent_for_pushback_rounds(monkeypatch):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)

    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "123"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "running"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class FakeBashSession:
        def has_verification_run(self):
            return True

    monkeypatch.setattr("services.engineer_runtime.ContainerBashSession", lambda *args, **kwargs: FakeBashSession())

    class FreshAgent(FakeAgent):
        pass

    class FakeAgentCls:
        instances = []

        @staticmethod
        def build_for_workspace(*args, **kwargs):
            agent = FreshAgent()
            FakeAgentCls.instances.append(agent)
            return agent

    def fake_preview_url_builder(key):
        return {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"}

    class BackendProbeSandbox(FakeSandboxService):
        def __init__(self):
            self.probe_calls = 0

        async def exec(self, container_name, command):
            self.probe_calls += 1
            if self.probe_calls == 1:
                return 1, "import failed", ""
            return 0, "ATOMS_BACKEND_CHECK_OK\n", ""

    class Contract:
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    sandbox = BackendProbeSandbox()

    await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build something",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: FakeWorkspaceService(),
        sandbox_service_factory=lambda: sandbox,
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=fake_preview_url_builder,
        preview_contract_loader=lambda p: Contract(),
    )

    assert len(FakeAgentCls.instances) == 1
    assert FakeAgentCls.instances[0].run_calls == 2
    assert "build something" in FakeAgentCls.instances[0].prompts[0]
    assert "COMPLETION GATE" in FakeAgentCls.instances[0].prompts[1]


@pytest.mark.asyncio
async def test_engineer_runtime_feeds_tool_json_error_back_to_same_agent(monkeypatch):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)
    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "123"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "not_configured"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class FakeBashSession:
        def has_verification_run(self):
            return True

    monkeypatch.setattr("services.engineer_runtime.ContainerBashSession", lambda *args, **kwargs: FakeBashSession())

    class ToolJsonErrorAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.messages = []

        async def run(self, prompt):
            self.run_calls += 1
            self.prompts.append(prompt)
            if self.run_calls == 1:
                bad_call = ToolCall(
                    id="call_bad",
                    function=Function(name="str_replace_editor", arguments='{"command": "create", "path":'),
                )
                self.messages.append(Message.from_tool_calls(content="", tool_calls=[bad_call]))
                self.messages.append(
                    Message.tool_message(
                        content="Error: Error parsing arguments for str_replace_editor: Invalid JSON format",
                        name="str_replace_editor",
                        tool_call_id="call_bad",
                    )
                )
                raise Exception(
                    "Error code: 400 - invalid params, invalid function arguments json string, "
                    "tool_call_id: call_bad"
                )
            return "The interaction has been completed with status: success\nSummary: recovered"

    agent = ToolJsonErrorAgent()

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return agent

    result = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build something",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: FakeWorkspaceService(),
        sandbox_service_factory=lambda: FakeSandboxService(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=lambda key: {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"},
        preview_contract_loader=lambda p: None,
    )

    assert result is True
    assert agent.run_calls == 2
    assert "TOOL CALL JSON ERROR" in agent.prompts[1]
    assert "invalid function arguments json string" in agent.prompts[1]
    assert any(
        event.get("type") == "assistant"
        and event.get("agent") == "system"
        and "TOOL CALL JSON ERROR" in event.get("content", "")
        for event in events
    )
    assert not any(
        message.tool_calls
        and any(call.function.arguments == '{"command": "create", "path":' for call in message.tool_calls)
        for message in agent.messages
    )


@pytest.mark.asyncio
async def test_engineer_runtime_removes_all_tool_results_from_mixed_tool_json_error(monkeypatch):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)
    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "123"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "not_configured"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class FakeBashSession:
        def has_verification_run(self):
            return True

    monkeypatch.setattr("services.engineer_runtime.ContainerBashSession", lambda *args, **kwargs: FakeBashSession())

    class MixedToolJsonErrorAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.messages = []

        async def run(self, prompt):
            self.run_calls += 1
            self.prompts.append(prompt)
            if self.run_calls == 1:
                good_call = ToolCall(
                    id="call_good",
                    function=Function(name="str_replace_editor", arguments='{"command":"create","path":"/tmp/a"}'),
                )
                bad_call = ToolCall(
                    id="call_bad",
                    function=Function(name="str_replace_editor", arguments='{"command": "create", "path":'),
                )
                self.messages.append(Message.from_tool_calls(content="", tool_calls=[good_call, bad_call]))
                self.messages.append(
                    Message.tool_message(
                        content="File created successfully",
                        name="str_replace_editor",
                        tool_call_id="call_good",
                    )
                )
                self.messages.append(
                    Message.tool_message(
                        content="Error: Error parsing arguments for str_replace_editor: Invalid JSON format",
                        name="str_replace_editor",
                        tool_call_id="call_bad",
                    )
                )
                raise Exception(
                    "Error code: 400 - invalid params, invalid function arguments json string, "
                    "tool_call_id: call_bad"
                )
            return "The interaction has been completed with status: success\nSummary: recovered"

    agent = MixedToolJsonErrorAgent()

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return agent

    result = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build something",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: FakeWorkspaceService(),
        sandbox_service_factory=lambda: FakeSandboxService(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=lambda key: {
            "preview_frontend_url": "http://front",
            "preview_backend_url": "http://back",
        },
        preview_contract_loader=lambda p: None,
    )

    assert result is True
    assert agent.run_calls == 2
    assert not any(
        getattr(message, "role", None) == "tool"
        and getattr(message, "tool_call_id", None) in {"call_good", "call_bad"}
        for message in agent.messages
    )


@pytest.mark.asyncio
async def test_engineer_runtime_writes_metrics_summary(monkeypatch, tmp_path):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)
    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "123"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "not_configured"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class SummaryAgent(FakeAgent):
        async def run(self, prompt):
            self.run_calls += 1
            return "The interaction has been completed with status: success\nSummary: ok"

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return SummaryAgent()

    class Paths:
        host_root = tmp_path / "workspace"
        container_root = "/workspace"

    Paths.host_root.mkdir()

    class Workspace:
        def resolve_paths(self, user_id, project_id):
            return Paths()

        def materialize_files(self, host_root, file_records):
            pass

        def snapshot_files(self, host_root):
            return {}

    await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build frontend",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: Workspace(),
        sandbox_service_factory=lambda: FakeSandboxService(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=lambda key: {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"},
        preview_contract_loader=lambda p: None,
    )

    run_logs = AgentRunLogStore(base_root=_WORKSPACES_ROOT)
    latest = run_logs.read_latest_run(user_id="user-1", project_id=42)

    assert latest["metrics_summary"]["span_count"] > 0
    assert latest["metrics_summary"]["durations_ms"]["runtime"] >= 0


@pytest.mark.asyncio
async def test_engineer_runtime_blocks_preview_ready_when_backend_api_contract_missing(monkeypatch, tmp_path):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)
    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "123"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "running"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class SummaryAgent(FakeAgent):
        async def run(self, prompt):
            self.run_calls += 1
            return "The interaction has been completed with status: success\nSummary: ok"

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return SummaryAgent()

    host_root = tmp_path / "workspace"
    host_root.mkdir()

    class Paths:
        container_root = "/workspace"

        def __init__(self):
            self.host_root = host_root

    class Workspace:
        def resolve_paths(self, user_id, project_id):
            return Paths()

        def materialize_files(self, host_root, file_records):
            pass

        def snapshot_files(self, host_root):
            return {}

    class Contract:
        frontend = SimpleNamespace(healthcheck_path="/")
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    class MissingContractSandbox(FakeSandboxService):
        async def exec(self, container_name, command):
            return 0, "ATOMS_BACKEND_CHECK_OK\n", ""

        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            assert path == "/openapi.json"
            return 200, {"content-type": "application/json"}, b'{"paths":{"/health":{"get":{}},"/api/generate":{"post":{}}}}'

    result = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build api app",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: Workspace(),
        sandbox_service_factory=lambda: MissingContractSandbox(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=lambda key: {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"},
        preview_contract_loader=lambda p: Contract(),
    )

    run_logs = AgentRunLogStore(base_root=_WORKSPACES_ROOT)
    latest = run_logs.read_latest_run(user_id="user-1", project_id=42)

    assert result is False
    assert any(
        event.get("type") == "preview_failed" and event.get("reason") == "smoke_contract_missing"
        for event in events
    )
    assert not any(event.get("type") == "preview_ready" for event in events)
    assert latest["status"] == "failed"


@pytest.mark.asyncio
async def test_engineer_runtime_allows_custom_backend_healthcheck_without_smoke_contract(monkeypatch, tmp_path):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)
    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "123"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "running"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class SummaryAgent(FakeAgent):
        async def run(self, prompt):
            self.run_calls += 1
            return "The interaction has been completed with status: success\nSummary: ok"

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return SummaryAgent()

    host_root = tmp_path / "workspace"
    host_root.mkdir()

    class Paths:
        container_root = "/workspace"

        def __init__(self):
            self.host_root = host_root

    class Workspace:
        def resolve_paths(self, user_id, project_id):
            return Paths()

        def materialize_files(self, host_root, file_records):
            pass

        def snapshot_files(self, host_root):
            return {}

    class Contract:
        frontend = SimpleNamespace(healthcheck_path="/")
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/api/health",
        )

    class HealthOnlySandbox(FakeSandboxService):
        async def exec(self, container_name, command):
            return 0, "ATOMS_BACKEND_CHECK_OK\n", ""

        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            assert path == "/openapi.json"
            return 200, {"content-type": "application/json"}, b'{"paths":{"/api/health":{"get":{}}}}'

    result = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build api app",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: Workspace(),
        sandbox_service_factory=lambda: HealthOnlySandbox(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=lambda key: {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"},
        preview_contract_loader=lambda p: Contract(),
    )

    assert result is True
    assert any(event.get("type") == "preview_ready" for event in events)
    assert not any(
        event.get("type") == "preview_failed" and event.get("reason") == "smoke_contract_missing"
        for event in events
    )


@pytest.mark.asyncio
async def test_engineer_runtime_blocks_preview_ready_on_smoke_failure(monkeypatch, tmp_path):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)
    fake_session_record = MagicMock()
    fake_session_record.preview_session_key = "123"
    fake_session_record.preview_expires_at = None
    fake_session_record.frontend_status = "running"
    fake_session_record.backend_status = "running"
    fake_sessions_service.create = AsyncMock(return_value=fake_session_record)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class SummaryAgent(FakeAgent):
        async def run(self, prompt):
            self.run_calls += 1
            self.prompts.append(prompt)
            return "The interaction has been completed with status: success\nSummary: ok"

    agent = SummaryAgent()

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return agent

    host_root = tmp_path / "workspace"
    atoms_dir = host_root / ".atoms"
    atoms_dir.mkdir(parents=True)
    (atoms_dir / "smoke.json").write_text(
        json.dumps(
            {
                "version": 1,
                "checks": [
                    {
                        "name": "generate png",
                        "service": "backend",
                        "method": "POST",
                        "path": "/api/generate",
                        "json": {"content": "atoms-smoke-test"},
                        "expect": {"status": 200, "content_type": "image/png"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class Paths:
        container_root = "/workspace"

        def __init__(self):
            self.host_root = host_root

    class Workspace:
        def resolve_paths(self, user_id, project_id):
            return Paths()

        def materialize_files(self, host_root, file_records):
            pass

        def snapshot_files(self, host_root):
            return {}

    class Contract:
        frontend = SimpleNamespace(healthcheck_path="/")
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    class SmokeFailSandbox(FakeSandboxService):
        def __init__(self):
            self.start_calls = 0
            self.smoke_calls = 0

        async def start_preview_services(self, container_name, env):
            self.start_calls += 1
            return 0, "out", "err"

        async def exec(self, container_name, command):
            return 0, "ATOMS_BACKEND_CHECK_OK\n", ""

        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            self.smoke_calls += 1
            if self.smoke_calls == 1:
                return 200, {"content-type": "application/json"}, b'{"content":"not png"}'
            return 200, {"content-type": "image/png"}, b"\x89PNG\r\n\x1a\nabc"

    sandbox = SmokeFailSandbox()

    result = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build qr app",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: Workspace(),
        sandbox_service_factory=lambda: sandbox,
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=lambda key: {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"},
        preview_contract_loader=lambda p: Contract(),
    )

    run_logs = AgentRunLogStore(base_root=_WORKSPACES_ROOT)
    latest = run_logs.read_latest_run(user_id="user-1", project_id=42)

    assert result is True
    assert any(event.get("type") == "preview_failed" and event.get("reason") == "smoke_failed" for event in events)
    assert any(event.get("type") == "preview_ready" for event in events)
    assert latest["status"] == "completed"
    assert agent.run_calls == 2
    assert sandbox.start_calls == 2
    repair_prompts = [
        event["content"]
        for event in events
        if event.get("type") == "assistant" and event.get("agent") == "system"
    ]
    assert any("SMOKE CHECK FAILURE" in prompt for prompt in repair_prompts)
    assert any("generate png: expected content-type image/png, got application/json" in prompt for prompt in repair_prompts)


@pytest.mark.asyncio
async def test_engineer_runtime_fails_on_malformed_smoke_contract(monkeypatch, tmp_path):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    fake_files_service = MagicMock()
    fake_files_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.project_files.Project_filesService", lambda db: fake_files_service)

    fake_messages_service = MagicMock()
    fake_messages_service.get_list = AsyncMock(return_value={"items": []})
    monkeypatch.setattr("services.messages.MessagesService", lambda db: fake_messages_service)

    fake_sessions_service = MagicMock()
    fake_sessions_service.get_by_project = AsyncMock(return_value=None)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

    monkeypatch.setattr("services.approval_gate.ApprovalGate", lambda *args, **kwargs: FakeGate())

    class SummaryAgent(FakeAgent):
        async def run(self, prompt):
            self.run_calls += 1
            return "The interaction has been completed with status: success\nSummary: ok"

    class FakeAgentCls:
        @staticmethod
        def build_for_workspace(*args, **kwargs):
            return SummaryAgent()

    host_root = tmp_path / "workspace"
    atoms_dir = host_root / ".atoms"
    atoms_dir.mkdir(parents=True)
    (atoms_dir / "smoke.json").write_text(json.dumps({"version": 1}), encoding="utf-8")

    class Paths:
        container_root = "/workspace"

        def __init__(self):
            self.host_root = host_root

    class Workspace:
        def resolve_paths(self, user_id, project_id):
            return Paths()

        def materialize_files(self, host_root, file_records):
            pass

        def snapshot_files(self, host_root):
            return {}

    class Contract:
        frontend = SimpleNamespace(healthcheck_path="/")
        backend = SimpleNamespace(
            command="cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
            healthcheck_path="/health",
        )

    class SmokeSandbox(FakeSandboxService):
        async def exec(self, container_name, command):
            return 0, "ATOMS_BACKEND_CHECK_OK\n", ""

    result = await run_engineer_session(
        db=FakeDB(),
        user_id="user-1",
        project_id=42,
        prompt="build qr app",
        model="fake-model",
        event_sink=fake_event_sink,
        workspace_service_factory=lambda: Workspace(),
        sandbox_service_factory=lambda: SmokeSandbox(),
        agent_cls=FakeAgentCls,
        llm_builder=lambda model: None,
        workspace_runtime_sessions_service_cls=lambda db: fake_sessions_service,
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=lambda key: {"preview_frontend_url": "http://front", "preview_backend_url": "http://back"},
        preview_contract_loader=lambda p: Contract(),
    )

    run_logs = AgentRunLogStore(base_root=_WORKSPACES_ROOT)
    latest = run_logs.read_latest_run(user_id="user-1", project_id=42)

    assert result is False
    assert any(event.get("type") == "preview_failed" and event.get("reason") == "smoke_failed" for event in events)
    assert not any(event.get("type") == "preview_ready" for event in events)
    assert latest["status"] == "failed"
