import asyncio
import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.dml import Update
from starlette.websockets import WebSocketDisconnect

from core.database import Base
from models.agent_realtime_tickets import AgentRealtimeTickets
from models.projects import Projects
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


async def _create_schema(engine):
    import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_projects(session_maker):
    async with session_maker() as db:
        db.add_all(
            [
                Projects(id=42, user_id="user-1", name="Owned project"),
                Projects(id=43, user_id="other-user", name="Other project"),
            ]
        )
        await db.commit()


def _build_environment(tmp_path: Path, monkeypatch):
    _configure_jwt_env(monkeypatch)

    db_path = tmp_path / "agent_realtime.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    asyncio.run(_create_schema(engine))
    asyncio.run(_seed_projects(session_maker))

    service = AgentRealtimeService()

    from dependencies.auth import get_current_user
    from core.database import get_db

    async def _fake_get_current_user():
        return _FakeCurrentUser()

    async def _fake_get_db():
        async with session_maker() as db:
            yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _fake_get_current_user
    app.dependency_overrides[get_db] = _fake_get_db
    monkeypatch.setattr("routers.agent_realtime.get_agent_realtime_service", lambda: service)

    return app, engine, session_maker, service


def test_issue_session_ticket_and_reject_invalid_websocket(monkeypatch, tmp_path):
    app, engine, session_maker, service = _build_environment(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.post("/api/v1/agent/session-ticket", json={"project_id": 42, "model": "gpt-4.1"})
        assert response.status_code == 200

        payload = response.json()
        assert payload["project_id"] == 42
        assert payload["assistant_role"] == "engineer"
        ticket = payload["ticket"]
        assert isinstance(ticket, str)
        assert ticket

        async def _load_ticket_row():
            async with session_maker() as db:
                result = await db.execute(select(AgentRealtimeTickets))
                return result.scalar_one()

        stored_ticket = asyncio.run(_load_ticket_row())
        assert stored_ticket.ticket_hash == hashlib.sha256(ticket.encode("utf-8")).hexdigest()
        assert stored_ticket.ticket_hash != ticket

        async def _consume():
            async with session_maker() as db:
                return await service.consume_ticket(db, ticket)

        consumed = asyncio.run(_consume())
        assert consumed is not None
        assert consumed.model == "gpt-4.1"
        assert consumed.project_id == 42

        response2 = client.post("/api/v1/agent/session-ticket", json={"project_id": 42, "model": "gpt-4.1"})
        assert response2.status_code == 200
        websocket_ticket = response2.json()["ticket"]

        with client.websocket_connect("/api/v1/agent/session/ws?ticket=invalid-ticket") as websocket:
            message = websocket.receive_json()
            assert message == {"type": "error", "code": "invalid_ticket"}
            try:
                websocket.receive_json()
                raise AssertionError("expected websocket to close after invalid ticket")
            except WebSocketDisconnect:
                pass

        with client.websocket_connect(f"/api/v1/agent/session/ws?ticket={websocket_ticket}") as websocket:
            message = websocket.receive_json()
            assert message == {
                "type": "session.state",
                "status": "idle",
                "project_id": 42,
                "assistant_role": "engineer",
            }

    asyncio.run(engine.dispose())


def test_issue_session_ticket_denies_unowned_project(monkeypatch, tmp_path):
    app, engine, _, _ = _build_environment(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.post("/api/v1/agent/session-ticket", json={"project_id": 43, "model": "gpt-4.1"})
        assert response.status_code == 404

    asyncio.run(engine.dispose())


def test_agent_realtime_service_rejects_invalid_reuse_and_expired_tickets(monkeypatch, tmp_path):
    _, engine, session_maker, service = _build_environment(tmp_path, monkeypatch)

    async def _run():
        async with session_maker() as db:
            ticket = await service.issue_ticket(db, user_id="user-1", project_id=42, model="gpt-4.1")
            assert ticket.model == "gpt-4.1"

            first = await service.consume_ticket(db, ticket.ticket)
            assert first is not None
            assert first.model == "gpt-4.1"

            second = await service.consume_ticket(db, ticket.ticket)
            assert second is None

            assert await service.consume_ticket(db, "not-a-valid-ticket") is None

        expired_service = AgentRealtimeService(ttl_minutes=-1)
        async with session_maker() as db:
            expired_ticket = await expired_service.issue_ticket(
                db,
                user_id="user-1",
                project_id=42,
                model="gpt-4.1",
            )
            assert await expired_service.consume_ticket(db, expired_ticket.ticket) is None

            result = await db.execute(
                select(AgentRealtimeTickets).where(
                    AgentRealtimeTickets.ticket_hash == expired_service._hash_ticket(expired_ticket.ticket)
                )
            )
            assert result.scalar_one_or_none() is None

    asyncio.run(_run())
    asyncio.run(engine.dispose())


def test_agent_realtime_service_fallback_double_consume_only_succeeds_once(monkeypatch, tmp_path):
    _, engine, session_maker, service = _build_environment(tmp_path, monkeypatch)

    monkeypatch.setattr(service, "_supports_update_returning", lambda db: False, raising=False)

    def _raise_if_returning(*args, **kwargs):
        raise AssertionError("fallback path must not call RETURNING")

    monkeypatch.setattr(Update, "returning", _raise_if_returning)

    async def _run():
        async with session_maker() as db:
            ticket = await service.issue_ticket(db, user_id="user-1", project_id=42, model="gpt-4.1")

        async def _consume_once():
            async with session_maker() as db:
                return await service.consume_ticket(db, ticket.ticket)

        first, second = await asyncio.gather(_consume_once(), _consume_once())
        successes = [result for result in (first, second) if result is not None]
        failures = [result for result in (first, second) if result is None]

        assert len(successes) == 1
        assert len(failures) == 1
        assert successes[0].model == "gpt-4.1"

    asyncio.run(_run())
    asyncio.run(engine.dispose())


def test_issue_session_ticket_rejects_overlong_model(monkeypatch, tmp_path):
    app, engine, _, _ = _build_environment(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/session-ticket",
            json={"project_id": 42, "model": "g" * 65},
        )
        assert response.status_code == 422

    asyncio.run(engine.dispose())


def test_websocket_user_message_streams_engineer_reply(monkeypatch, tmp_path):
    app, engine, _, _ = _build_environment(tmp_path, monkeypatch)

    async def fake_run_engineer_session(*, event_sink, **kwargs):
        await event_sink({"type": "assistant", "agent": "swe", "content": "Working on auth flow"})

    monkeypatch.setattr("routers.agent_realtime.run_engineer_session", fake_run_engineer_session, raising=False)

    with TestClient(app) as client:
        ticket = client.post("/api/v1/agent/session-ticket", json={"project_id": 42, "model": "gpt-4.1"}).json()[
            "ticket"
        ]

        with client.websocket_connect(f"/api/v1/agent/session/ws?ticket={ticket}") as websocket:
            idle_state = websocket.receive_json()
            assert idle_state == {
                "type": "session.state",
                "status": "idle",
                "project_id": 42,
                "assistant_role": "engineer",
            }

            websocket.send_json({"type": "user.message", "project_id": 42, "prompt": "build auth"})

            running_state = websocket.receive_json()
            assert running_state == {
                "type": "session.state",
                "status": "running",
                "project_id": 42,
                "assistant_role": "engineer",
            }

            assistant_delta = websocket.receive_json()
            assert assistant_delta == {
                "type": "assistant.delta",
                "agent": "swe",
                "content": "Working on auth flow",
            }

            assistant_done = websocket.receive_json()
            assert assistant_done == {
                "type": "assistant.message_done",
                "agent": "swe",
            }

            completed_state = websocket.receive_json()
            assert completed_state == {
                "type": "session.state",
                "status": "completed",
                "project_id": 42,
                "assistant_role": "engineer",
            }

    asyncio.run(engine.dispose())


def test_websocket_run_stop_emits_run_stopped(monkeypatch, tmp_path):
    app, engine, _, _ = _build_environment(tmp_path, monkeypatch)

    async def fake_long_running_engineer_session(*, stop_event=None, **kwargs):
        assert stop_event is not None
        while not stop_event.is_set():
            await asyncio.sleep(0.01)
        return False

    monkeypatch.setattr(
        "routers.agent_realtime.run_engineer_session",
        fake_long_running_engineer_session,
        raising=False,
    )

    with TestClient(app) as client:
        ticket = client.post("/api/v1/agent/session-ticket", json={"project_id": 42, "model": "gpt-4.1"}).json()[
            "ticket"
        ]

        with client.websocket_connect(f"/api/v1/agent/session/ws?ticket={ticket}") as websocket:
            idle_state = websocket.receive_json()
            assert idle_state == {
                "type": "session.state",
                "status": "idle",
                "project_id": 42,
                "assistant_role": "engineer",
            }

            websocket.send_json({"type": "user.message", "project_id": 42, "prompt": "build auth"})

            running_state = websocket.receive_json()
            assert running_state == {
                "type": "session.state",
                "status": "running",
                "project_id": 42,
                "assistant_role": "engineer",
            }

            websocket.send_json({"type": "run.stop"})
            stopped = websocket.receive_json()
            assert stopped == {"type": "run.stopped"}

    asyncio.run(engine.dispose())
