import pytest
from unittest.mock import AsyncMock, MagicMock
from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.tool.todo_write import TodoWriteTool


def _make_tool(current_tasks=None, approval_gate=None):
    """Helper to create a TodoWriteTool with mocked dependencies."""
    tool = TodoWriteTool()
    tool._approval_gate = approval_gate
    tool._project_id = 42

    if current_tasks is not None:
        fake_store = MagicMock()
        fake_store.list_tasks = AsyncMock(return_value=current_tasks)
        fake_store.sync_request_tasks = AsyncMock(return_value=current_tasks)
        tool._task_store_factory = lambda: fake_store
        tool._request_key = "req-1"
    else:
        tool._task_store_factory = None

    # Stub file operator and event sink so they don't error
    tool._file_operator = MagicMock()
    tool._file_operator.write_file = AsyncMock()
    tool._event_sink = MagicMock(return_value=None)

    return tool


class FakeTask:
    def __init__(self, task_key, status, blocked_by=None):
        self.id = task_key
        self.task_key = task_key
        self.status = status
        self.blocked_by = blocked_by or []
        self.subject = f"Task {task_key}"


@pytest.mark.asyncio
async def test_todo_write_rejects_backward_status_transition():
    current = [FakeTask("1", "completed"), FakeTask("2", "pending")]
    tool = _make_tool(current_tasks=current)

    with pytest.raises(ToolError, match="cannot move.*backward|forward only"):
        await tool.execute(
            items=[
                {"id": "1", "text": "Task 1", "status": "in_progress"},  # completed→in_progress: INVALID
                {"id": "2", "text": "Task 2", "status": "pending"},
            ],
            request_key="req-1",
        )


@pytest.mark.asyncio
async def test_todo_write_rejects_in_progress_when_blocker_not_complete():
    current = [
        FakeTask("1", "pending"),  # blocker
        FakeTask("2", "pending", blocked_by=["1"]),
    ]
    tool = _make_tool(current_tasks=current)

    with pytest.raises(ToolError, match="blocked by|blocker"):
        await tool.execute(
            items=[
                {"id": "1", "text": "Task 1", "status": "pending"},
                {"id": "2", "text": "Task 2", "status": "in_progress", "blocked_by": ["1"]},
            ],
            request_key="req-1",
        )


@pytest.mark.asyncio
async def test_todo_write_allows_in_progress_when_blocker_complete():
    current = [
        FakeTask("1", "completed"),
        FakeTask("2", "pending", blocked_by=["1"]),
    ]
    tool = _make_tool(current_tasks=current)

    # Should not raise
    await tool.execute(
        items=[
            {"id": "1", "text": "Task 1", "status": "completed"},
            {"id": "2", "text": "Task 2", "status": "in_progress", "blocked_by": ["1"]},
        ],
        request_key="req-1",
    )


@pytest.mark.asyncio
async def test_todo_write_notifies_gate_when_task_active():
    gate = MagicMock()
    gate.check_todo_write = MagicMock()
    gate.approved_request_key = "req-1"
    gate.plan_path = "docs/plans/plan.md"
    gate.begin_todo_write = MagicMock()
    gate.end_todo_write = MagicMock()
    gate.record_todo_written = MagicMock()
    gate.notify_task_active = MagicMock()
    gate.notify_no_active_task = MagicMock()

    tool = _make_tool(current_tasks=[], approval_gate=gate)

    await tool.execute(
        items=[{"id": "1", "text": "Task 1", "status": "in_progress"}],
        request_key="req-1",
    )

    gate.notify_task_active.assert_called_once()
    gate.notify_no_active_task.assert_not_called()


@pytest.mark.asyncio
async def test_todo_write_notifies_gate_no_active_when_all_completed():
    gate = MagicMock()
    gate.check_todo_write = MagicMock()
    gate.approved_request_key = "req-1"
    gate.plan_path = "docs/plans/plan.md"
    gate.begin_todo_write = MagicMock()
    gate.end_todo_write = MagicMock()
    gate.record_todo_written = MagicMock()
    gate.notify_task_active = MagicMock()
    gate.notify_no_active_task = MagicMock()

    tool = _make_tool(current_tasks=[], approval_gate=gate)

    await tool.execute(
        items=[{"id": "1", "text": "Task 1", "status": "completed"}],
        request_key="req-1",
    )

    gate.notify_no_active_task.assert_called_once()
    gate.notify_task_active.assert_not_called()
