from fastapi import APIRouter, Depends, HTTPException, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.agent_realtime import (
    AgentRealtimeErrorPayload,
    AgentSessionStatePayload,
    AgentSessionTicketRequest,
    AgentSessionTicketResponse,
)
from schemas.auth import UserResponse
from services.agent_realtime import get_agent_realtime_service
from services.projects import ProjectsService


router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


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


@router.websocket("/session/ws")
async def agent_session_websocket(websocket: WebSocket):
    ticket_value = websocket.query_params.get("ticket")
    await websocket.accept()

    ticket_service = get_agent_realtime_service()
    ticket = await ticket_service.consume_ticket(ticket_value or "")
    if ticket is None:
        await websocket.send_json(AgentRealtimeErrorPayload().model_dump())
        await websocket.close(code=1008)
        return

    await websocket.send_json(
        AgentSessionStatePayload(project_id=ticket.project_id).model_dump()
    )
