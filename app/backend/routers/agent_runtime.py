import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from openmanus_runtime.streaming import StreamingSWEAgent, build_agent_llm
from openmanus_runtime.tool.bash import ContainerBashSession
from openmanus_runtime.tool.file_operators import ProjectFileOperator
from schemas.agent_runtime import AgentRunRequest
from services.project_files import Project_filesService
from services.project_workspace import ProjectWorkspaceService
from services.sandbox_runtime import SandboxRuntimeService
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


@router.post("/run")
async def run_agent(
    request: AgentRunRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def emit(event: dict) -> None:
        await queue.put(event)

    async def run_task() -> None:
        try:
            user_id = str(current_user.id)
            project_id = request.project_id

            # --- Resolve workspace paths and materialize project files ---
            workspace_service = _get_workspace_service()
            paths = workspace_service.resolve_paths(user_id=user_id, project_id=project_id)

            files_service = Project_filesService(db)
            files_result = await files_service.get_list(
                skip=0,
                limit=10000,
                user_id=user_id,
                query_dict={"project_id": project_id},
            )
            file_records = [
                {
                    "file_path": f.file_path,
                    "content": f.content,
                    "is_directory": f.is_directory,
                }
                for f in files_result["items"]
            ]
            workspace_service.materialize_files(paths.host_root, file_records)

            # --- Ensure sandbox container is running ---
            sandbox_service = _get_sandbox_service()
            try:
                container_name = await sandbox_service.ensure_runtime(
                    user_id=user_id,
                    project_id=project_id,
                    host_root=paths.host_root,
                )
            except Exception as exc:
                await emit({"type": "error", "status": "failure", "error": f"Could not start sandbox: {exc}"})
                await queue.put(None)
                return

            # --- Build project-scoped tools ---
            file_operator = ProjectFileOperator(
                host_root=paths.host_root,
                container_root=paths.container_root,
            )

            bash_session = ContainerBashSession(
                runtime_service=sandbox_service,
                container_name=container_name,
            )

            llm = build_agent_llm(request.model)
            agent = StreamingSWEAgent.build_for_workspace(
                llm=llm,
                event_emitter=emit,
                file_operator=file_operator,
                bash_session=bash_session,  # always a ContainerBashSession now
            )

            await emit(
                {
                    "type": "session",
                    "agent": agent.name,
                    "workspace_root": str(paths.host_root),
                    "status": "started",
                }
            )

            task_prompt = (
                f"You must work inside this workspace root: /workspace\n"
                "Use absolute paths starting with /workspace for file edits, "
                "and change into this directory before running bash commands.\n\n"
                f"User request:\n{request.prompt}"
            )
            result = await agent.run(task_prompt)

            # --- Snapshot and sync changed files back to DB ---
            try:
                snapshot = workspace_service.snapshot_files(paths.host_root)
                changed_paths: list[str] = []

                for rel_path, file_info in snapshot.items():
                    file_name = Path(rel_path).name
                    # Try to find existing record for this path
                    existing_list = await files_service.get_list(
                        skip=0,
                        limit=1,
                        user_id=user_id,
                        query_dict={"project_id": project_id, "file_path": rel_path},
                    )
                    existing = existing_list["items"]
                    if existing:
                        existing_record = existing[0]
                        if existing_record.content != file_info["content"]:
                            await files_service.update(
                                existing_record.id,
                                {"content": file_info["content"]},
                                user_id=user_id,
                            )
                            changed_paths.append(rel_path)
                    else:
                        await files_service.create(
                            {
                                "project_id": project_id,
                                "file_path": rel_path,
                                "file_name": file_name,
                                "content": file_info["content"],
                                "is_directory": False,
                            },
                            user_id=user_id,
                        )
                        changed_paths.append(rel_path)

                await emit(
                    {
                        "type": "workspace_sync",
                        "changed_files": changed_paths,
                    }
                )
            except Exception as sync_exc:
                logger.warning("Workspace sync failed: %s", sync_exc)

            await emit(
                {
                    "type": "done",
                    "agent": agent.name,
                    "status": "success",
                    "result": result,
                }
            )
        except Exception as exc:
            await emit(
                {
                    "type": "error",
                    "status": "failure",
                    "error": str(exc),
                }
            )
        finally:
            await queue.put(None)

    async def event_generator() -> AsyncGenerator[dict, None]:
        task = asyncio.create_task(run_task())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {
                    "event": event["type"],
                    "data": json.dumps(event, ensure_ascii=False),
                }
        finally:
            await task

    return EventSourceResponse(event_generator(), media_type="text/event-stream")
