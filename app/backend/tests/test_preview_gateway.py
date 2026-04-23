from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.preview_gateway import (
    router,
    _parse_websocket_subprotocols,
    build_preview_websocket_upstream,
)


class _FakeSession:
    status = "running"
    preview_session_key = "preview-session-123"
    preview_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    frontend_port = 3100
    backend_port = 8100
    frontend_status = "running"
    backend_status = "running"


class _FakeSessionsService:
    async def get_by_preview_session_key(self, preview_session_key: str):
        if preview_session_key == "expired-session":
            session = _FakeSession()
            session.preview_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            return session
        return _FakeSession() if preview_session_key == "preview-session-123" else None


def _make_client(monkeypatch):
    from core.database import get_db

    async def _fake_get_db():
        yield object()

    monkeypatch.setattr(
        "routers.preview_gateway.WorkspaceRuntimeSessionsService",
        lambda db: _FakeSessionsService(),
    )

    async def _fake_proxy_request(upstream, request):
        return 200, {"content-type": "text/plain"}, b"ok:" + upstream.encode("utf-8")

    monkeypatch.setattr("routers.preview_gateway._proxy_http_request", _fake_proxy_request)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = _fake_get_db
    return TestClient(app)


def test_frontend_preview_gateway_uses_frontend_port(monkeypatch):
    client = _make_client(monkeypatch)
    response = client.get("/preview/preview-session-123/frontend/src/main.tsx")
    assert response.status_code == 200
    assert response.text == "ok:http://127.0.0.1:3100/preview/preview-session-123/frontend/src/main.tsx"


def test_backend_preview_gateway_uses_backend_port(monkeypatch):
    client = _make_client(monkeypatch)
    response = client.post("/preview/preview-session-123/backend/api/health")
    assert response.status_code == 200
    assert response.text == "ok:http://127.0.0.1:8100/api/health"


def test_expired_preview_session_key_returns_404(monkeypatch):
    client = _make_client(monkeypatch)
    response = client.get("/preview/expired-session/frontend/")
    assert response.status_code == 404


def test_unknown_preview_session_key_returns_404(monkeypatch):
    client = _make_client(monkeypatch)
    response = client.get("/preview/unknown-key/frontend/")
    assert response.status_code == 404


def test_build_preview_websocket_upstream_uses_service_port():
    session = _FakeSession()
    assert build_preview_websocket_upstream(session, "frontend", "hmr") == "ws://127.0.0.1:3100/hmr"
    assert build_preview_websocket_upstream(session, "backend", "ws/events") == "ws://127.0.0.1:8100/ws/events"
    assert build_preview_websocket_upstream(session, "frontend", "", "token=abc") == "ws://127.0.0.1:3100/?token=abc"


def test_parse_websocket_subprotocols_splits_header_values():
    assert _parse_websocket_subprotocols(None) == []
    assert _parse_websocket_subprotocols("vite-hmr") == ["vite-hmr"]
    assert _parse_websocket_subprotocols("vite-hmr, other-proto") == ["vite-hmr", "other-proto"]
