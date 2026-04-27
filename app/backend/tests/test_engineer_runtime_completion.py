import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.engineer_runtime import (
    _build_backend_check_command,
    _extract_terminate_summary,
    _probe_backend_health,
    run_engineer_session,
)
from services.agent_bootstrap import BootstrapContext

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

    monkeypatch.setattr("services.agent_bootstrap.classify_user_request_async", AsyncMock(return_value=BootstrapContext(mode="implementation", requires_backend_readme=False, requires_draft_plan=True)))

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

    fake_task_store = MagicMock()
    fake_task_store.list_tasks = AsyncMock(return_value=[FakeTask("Task 1", "completed")])
    monkeypatch.setattr("services.agent_task_store.AgentTaskStore", lambda db: fake_task_store)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

        def check_todo_write(self):
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
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
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

    monkeypatch.setattr("services.agent_bootstrap.classify_user_request_async", AsyncMock(return_value=BootstrapContext(mode="implementation", requires_backend_readme=False, requires_draft_plan=True)))

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

        def check_todo_write(self):
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


def test_build_backend_check_command_quotes_backend_dir():
    backend_dir = "/workspace/app/backend; echo pwned"

    command = _build_backend_check_command(backend_dir, "/health")

    assert command.startswith(f"cd {shlex.quote(backend_dir)} && ")
    assert f"cd {backend_dir} && " not in command
    assert "uv pip install --python .venv/bin/python -r requirements.txt -q 2>&1)" in command
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
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "venv" ] && [ "$2" = ".venv" ]; then
  mkdir -p .venv/bin
  ln -sf {shlex.quote(sys.executable)} .venv/bin/python
  exit 0
fi
echo "unexpected uv args: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        ["bash", "-lc", _build_backend_check_command(str(backend_dir), "/health")],
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
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "venv" ] && [ "$2" = ".venv" ]; then
  mkdir -p .venv/bin
  ln -sf {shlex.quote(sys.executable)} .venv/bin/python
  exit 0
fi
echo "unexpected uv args: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        [
            "bash",
            "-lc",
            _build_backend_check_command(str(backend_dir), f"/health$(touch {marker})"),
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
async def test_engineer_runtime_pushback_on_incomplete_tasks(monkeypatch, tmp_path):
    events = []
    async def fake_event_sink(event):
        events.append(event)
    
    agent = FakeAgent()
    
    monkeypatch.setattr("services.agent_bootstrap.classify_user_request_async", AsyncMock(return_value=BootstrapContext(mode="implementation", requires_backend_readme=False, requires_draft_plan=True)))
    
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
    
    fake_task_store = MagicMock()
    # First call: incomplete tasks
    # Second call: completed tasks
    fake_task_store.list_tasks = AsyncMock(side_effect=[
        [FakeTask("Task 1", "pending")],
        [FakeTask("Task 1", "completed")]
    ])
    monkeypatch.setattr("services.agent_task_store.AgentTaskStore", lambda db: fake_task_store)
    
    # We need to mock gate.approved_request_key
    class FakeGate:
        approved_request_key = "req_123"
        def check_write(self, path): pass
        def check_todo_write(self): pass
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
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=fake_preview_url_builder,
        preview_contract_loader=lambda p: None
    )
    
    # Agent should have been run twice (1 original + 1 pushback)
    assert agent.run_calls == 2

    # Check if pushback message was sent
    system_messages = [e for e in events if e.get("agent") == "system"]
    assert len(system_messages) > 0
    assert "COMPLETION GATE" in system_messages[0]["content"]
    assert "Task 1" in system_messages[0]["content"]


@pytest.mark.asyncio
async def test_engineer_runtime_reuses_same_agent_for_pushback_rounds(monkeypatch):
    events = []

    async def fake_event_sink(event):
        events.append(event)

    monkeypatch.setattr(
        "services.agent_bootstrap.classify_user_request_async",
        AsyncMock(
            return_value=BootstrapContext(
                mode="implementation",
                requires_backend_readme=False,
                requires_draft_plan=True,
            )
        ),
    )

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

    fake_task_store = MagicMock()
    fake_task_store.list_tasks = AsyncMock(
        side_effect=[
            [FakeTask("Task 1", "pending")],
            [FakeTask("Task 1", "completed")],
        ]
    )
    monkeypatch.setattr("services.agent_task_store.AgentTaskStore", lambda db: fake_task_store)

    class FakeGate:
        approved_request_key = "req_123"

        def check_write(self, path):
            pass

        def check_todo_write(self):
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
        preview_session_fields_factory=lambda: {"preview_session_key": "123"},
        preview_url_builder=fake_preview_url_builder,
        preview_contract_loader=lambda p: None,
    )

    assert len(FakeAgentCls.instances) == 1
    assert FakeAgentCls.instances[0].run_calls == 2
    assert "build something" in FakeAgentCls.instances[0].prompts[0]
    assert "COMPLETION GATE" in FakeAgentCls.instances[0].prompts[1]
