import pytest
from unittest.mock import AsyncMock, MagicMock
from services.engineer_runtime import run_engineer_session
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
    host_root = "/tmp/fake_host"
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
    assert "CRITICAL ERROR" in system_messages[0]["content"]
    assert "Task 1" in system_messages[0]["content"]


@pytest.mark.asyncio
async def test_engineer_runtime_uses_fresh_agent_instance_for_pushback_rounds(monkeypatch):
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

    assert len(FakeAgentCls.instances) == 2
    assert FakeAgentCls.instances[0].run_calls == 1
    assert FakeAgentCls.instances[1].run_calls == 1
    assert "build something" in FakeAgentCls.instances[0].prompts[0]
    assert "CRITICAL ERROR" in FakeAgentCls.instances[1].prompts[0]
