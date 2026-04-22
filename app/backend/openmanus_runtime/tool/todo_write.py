from typing import Any, Optional

from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.tool.base import BaseTool, CLIResult


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

        content = _render_todo_markdown(items)
        await self._file_operator.write_file("/workspace/docs/todo.md", content)

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

        return CLIResult(output=f"docs/todo.md updated with {len(items)} items.")
