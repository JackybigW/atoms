from typing import Any, Optional

from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.tool.base import BaseTool, CLIResult

_STATUS_ORDER = {"pending": 0, "in_progress": 1, "completed": 2}


def _render_todo_markdown(items: list[dict]) -> str:
    _STATUS_MARKERS = {
        "completed": "x",
        "in_progress": ">",
        "blocked": "!",
    }
    lines = ["# Todo\n\n"]
    for item in items:
        status = item.get("status", "pending")
        marker = _STATUS_MARKERS.get(status, " ")
        lines.append(f"- [{marker}] {item.get('text', '')}\n")
    return "".join(lines)


class TodoWriteTool(BaseTool):
    name: str = "todo_write"
    description: str = (
        "Write or update docs/todo.md with the current task checklist. "
        "At most one item may be in_progress at a time."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "request_key": {
                "type": "string",
                "description": "Stable request key matching the approved draft plan.",
            },
            "source_plan_path": {
                "type": "string",
                "description": "Path to the implementation plan file under docs/plans/.",
            },
            "items": {
                "type": "array",
                "description": "Ordered list of todo items.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "blocked"],
                        },
                    },
                    "required": ["id", "text", "status"],
                },
            },
        },
        "required": ["items"],
    }

    # Injected via create()
    _file_operator: Any = None
    _event_sink: Any = None
    _task_store_factory: Any = None
    _project_id: int = 0
    _request_key: str = ""
    _approval_gate: Any = None

    @classmethod
    def create(
        cls,
        file_operator: Any,
        event_sink: Any,
        task_store_factory: Any = None,
        project_id: int = 0,
        request_key: str = "",
        approval_gate: Any = None,
    ) -> "TodoWriteTool":
        tool = cls()
        tool._file_operator = file_operator
        tool._event_sink = event_sink
        tool._task_store_factory = task_store_factory
        tool._project_id = project_id
        tool._request_key = request_key
        tool._approval_gate = approval_gate
        return tool

    async def execute(
        self,
        items: Optional[list] = None,
        request_key: str = "",
        source_plan_path: str = "",
        **_kwargs,
    ) -> CLIResult:
        if self._approval_gate is not None:
            self._approval_gate.check_todo_write()
        if items is None:
            items = []
        if len(items) > 8:
            raise ToolError("At most 8 todo items are allowed")
        in_progress = [i for i in items if i.get("status") == "in_progress"]
        if len(in_progress) > 1:
            raise ToolError("At most one item may be in_progress at a time")

        # Hard constraint validation: requires current task state from the store
        if self._task_store_factory is not None and self._project_id:
            effective_request_key = request_key or self._request_key
            if not effective_request_key and self._approval_gate is not None:
                effective_request_key = self._approval_gate.approved_request_key or ""
            if effective_request_key:
                store = self._task_store_factory()
                existing = await store.list_tasks(
                    project_id=self._project_id,
                    request_key=effective_request_key,
                )
                if existing:
                    existing_by_key = {task.task_key: task for task in existing}
                    for item in items:
                        task_key = str(item.get("id") or "").strip()
                        new_status = str(item.get("status") or "pending")
                        existing_task = existing_by_key.get(task_key)
                        if existing_task is not None:
                            old_order = _STATUS_ORDER.get(existing_task.status, 0)
                            new_order = _STATUS_ORDER.get(new_status, 0)
                            if new_order < old_order:
                                raise ToolError(
                                    f"Task '{task_key}' cannot move backward: "
                                    f"status is forward only "
                                    f"({existing_task.status} → {new_status} is not allowed)."
                                )
                    for item in items:
                        if item.get("status") != "in_progress":
                            continue
                        task_key = str(item.get("id") or "").strip()
                        blockers = item.get("blocked_by") or []
                        if not blockers:
                            existing_task = existing_by_key.get(task_key)
                            if existing_task is not None:
                                blockers = existing_task.blocked_by or []
                        for blocker_key in blockers:
                            blocker = existing_by_key.get(str(blocker_key))
                            blocker_status = blocker.status if blocker is not None else "pending"
                            if blocker_status != "completed":
                                raise ToolError(
                                    f"Task '{task_key}' is blocked by task '{blocker_key}' "
                                    f"which is not yet completed (status: {blocker_status}). "
                                    f"Complete all blocker tasks before setting this task to in_progress."
                                )

        # Notify the approval gate before the file write so the gate state is correct
        if self._approval_gate is not None:
            has_active = any(i.get("status") == "in_progress" for i in items)
            if has_active:
                self._approval_gate.notify_task_active()
            else:
                self._approval_gate.notify_no_active_task()

        content = _render_todo_markdown(items)
        if self._approval_gate is not None:
            self._approval_gate.begin_todo_write()
        try:
            await self._file_operator.write_file("/workspace/docs/todo.md", content)
        finally:
            if self._approval_gate is not None:
                self._approval_gate.end_todo_write()

        event = {"type": "todo.updated", "items": items}
        result = self._event_sink(event)
        if hasattr(result, "__await__"):
            await result

        if self._task_store_factory is not None and self._project_id:
            store = self._task_store_factory()
            effective_request_key = request_key or self._request_key
            if not effective_request_key and self._approval_gate is not None:
                effective_request_key = self._approval_gate.approved_request_key or ""
            if not effective_request_key:
                raise ToolError("todo_write requires a request_key")

            effective_plan_path = source_plan_path
            if not effective_plan_path and self._approval_gate is not None:
                effective_plan_path = self._approval_gate.plan_path or ""

            synced = await store.sync_request_tasks(
                project_id=self._project_id,
                request_key=effective_request_key,
                source_plan_path=effective_plan_path,
                items=items,
            )
            summaries = [
                {
                    "id": task.id,
                    "subject": task.subject,
                    "status": task.status,
                    "blocked_by": task.blocked_by,
                }
                for task in synced
            ]

            summary_event = {"type": "task_store.summary", "tasks": summaries}
            result = self._event_sink(summary_event)
            if hasattr(result, "__await__"):
                await result

        if self._approval_gate is not None:
            self._approval_gate.record_todo_written()

        return CLIResult(output=f"docs/todo.md updated with {len(items)} items.")
