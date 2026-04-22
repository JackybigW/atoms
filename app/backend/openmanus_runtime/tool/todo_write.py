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

    @classmethod
    def create(
        cls,
        file_operator: Any,
        event_sink: Any,
        task_store_factory: Any = None,
        project_id: int = 0,
        request_key: str = "",
    ) -> "TodoWriteTool":
        tool = cls()
        tool._file_operator = file_operator
        tool._event_sink = event_sink
        tool._task_store_factory = task_store_factory
        tool._project_id = project_id
        tool._request_key = request_key
        return tool

    async def execute(self, items: Optional[list] = None, **_kwargs) -> CLIResult:
        if items is None:
            items = []
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
            existing = await store.list_tasks(
                project_id=self._project_id,
                request_key=self._request_key or "todo",
            )
            existing_map = {t.subject: t for t in existing}

            summaries = []
            for item in items:
                subject = item.get("text", "")
                status = item.get("status", "pending")
                if subject in existing_map:
                    updated = await store.update_task(
                        task_id=existing_map[subject].id,
                        status=status,
                    )
                    if updated:
                        summaries.append({
                            "id": updated.id,
                            "subject": updated.subject,
                            "status": updated.status,
                            "blocked_by": updated.blocked_by,
                        })
                else:
                    created = await store.create_task(
                        project_id=self._project_id,
                        request_key=self._request_key or "todo",
                        subject=subject,
                        description="",
                        status=status,
                    )
                    summaries.append({
                        "id": created.id,
                        "subject": created.subject,
                        "status": created.status,
                        "blocked_by": created.blocked_by,
                    })

            summary_event = {"type": "task_store.summary", "tasks": summaries}
            result = self._event_sink(summary_event)
            if hasattr(result, "__await__"):
                await result

        return CLIResult(output=f"docs/todo.md updated with {len(items)} items.")
