import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from routers.agent_realtime import router
from services.agent_realtime import AgentRealtimeService


class _FakeCurrentUser:
    def __init__(self, user_id: str = "user-1"):
        self.id = user_id
        self.email = "test@example.com"
        self.name = "Test User"
        self.role = "user"


def _configure_jwt_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")


def _make_client(monkeypatch, db=None):
    _configure_jwt_env(monkeypatch)
    from dependencies.auth import get_current_user
    from core.database import get_db

    async def _fake_get_current_user():
        return _FakeCurrentUser()

    async def _fake_get_db():
        yield db or _FakeDB()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _fake_get_current_user
    app.dependency_overrides[get_db] = _fake_get_db
    return TestClient(app)


class _FakeProject:
    def __init__(self, owner_id: str):
        self.user_id = owner_id


class _FakeProjectsService:
    def __init__(self, db):
        self.db = db
        self.requests: list[tuple[int, str]] = []

    async def get_by_id(self, obj_id: int, user_id: str | None = None):
        self.requests.append((obj_id, user_id or ""))
        if obj_id == 42 and user_id == "user-1":
            return _FakeProject(owner_id=user_id)
        return None


class _FakeDB:
    pass


def test_issue_session_ticket_and_reject_invalid_websocket(monkeypatch):
    _configure_jwt_env(monkeypatch)
    service = AgentRealtimeService()
    monkeypatch.setattr("routers.agent_realtime.ProjectsService", _FakeProjectsService)
    monkeypatch.setattr("routers.agent_realtime.get_agent_realtime_service", lambda: service)
    client = _make_client(monkeypatch)

    response = client.post("/api/v1/agent/session-ticket", json={"project_id": 42, "model": "gpt-4.1"})
    assert response.status_code == 200

    payload = response.json()
    assert payload["project_id"] == 42
    assert payload["assistant_role"] == "engineer"
    ticket = payload["ticket"]
    assert isinstance(ticket, str)
    assert ticket

    consumed = asyncio.run(service.consume_ticket(ticket))
    assert consumed is not None
    assert consumed.model == "gpt-4.1"
    assert consumed.project_id == 42

    with client.websocket_connect("/api/v1/agent/session/ws?ticket=invalid-ticket") as websocket:
        message = websocket.receive_json()
        assert message == {"type": "error", "code": "invalid_ticket"}
        try:
            websocket.receive_json()
            raise AssertionError("expected websocket to close after invalid ticket")
        except WebSocketDisconnect:
            pass

    with client.websocket_connect(f"/api/v1/agent/session/ws?ticket={ticket}") as websocket:
        message = websocket.receive_json()
        assert message == {
            "type": "session.state",
            "status": "idle",
            "project_id": 42,
            "assistant_role": "engineer",
        }


def test_issue_session_ticket_denies_unowned_project(monkeypatch):
    _configure_jwt_env(monkeypatch)
    monkeypatch.setattr("routers.agent_realtime.ProjectsService", _FakeProjectsService)
    client = _make_client(monkeypatch)

    response = client.post("/api/v1/agent/session-ticket", json={"project_id": 43, "model": "gpt-4.1"})

    assert response.status_code == 404


def test_agent_realtime_service_rejects_invalid_or_expired_tickets(monkeypatch):
    _configure_jwt_env(monkeypatch)

    invalid_service = AgentRealtimeService()
    assert asyncio.run(invalid_service.consume_ticket("not-a-valid-ticket")) is None

    expired_service = AgentRealtimeService(ttl_minutes=-1)

    async def _issue_and_consume_expired():
        ticket = await expired_service.issue_ticket(user_id="user-1", project_id=42, model="gpt-4.1")
        return await expired_service.consume_ticket(ticket.ticket)

    assert asyncio.run(_issue_and_consume_expired()) is None
