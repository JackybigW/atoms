import asyncio
from datetime import datetime, timezone

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.workspace_runtime_sessions import WorkspaceRuntimeSessionsService


router = APIRouter(tags=["preview-gateway"])

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
}


def _validate_preview_session(session):
    if session is None or session.status not in {"running", "starting"}:
        raise HTTPException(status_code=404, detail="Preview runtime not found")
    if session.preview_expires_at:
        expires_at = session.preview_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=404, detail="Preview session expired")
    return session


async def _proxy_http_request(upstream: str, request: Request):
    timeout = httpx.Timeout(30.0, connect=5.0)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        try:
            response = await client.request(
                request.method,
                upstream,
                params=request.query_params,
                content=await request.body(),
                headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Preview timed out")
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Preview unavailable")
    headers = {k: v for k, v in response.headers.items() if k.lower() not in HOP_BY_HOP}
    return response.status_code, headers, response.content


@router.api_route(
    "/preview/{preview_session_key}/frontend/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_preview_frontend(
    preview_session_key: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    session = _validate_preview_session(
        await WorkspaceRuntimeSessionsService(db).get_by_preview_session_key(preview_session_key)
    )
    if not session.frontend_port:
        raise HTTPException(status_code=404, detail="Preview frontend not available")
    upstream_path = f"/preview/{preview_session_key}/frontend/{path}".rstrip("/")
    if not path:
        upstream_path = f"/preview/{preview_session_key}/frontend/"
    upstream = f"http://127.0.0.1:{session.frontend_port}{upstream_path}"
    status_code, headers, content = await _proxy_http_request(upstream, request)
    return Response(content=content, status_code=status_code, headers=headers)


@router.api_route(
    "/preview/{preview_session_key}/backend/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_preview_backend(
    preview_session_key: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    session = _validate_preview_session(
        await WorkspaceRuntimeSessionsService(db).get_by_preview_session_key(preview_session_key)
    )
    if not session.backend_port:
        raise HTTPException(status_code=404, detail="Preview backend not available")
    upstream = f"http://127.0.0.1:{session.backend_port}/{path}"
    status_code, headers, content = await _proxy_http_request(upstream, request)
    return Response(content=content, status_code=status_code, headers=headers)


def build_preview_websocket_upstream(
    session,
    service: str,
    path: str,
    query_string: str = "",
) -> str:
    port = session.frontend_port if service == "frontend" else session.backend_port
    normalized_path = f"/{path}" if path else "/"
    suffix = f"?{query_string}" if query_string else ""
    return f"ws://127.0.0.1:{port}{normalized_path}{suffix}"


def _parse_websocket_subprotocols(header_value: str | None) -> list[str]:
    if not header_value:
        return []
    return [part.strip() for part in header_value.split(",") if part.strip()]


async def _proxy_preview_websocket_common(
    websocket: WebSocket,
    preview_session_key: str,
    service: str,
    path: str,
    db: AsyncSession,
):
    session = _validate_preview_session(
        await WorkspaceRuntimeSessionsService(db).get_by_preview_session_key(preview_session_key)
    )
    if service not in {"frontend", "backend"}:
        await websocket.close(code=1011, reason="Invalid preview service")
        return
    upstream_url = build_preview_websocket_upstream(session, service, path, websocket.url.query)
    requested_subprotocols = _parse_websocket_subprotocols(
        websocket.headers.get("sec-websocket-protocol")
    )
    accepted_subprotocol = requested_subprotocols[0] if requested_subprotocols else None
    await websocket.accept(subprotocol=accepted_subprotocol)
    try:
        connect_kwargs = {}
        if requested_subprotocols:
            connect_kwargs["subprotocols"] = requested_subprotocols
        async with websockets.connect(upstream_url, **connect_kwargs) as upstream:
            async def client_to_upstream():
                while True:
                    message = await websocket.receive()
                    if "text" in message:
                        await upstream.send(message["text"])
                    elif "bytes" in message:
                        await upstream.send(message["bytes"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            try:
                await asyncio.gather(client_to_upstream(), upstream_to_client())
            except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
                pass
    except (OSError, websockets.exceptions.WebSocketException):
        await websocket.close(code=1011, reason="Preview upstream unavailable")


@router.websocket("/preview/{preview_session_key}/{service}/ws/{path:path}")
async def proxy_preview_websocket(
    websocket: WebSocket,
    preview_session_key: str,
    service: str,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    await _proxy_preview_websocket_common(websocket, preview_session_key, service, path, db)


@router.websocket("/preview/{preview_session_key}/{service}/")
async def proxy_preview_websocket_root(
    websocket: WebSocket,
    preview_session_key: str,
    service: str,
    db: AsyncSession = Depends(get_db),
):
    await _proxy_preview_websocket_common(websocket, preview_session_key, service, "", db)
