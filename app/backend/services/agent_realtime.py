from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.auth import AccessTokenError, create_access_token, decode_access_token


_AGENT_REALTIME_TICKET_TYPE = "agent_realtime_session"


@dataclass(slots=True)
class AgentRealtimeTicket:
    ticket: str
    user_id: str
    project_id: int
    expires_at: datetime
    model: Optional[str] = None


class AgentRealtimeService:
    def __init__(self, ttl_minutes: int = 5):
        self._ttl_minutes = ttl_minutes

    async def issue_ticket(
        self,
        *,
        user_id: str,
        project_id: int,
        model: Optional[str] = None,
    ) -> AgentRealtimeTicket:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self._ttl_minutes)
        ticket = create_access_token(
            {
                "sub": str(user_id),
                "project_id": int(project_id),
                "model": model,
                "ticket_type": _AGENT_REALTIME_TICKET_TYPE,
            },
            expires_minutes=self._ttl_minutes,
        )
        return AgentRealtimeTicket(
            ticket=ticket,
            user_id=str(user_id),
            project_id=int(project_id),
            model=model,
            expires_at=expires_at,
        )

    async def consume_ticket(self, ticket: str) -> Optional[AgentRealtimeTicket]:
        try:
            payload = decode_access_token(ticket)
        except AccessTokenError:
            return None

        if payload.get("ticket_type") != _AGENT_REALTIME_TICKET_TYPE:
            return None

        project_id = payload.get("project_id")
        sub = payload.get("sub")
        expires_at_raw = payload.get("exp")
        if sub is None or project_id is None or expires_at_raw is None:
            return None

        try:
            expires_at = datetime.fromtimestamp(float(expires_at_raw), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None

        model = payload.get("model")
        if model is not None and not isinstance(model, str):
            return None

        try:
            parsed_project_id = int(project_id)
        except (TypeError, ValueError):
            return None

        return AgentRealtimeTicket(
            ticket=ticket,
            user_id=str(sub),
            project_id=parsed_project_id,
            model=model,
            expires_at=expires_at,
        )


_agent_realtime_service = AgentRealtimeService()


def get_agent_realtime_service() -> AgentRealtimeService:
    return _agent_realtime_service
