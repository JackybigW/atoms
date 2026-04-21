import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from openmanus_runtime.schema import Message
from openmanus_runtime.streaming import StreamingSWEAgent, build_agent_llm
from schemas.agent_runtime import AgentRunRequest
from services.engineer_runtime import run_engineer_session
from services.preview_contract import load_preview_contract
from services.preview_sessions import build_preview_urls, new_preview_session_fields
from services.project_workspace import ProjectWorkspaceService
from services.sandbox_runtime import SandboxRuntimeService
from services.workspace_runtime_sessions import WorkspaceRuntimeSessionsService
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_WORKSPACES_ROOT = Path(os.environ.get("ATOMS_WORKSPACES_ROOT", "/tmp/atoms_workspaces"))


def _get_workspace_service() -> ProjectWorkspaceService:
    return ProjectWorkspaceService(base_root=_WORKSPACES_ROOT)


def _get_sandbox_service() -> SandboxRuntimeService:
    return SandboxRuntimeService(project_root=_WORKSPACES_ROOT)


def _make_trace_id() -> str:
    return uuid4().hex[:12]


def _serialize_agent_history(messages: list[Message]) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for message in messages:
        item: dict[str, object] = {"role": message.role}
        if message.content is not None:
            item["content"] = message.content
        if message.thinking is not None:
            item["thinking"] = message.thinking
        if message.name is not None:
            item["name"] = message.name
        if message.tool_call_id is not None:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls is not None:
            item["tool_calls"] = [tool_call.model_dump() for tool_call in message.tool_calls]
        serialized.append(item)
    return serialized


@router.post("/run")
async def run_agent(
    request: AgentRunRequest,
    http_request: Request,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    trace_id = http_request.headers.get("x-trace-id") or _make_trace_id()
    logger.info(
        "[agent:%s] request received user_id=%s project_id=%s model=%s prompt_chars=%s",
        trace_id,
        current_user.id,
        request.project_id,
        request.model,
        len(request.prompt or ""),
    )

    async def emit(event: dict) -> None:
        event_with_trace = {"trace_id": trace_id, **event}
        logger.info("[agent:%s] emit event=%s", trace_id, event_with_trace.get("type"))
        await queue.put(event_with_trace)

    async def run_task() -> None:
        try:
            logger.info("[agent:%s] run_task started", trace_id)
            success = await run_engineer_session(
                db=db,
                user_id=str(current_user.id),
                project_id=request.project_id,
                prompt=request.prompt,
                model=request.model,
                event_sink=emit,
                trace_id=trace_id,
                workspace_service_factory=_get_workspace_service,
                sandbox_service_factory=_get_sandbox_service,
                agent_cls=StreamingSWEAgent,
                llm_builder=build_agent_llm,
                history_serializer=_serialize_agent_history,
                workspace_runtime_sessions_service_cls=WorkspaceRuntimeSessionsService,
                preview_session_fields_factory=new_preview_session_fields,
                preview_url_builder=build_preview_urls,
                preview_contract_loader=load_preview_contract,
            )
            logger.info("[agent:%s] run_task finished success=%s", trace_id, success)
        except Exception as exc:
            logger.exception("[agent:%s] run_task failed", trace_id)
            await emit(
                {
                    "type": "error",
                    "status": "failure",
                    "error": str(exc),
                }
            )
        finally:
            logger.info("[agent:%s] run_task finalizing", trace_id)
            await queue.put(None)

    async def event_generator() -> AsyncGenerator[dict, None]:
        task = asyncio.create_task(run_task())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    logger.info("[agent:%s] event_generator received sentinel", trace_id)
                    break
                logger.info("[agent:%s] yielding sse event=%s", trace_id, event["type"])
                yield {
                    "event": event["type"],
                    "data": json.dumps(event, ensure_ascii=False),
                }
        finally:
            logger.info("[agent:%s] event_generator awaiting task completion", trace_id)
            await task

    return EventSourceResponse(event_generator(), media_type="text/event-stream")
