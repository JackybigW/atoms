import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Optional, Protocol, runtime_checkable

from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.tool.base import BaseTool, CLIResult

logger = logging.getLogger(__name__)


_DRAFT_PLAN_DESCRIPTION = """Submit a concise implementation plan for user approval before starting any code changes.
Call this tool as your FIRST action for implementation requests. Wait for approval before writing any code."""


@runtime_checkable
class _DraftPlanService(Protocol):
    def put(self, project_id: int, request_key: str, items: list) -> asyncio.Event: ...
    def delete(self, project_id: int, request_key: str) -> None: ...


@runtime_checkable
class _ApprovalGate(Protocol):
    def approve(self, *, request_key: str) -> None: ...


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
    _stop_event: Any = None  # asyncio.Event | None

    @classmethod
    def create(
        cls,
        event_sink: Callable[[dict], Awaitable[None]],
        service: _DraftPlanService,
        project_id: int,
        approval_timeout: float = 300.0,
        approval_gate: Optional[_ApprovalGate] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> "DraftPlanTool":
        tool = cls()
        tool._event_sink = event_sink
        tool._service = service
        tool._project_id = project_id
        tool._approval_timeout = approval_timeout
        tool._approval_gate = approval_gate
        tool._stop_event = stop_event
        return tool

    async def execute(self, request_key: str = "request", items: Optional[list] = None, **_kwargs) -> CLIResult:
        if items is None:
            items = []
        if not (3 <= len(items) <= 7):
            raise ToolError("draft_plan requires 3 to 7 items")

        logger.info(
            "draft_plan execute project_id=%s request_key=%s items=%d timeout=%.0fs",
            self._project_id, request_key, len(items), self._approval_timeout,
        )

        approval_event = self._service.put(
            project_id=self._project_id,
            request_key=request_key,
            items=items,
        )

        logger.info("draft_plan emitting start project_id=%s request_key=%s", self._project_id, request_key)
        await self._event_sink({"type": "draft_plan.start", "request_key": request_key})
        for item in items:
            await asyncio.sleep(0.12)  # stagger items for streaming visual effect
            await self._event_sink({"type": "draft_plan.item", "request_key": request_key, "item": item})
        await asyncio.sleep(0.12)
        logger.info("draft_plan emitting ready project_id=%s request_key=%s", self._project_id, request_key)
        await self._event_sink({"type": "draft_plan.ready", "request_key": request_key})

        logger.info("draft_plan waiting for approval project_id=%s request_key=%s", self._project_id, request_key)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._approval_timeout
            while True:
                if self._stop_event is not None and self._stop_event.is_set():
                    self._service.delete(self._project_id, request_key)
                    logger.warning("draft_plan stopped by stop_event project_id=%s request_key=%s", self._project_id, request_key)
                    raise asyncio.CancelledError("Session stopped by user while waiting for plan approval")
                
                try:
                    await asyncio.wait_for(approval_event.wait(), timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    if loop.time() >= deadline:
                        raise asyncio.TimeoutError()

        except asyncio.TimeoutError:
            self._service.delete(self._project_id, request_key)
            logger.warning(
                "draft_plan TIMEOUT project_id=%s request_key=%s timeout=%.0fs — stopping session",
                self._project_id, request_key, self._approval_timeout,
            )
            if self._stop_event is not None:
                self._stop_event.set()
            raise ToolError(
                "Draft plan approval timed out after waiting for the user. "
                "The session has been stopped. Do NOT continue or implement anything."
            )
        except asyncio.CancelledError:
            self._service.delete(self._project_id, request_key)
            logger.info("draft_plan cancelled project_id=%s request_key=%s", self._project_id, request_key)
            raise

        logger.info("draft_plan approved project_id=%s request_key=%s", self._project_id, request_key)
        if self._approval_gate is not None:
            self._approval_gate.approve(request_key=request_key)
        await self._event_sink({"type": "draft_plan.approved", "request_key": request_key})
        self._service.delete(self._project_id, request_key)
        return CLIResult(output="Plan approved. Proceed with implementation.")
