import asyncio
from typing import Any, Optional

from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.tool.base import BaseTool, CLIResult


_DRAFT_PLAN_DESCRIPTION = """Submit a concise implementation plan for user approval before starting any code changes.
Call this tool as your FIRST action for implementation requests. Wait for approval before writing any code."""


class DraftPlanTool(BaseTool):
    name: str = "draft_plan"
    description: str = _DRAFT_PLAN_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "request_key": {
                "type": "string",
                "description": "A short identifier for this request (e.g. 'auth-flow').",
            },
            "items": {
                "type": "array",
                "description": "Ordered list of high-level implementation steps.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["id", "text"],
                },
            },
        },
        "required": ["request_key", "items"],
    }

    # Injected via create()
    _event_sink: Any = None
    _service: Any = None
    _project_id: int = 0
    _approval_timeout: float = 300.0
    _approval_gate: Any = None

    @classmethod
    def create(cls, event_sink: Any, service: Any, project_id: int, approval_timeout: float = 300.0, approval_gate: Any = None) -> "DraftPlanTool":
        tool = cls()
        tool._event_sink = event_sink
        tool._service = service
        tool._project_id = project_id
        tool._approval_timeout = approval_timeout
        tool._approval_gate = approval_gate
        return tool

    async def execute(self, request_key: str = "request", items: Optional[list] = None, **_kwargs) -> CLIResult:
        if items is None:
            items = []
        if not (3 <= len(items) <= 7):
            raise ToolError("draft_plan requires 3 to 7 items")
        from services.agent_draft_plan import DraftPlanState
        state = DraftPlanState(
            project_id=self._project_id,
            request_key=request_key,
            items=items,
        )
        approval_event = self._service.put(state)
        await self._event_sink({
            "type": "draft_plan.pending",
            "request_key": request_key,
            "items": items,
        })
        try:
            await asyncio.wait_for(approval_event.wait(), timeout=self._approval_timeout)
        except asyncio.TimeoutError:
            raise ToolError("Draft plan approval timed out")
        if self._approval_gate is not None:
            self._approval_gate.approve(request_key=request_key)
        await self._event_sink({
            "type": "draft_plan.approved",
            "request_key": request_key,
        })
        return CLIResult(output="Plan approved. Proceed with implementation.")
