import asyncio
import logging
import os
from pathlib import Path

from anyio import ClosedResourceError
from fastapi import APIRouter, Depends, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.agent_realtime import (
    AgentLatestRunLogsResponse,
    AgentRealtimeErrorPayload,
    AgentSessionStatePayload,
    AgentSessionTicketRequest,
    AgentSessionTicketResponse,
)
from schemas.auth import UserResponse
from services.engineer_runtime import run_engineer_session
from services.agent_run_logs import AgentRunLogStore
from services.agent_realtime import get_agent_realtime_service
from services.agent_draft_plan import get_agent_draft_plan_service
from services.projects import ProjectsService


router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
logger = logging.getLogger(__name__)
_WORKSPACES_ROOT = Path(os.environ.get("ATOMS_WORKSPACES_ROOT", "/tmp/atoms_workspaces"))


@router.post("/session-ticket", response_model=AgentSessionTicketResponse)
async def issue_session_ticket(
    request: AgentSessionTicketRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await ProjectsService(db).get_by_id(request.project_id, user_id=current_user.id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    ticket = await get_agent_realtime_service().issue_ticket(
        db,
        user_id=current_user.id,
        project_id=request.project_id,
        model=request.model,
    )
    return AgentSessionTicketResponse(
        ticket=ticket.ticket,
        project_id=ticket.project_id,
        assistant_role="engineer",
        expires_at=ticket.expires_at,
    )


@router.get("/projects/{project_id}/latest-run-logs", response_model=AgentLatestRunLogsResponse)
async def latest_run_logs(
    project_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await ProjectsService(db).get_by_id(project_id, user_id=current_user.id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    latest = AgentRunLogStore(base_root=_WORKSPACES_ROOT).read_latest_run(
        user_id=str(current_user.id),
        project_id=project_id,
    )
    if latest is None or not latest.get("run_id"):
        raise HTTPException(status_code=404, detail="No run logs found")
    return AgentLatestRunLogsResponse(**latest)


@router.websocket("/session/ws")
async def agent_session_websocket(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    ticket_value = websocket.query_params.get("ticket")
    await websocket.accept()

    ticket_service = get_agent_realtime_service()
    ticket = await ticket_service.consume_ticket(db, ticket_value or "")
    if ticket is None:
        await websocket.send_json(AgentRealtimeErrorPayload().model_dump())
        await websocket.close(code=1008)
        return

    await websocket.send_json(
        AgentSessionStatePayload(project_id=ticket.project_id).model_dump()
    )

    client_attached = True

    async def send_to_client(payload: dict) -> bool:
        nonlocal client_attached
        if not client_attached:
            return False
        try:
            await websocket.send_json(payload)
            return True
        except (ClosedResourceError, RuntimeError, WebSocketDisconnect):
            client_attached = False
            logger.info("agent realtime websocket detached project_id=%s", ticket.project_id)
            return False

    async def emit_control_plane_event(event: dict) -> None:
        event_type = event.get("type")
        if event_type == "assistant":
            agent = str(event.get("agent") or "swe")
            content = str(event.get("content") or "")
            if content:
                await send_to_client(
                    {
                        "type": "assistant.delta",
                        "agent": agent,
                        "content": content,
                    }
                )
            await send_to_client(
                {
                    "type": "assistant.message_done",
                    "agent": agent,
                }
            )
            return

        if event_type in {
            "error",
            "progress",
            "terminal.log",
            "file.snapshot",
            "file.changed",
            "workspace_sync",
            "preview_ready",
            "preview_failed",
            "draft_plan.start",
            "draft_plan.item",
            "draft_plan.ready",
            "draft_plan.approved",
            "task_store.summary",
            "todo.updated",
        }:
            if event_type.startswith("draft_plan."):
                logger.info(
                    "ws:send %s project_id=%s request_key=%s",
                    event_type,
                    ticket.project_id,
                    event.get("request_key"),
                )
            await send_to_client(event)
            return

        # Forward any other event type directly to the client
        await send_to_client(event)

    current_task: asyncio.Task[None] | None = None
    current_stop_event: asyncio.Event | None = None

    async def run_current_session(payload: dict) -> None:
        nonlocal current_task, current_stop_event
        try:
            success = await run_engineer_session(
                db=db,
                user_id=str(ticket.user_id),
                project_id=ticket.project_id,
                prompt=str(payload.get("prompt") or ""),
                model=ticket.model,
                event_sink=emit_control_plane_event,
                stop_event=current_stop_event,
            )
            if current_stop_event is not None and current_stop_event.is_set():
                return

            await send_to_client(
                AgentSessionStatePayload(
                    status="completed" if success is not False else "failed",
                    project_id=ticket.project_id,
                ).model_dump()
            )
        except asyncio.CancelledError:
            logger.info("agent realtime websocket run cancelled project_id=%s", ticket.project_id)
            raise
        except Exception:
            logger.exception("agent realtime websocket run failed project_id=%s", ticket.project_id)
            if current_stop_event is None or not current_stop_event.is_set():
                await send_to_client(
                    AgentSessionStatePayload(
                        status="failed",
                        project_id=ticket.project_id,
                    ).model_dump()
                )
        finally:
            current_task = None
            current_stop_event = None

    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")

            if message_type == "run.stop":
                if current_stop_event is not None and not current_stop_event.is_set():
                    current_stop_event.set()
                if current_task is not None and not current_task.done():
                    current_task.cancel()
                await send_to_client({"type": "run.stopped"})
                continue

            if message_type == "user.approve_plan":
                request_key = str(message.get("request_key") or "")
                logger.info(
                    "ws:recv user.approve_plan project_id=%s request_key=%s",
                    ticket.project_id, request_key,
                )
                result = get_agent_draft_plan_service().approve(
                    project_id=ticket.project_id,
                    request_key=request_key,
                )
                if result is None:
                    logger.warning(
                        "ws:approve_plan no matching plan project_id=%s request_key=%s (already timed out?)",
                        ticket.project_id, request_key,
                    )
                continue

            if message_type != "user.message":
                continue

            if current_task is not None and not current_task.done():
                continue

            current_stop_event = asyncio.Event()
            await send_to_client(
                AgentSessionStatePayload(
                    status="running",
                    project_id=ticket.project_id,
                ).model_dump()
            )
            current_task = asyncio.create_task(run_current_session(message))
    except WebSocketDisconnect:
        client_attached = False
        logger.info("agent realtime websocket disconnected project_id=%s", ticket.project_id)
        if current_task is not None and not current_task.done():
            try:
                await current_task
            except asyncio.CancelledError:
                logger.info("agent realtime websocket run cancelled project_id=%s", ticket.project_id)
