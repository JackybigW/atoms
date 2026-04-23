import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from schemas.workspace_runtime import WorkspaceRuntimeStatusResponse
from services.preview_contract import load_preview_contract
from services.preview_sessions import build_preview_urls, can_reuse_preview_session, new_preview_session_fields
from services.project_workspace import ProjectWorkspaceService
from services.projects import ProjectsService
from services.sandbox_runtime import SandboxRuntimeService
from services.workspace_runtime_sessions import WorkspaceRuntimeSessionsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/workspace-runtime", tags=["workspace-runtime"])


def build_runtime_failure_report(
    *,
    service: str,
    phase: str,
    reason_code: str,
    detected_root: str = "",
    detected_entrypoint: str = "",
    attempted_command: str = "",
    stderr_tail: str = "",
    suggested_fix: str = "",
) -> dict:
    return {
        "service": service,
        "phase": phase,
        "reason_code": reason_code,
        "detected_root": detected_root,
        "detected_entrypoint": detected_entrypoint,
        "attempted_command": attempted_command,
        "stderr_tail": stderr_tail,
        "suggested_fix": suggested_fix,
    }

_WORKSPACES_ROOT = Path(os.environ.get("ATOMS_WORKSPACES_ROOT", "/tmp/atoms_workspaces"))


async def ensure_runtime_for_project(
    db: AsyncSession,
    user_id: str,
    project_id: int,
):
    """
    Resolve workspace paths, start/reuse the sandbox container, start the Vite
    dev server, wait for frontend readiness, then upsert a WorkspaceRuntimeSessions
    record and return it.
    """
    sessions_service = WorkspaceRuntimeSessionsService(db)
    workspace_service = ProjectWorkspaceService(base_root=_WORKSPACES_ROOT)
    paths = workspace_service.resolve_paths(user_id=user_id, project_id=project_id)

    existing = await sessions_service.get_by_project(user_id, project_id)
    if (
        existing
        and existing.status == "running"
        and can_reuse_preview_session(existing.preview_session_key, existing.preview_expires_at)
    ):
        # Verify the Vite dev server is actually alive
        if existing.frontend_port:
            sandbox_service_check = SandboxRuntimeService(project_root=_WORKSPACES_ROOT)
            container_name_check = existing.container_name
            if container_name_check:
                frontend_alive = await sandbox_service_check.wait_for_service(
                    container_name_check, 3000, timeout_seconds=3
                )
                
                # Verify backend if configured
                contract = load_preview_contract(paths.host_root)
                backend_alive = True
                if contract and contract.backend:
                    if not existing.backend_port:
                        backend_alive = False
                    else:
                        backend_alive = await sandbox_service_check.wait_for_service(
                            container_name_check, 8000, path=contract.backend.healthcheck_path, timeout_seconds=3
                        )

                if frontend_alive and backend_alive:
                    return existing
                else:
                    logger.warning(
                        "[ensure_runtime] cached session found but services dead (frontend=%s, backend=%s), forcing restart",
                        frontend_alive, backend_alive
                    )
            else:
                return existing
        else:
            return existing

    # Generate preview session key upfront so Vite --base path is correct.
    preview_fields = new_preview_session_fields()
    preview_session_key = preview_fields["preview_session_key"]
    preview_urls = build_preview_urls(preview_session_key)

    sandbox_service = SandboxRuntimeService(project_root=_WORKSPACES_ROOT)
    container_name = await sandbox_service.ensure_runtime(
        user_id=user_id,
        project_id=project_id,
        host_root=paths.host_root,
    )

    ports = await sandbox_service.get_runtime_ports(container_name)
    frontend_port = ports.get("frontend_port")
    backend_port = ports.get("backend_port")

    # Start the Vite dev server (and optional backend) inside the container.
    preview_env = {
        "ATOMS_PREVIEW_FRONTEND_BASE": preview_urls["preview_frontend_url"],
        "ATOMS_PREVIEW_BACKEND_BASE": preview_urls["preview_backend_url"],
        "VITE_ATOMS_PREVIEW_FRONTEND_BASE": preview_urls["preview_frontend_url"],
        "VITE_ATOMS_PREVIEW_BACKEND_BASE": preview_urls["preview_backend_url"],
    }
    returncode, stdout, stderr = await sandbox_service.start_preview_services(container_name, env=preview_env)
    if returncode != 0:
        message = stderr.strip() or stdout.strip() or "start-preview failed"
        raise RuntimeError(message)

    # Wait for frontend readiness.
    frontend_ready = False
    if frontend_port:
        frontend_ready = await sandbox_service.wait_for_service(container_name, 3000)

    # Check preview contract for optional backend service.
    contract = load_preview_contract(paths.host_root)
    backend_ready = False
    if contract and contract.backend and backend_port:
        backend_ready = await sandbox_service.wait_for_service(
            container_name,
            8000,
            path=contract.backend.healthcheck_path,
        )

    backend_configured = bool(contract and contract.backend)
    if backend_configured:
        if backend_ready:
            backend_status = "running"
        else:
            backend_status = "failed"
            logger.warning(
                "[ensure_runtime] backend healthcheck failed for project %s, marking session as degraded",
                project_id,
            )
    else:
        backend_status = "not_configured"

    overall_status = "running"
    if backend_configured and not backend_ready:
        overall_status = "degraded"

    session = await sessions_service.create(
        {
            "user_id": user_id,
            "project_id": project_id,
            "container_name": container_name,
            "status": overall_status,
            "preview_port": ports.get("preview_port"),
            "frontend_port": frontend_port,
            "backend_port": backend_port,
            **preview_fields,
            "frontend_status": "running" if frontend_ready else "starting",
            "backend_status": backend_status,
        }
    )
    return session


@router.post("/projects/{project_id}/ensure", response_model=WorkspaceRuntimeStatusResponse)
async def ensure_workspace_runtime(
    project_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    projects_service = ProjectsService(db)
    project = await projects_service.get_by_id(project_id, user_id=str(current_user.id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        session = await ensure_runtime_for_project(
            db=db,
            user_id=str(current_user.id),
            project_id=project_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if not session.preview_session_key:
        new_fields = new_preview_session_fields()
        for key, value in new_fields.items():
            setattr(session, key, value)
        await db.commit()
        await db.refresh(session)

    preview_urls = build_preview_urls(session.preview_session_key)

    return WorkspaceRuntimeStatusResponse(
        project_id=project_id,
        status=session.status,
        container_name=session.container_name,
        preview_session_key=session.preview_session_key,
        preview_expires_at=session.preview_expires_at,
        preview_frontend_url=preview_urls["preview_frontend_url"],
        preview_backend_url=preview_urls["preview_backend_url"],
        frontend_port=session.frontend_port,
        backend_port=session.backend_port,
        frontend_status=session.frontend_status,
        backend_status=session.backend_status,
    )
