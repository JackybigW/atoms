from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
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
    if session.preview_expires_at and session.preview_expires_at <= datetime.now(timezone.utc):
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
    upstream = f"http://127.0.0.1:{session.frontend_port}/{path}"
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
