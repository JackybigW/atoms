import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from openmanus_runtime.streaming import StreamingSWEAgent, build_agent_llm
from openmanus_runtime.tool.bash import ContainerBashSession
from openmanus_runtime.tool.file_operators import ProjectFileOperator
from services.messages import MessagesService
from services.preview_contract import load_preview_contract
from services.preview_sessions import build_preview_urls, new_preview_session_fields
from services.project_files import Project_filesService
from services.project_workspace import ProjectWorkspaceService
from services.sandbox_runtime import SandboxRuntimeService
from services.workspace_event_emitter import WorkspaceEventEmitter
from services.workspace_runtime_sessions import WorkspaceRuntimeSessionsService
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_WORKSPACES_ROOT = Path(os.environ.get("ATOMS_WORKSPACES_ROOT", "/tmp/atoms_workspaces"))

from services.agent_skill_loader import AgentSkillLoader  # noqa: E402

_skill_loader = AgentSkillLoader()

EventSink = Callable[[dict[str, Any]], Awaitable[None]]
WorkspaceServiceFactory = Callable[[], ProjectWorkspaceService]
SandboxServiceFactory = Callable[[], SandboxRuntimeService]
AgentHistorySerializer = Callable[[list[Any]], list[dict[str, object]]]
PreviewUrlBuilder = Callable[[str], dict[str, str]]
PreviewSessionFieldsFactory = Callable[[], dict[str, object]]
PreviewContractLoader = Callable[[Path], Any]


def _default_workspace_service_factory() -> ProjectWorkspaceService:
    return ProjectWorkspaceService(base_root=_WORKSPACES_ROOT)


def _default_sandbox_service_factory() -> SandboxRuntimeService:
    return SandboxRuntimeService(project_root=_WORKSPACES_ROOT)


def _log_prefix(trace_id: str | None) -> str:
    return f"[agent:{trace_id}]" if trace_id else "[agent]"


async def run_engineer_session(
    *,
    db: AsyncSession,
    user_id: str,
    project_id: int,
    prompt: str,
    model: str | None,
    event_sink: EventSink,
    trace_id: str | None = None,
    workspace_service_factory: WorkspaceServiceFactory | None = None,
    sandbox_service_factory: SandboxServiceFactory | None = None,
    agent_cls=StreamingSWEAgent,
    llm_builder=build_agent_llm,
    history_serializer: AgentHistorySerializer | None = None,
    workspace_runtime_sessions_service_cls=WorkspaceRuntimeSessionsService,
    preview_session_fields_factory: PreviewSessionFieldsFactory = new_preview_session_fields,
    preview_url_builder: PreviewUrlBuilder = build_preview_urls,
    preview_contract_loader: PreviewContractLoader = load_preview_contract,
    stop_event: asyncio.Event | None = None,
) -> bool:
    prefix = _log_prefix(trace_id)
    workspace_service_factory = workspace_service_factory or _default_workspace_service_factory
    sandbox_service_factory = sandbox_service_factory or _default_sandbox_service_factory

    try:
        logger.info("%s run_engineer_session started", prefix)
        workspace_events = WorkspaceEventEmitter(event_sink)

        if stop_event is not None and stop_event.is_set():
            logger.info("%s run_engineer_session skipped because stop_event already set", prefix)
            return False

        workspace_service = workspace_service_factory()
        paths = workspace_service.resolve_paths(user_id=user_id, project_id=project_id)
        logger.info("%s workspace resolved host_root=%s", prefix, paths.host_root)

        files_service = Project_filesService(db)
        files_result = await files_service.get_list(
            skip=0,
            limit=10000,
            user_id=user_id,
            query_dict={"project_id": project_id},
        )
        file_records = [
            {
                "file_path": record.file_path,
                "content": record.content,
                "is_directory": record.is_directory,
            }
            for record in files_result["items"]
        ]
        workspace_service.materialize_files(paths.host_root, file_records)
        logger.info("%s materialized %s project files", prefix, len(file_records))

        from services.agent_bootstrap import classify_user_request_async

        bootstrap_ctx = await classify_user_request_async(prompt)

        messages_service = MessagesService(db)
        message_history_result = await messages_service.get_list(
            skip=0,
            limit=200,
            user_id=user_id,
            query_dict={"project_id": project_id},
            sort="created_at",
        )
        persisted_history = [
            {
                "role": message.role,
                "content": message.content,
                "agent": message.agent,
                "model": message.model,
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in message_history_result["items"]
        ]
        logger.info(
            "%s persisted message history count=%s history=%s",
            prefix,
            len(persisted_history),
            json.dumps(persisted_history, ensure_ascii=False),
        )

        sandbox_service = sandbox_service_factory()
        try:
            container_name = await sandbox_service.ensure_runtime(
                user_id=user_id,
                project_id=project_id,
                host_root=paths.host_root,
            )
            logger.info("%s sandbox ready container=%s", prefix, container_name)
        except Exception as exc:
            logger.exception("%s sandbox startup failed", prefix)
            await event_sink({"type": "error", "status": "failure", "error": f"Could not start sandbox: {exc}"})
            return False

        from services.approval_gate import ApprovalGate
        gate = ApprovalGate(requires_approval=bootstrap_ctx.requires_draft_plan)
        file_operator = ProjectFileOperator(
            host_root=paths.host_root,
            container_root=paths.container_root,
            event_sink=workspace_events.emit,
            approval_gate=gate,
        )
        bash_session = ContainerBashSession(
            runtime_service=sandbox_service,
            container_name=container_name,
        )
        llm = llm_builder(model)
        logger.info("%s llm constructed model=%s", prefix, model)
        agent = agent_cls.build_for_workspace(
            llm=llm,
            event_emitter=event_sink,
            file_operator=file_operator,
            bash_session=bash_session,
        )
        logger.info("%s agent built name=%s", prefix, agent.name)

        from openmanus_runtime.tool.draft_plan import DraftPlanTool
        from openmanus_runtime.tool.load_skill import LoadSkillTool
        from openmanus_runtime.tool.todo_write import TodoWriteTool
        from services.agent_draft_plan import get_agent_draft_plan_service
        from services.agent_task_store import AgentTaskStore

        draft_plan_service = get_agent_draft_plan_service()
        draft_plan_tool = DraftPlanTool.create(
            event_sink=event_sink,
            service=draft_plan_service,
            project_id=project_id,
            approval_gate=gate,
        )
        load_skill_tool = LoadSkillTool.create(loader=_skill_loader)
        todo_write_tool = TodoWriteTool.create(
            file_operator=file_operator,
            event_sink=event_sink,
            task_store_factory=lambda: AgentTaskStore(db),
            project_id=project_id,
            request_key=f"project-{project_id}",
            approval_gate=gate,
        )
        if hasattr(agent, "available_tools") and agent.available_tools is not None:
            agent.available_tools.add_tool(draft_plan_tool)
            agent.available_tools.add_tool(load_skill_tool)
            agent.available_tools.add_tool(todo_write_tool)

        await event_sink(
            {
                "type": "session",
                "agent": agent.name,
                "workspace_root": str(paths.host_root),
                "status": "started",
            }
        )

        readme_block = ""
        if bootstrap_ctx.requires_backend_readme:
            readme_path = paths.host_root / "app" / "backend" / "README.md"
            try:
                readme_content = readme_path.read_text(encoding="utf-8").strip()
                if readme_content:
                    readme_block = (
                        "## Backend README (mandatory reading before implementing backend features)\n\n"
                        f"{readme_content}\n\n---\n\n"
                    )
                    logger.info("%s injected backend README chars=%d", prefix, len(readme_content))
            except OSError:
                logger.debug("%s backend README not found at %s", prefix, readme_path)

        task_prompt = readme_block + (
            f"You must work inside this workspace root: /workspace\n"
            "Use absolute paths starting with /workspace for file edits, "
            "and change into this directory before running bash commands.\n"
            "After file edits, the backend will automatically run "
            "/usr/local/bin/start-preview to launch the preview services.\n"
            "The preview configuration is declared in .atoms/preview.json "
            "using this exact JSON shape:\n"
            '{\n'
            '  "frontend": {"command": "pnpm run dev -- --host 0.0.0.0 --port 3000", "healthcheck_path": "/"},\n'
            '  "backend": {"command": "node server/index.js", "healthcheck_path": "/health"}\n'
            '}\n'
            'The "backend" object is optional when the app is frontend-only.\n'
            "If your app has a backend API, set the VITE_ATOMS_PREVIEW_BACKEND_BASE "
            "environment variable in the frontend so it can reach the backend.\n"
            "Do not start the preview services yourself; focus on writing code and "
            "one-off verification commands.\n\n"
            "## Orchestration Workflow\n\n"
            "For implementation requests:\n"
            "1. Call `draft_plan` ALONE with a short numbered list (3-7 items) — wait for user approval\n"
            "2. After approval, write a detailed implementation plan to "
            "`docs/plans/{YYYY-MM-DD}-{feature-slug}.md` using str_replace_editor. "
            "This plan must expand each approved item into concrete steps with specific file paths, "
            "what to create/modify, and execution order.\n"
            "3. Call `todo_write` with the implementation checklist (max 8 items, one in_progress at a time)\n"
            "4. Implement the plan step by step\n"
            "5. Run verification commands when done\n\n"
            f"User request:\n{prompt}"
        )
        skill_listing = _skill_loader.describe_available()
        if skill_listing:
            skill_summary = "\n".join(f"- {name}: {desc}" for name, desc in skill_listing.items())
            task_prompt = task_prompt.replace(
                f"User request:\n{prompt}",
                f"Available skills (use load_skill tool to get full content):\n{skill_summary}\n\nUser request:\n{prompt}",
            )
        logger.info("%s agent.run started", prefix)
        result = await agent.run(task_prompt)
        logger.info("%s agent.run completed", prefix)

        if stop_event is not None and stop_event.is_set():
            logger.info("%s run_engineer_session stopped after agent.run", prefix)
            return False

        if history_serializer is not None:
            agent_messages = getattr(agent, "messages", [])
            logger.info(
                "%s agent memory history=%s",
                prefix,
                json.dumps(history_serializer(agent_messages), ensure_ascii=False),
            )

        try:
            snapshot = workspace_service.snapshot_files(paths.host_root)
            changed_paths: list[str] = []
            logger.info("%s snapshot captured files=%s", prefix, len(snapshot))

            for rel_path, file_info in snapshot.items():
                file_name = Path(rel_path).name
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

            await event_sink({"type": "workspace_sync", "changed_files": changed_paths})
            logger.info("%s workspace sync updated_files=%s", prefix, len(changed_paths))
        except Exception as sync_exc:
            logger.warning("%s workspace sync failed: %s", prefix, sync_exc)

        if stop_event is not None and stop_event.is_set():
            logger.info("%s run_engineer_session stopped before preview startup", prefix)
            return False

        try:
            sessions_service = workspace_runtime_sessions_service_cls(db)
            existing_session = await sessions_service.get_by_project(user_id=user_id, project_id=project_id)
            if existing_session and existing_session.preview_session_key:
                session_key_fields: dict[str, object] = {
                    "preview_session_key": existing_session.preview_session_key,
                }
                if existing_session.preview_expires_at is not None:
                    session_key_fields["preview_expires_at"] = existing_session.preview_expires_at
            else:
                session_key_fields = preview_session_fields_factory()

            preview_urls = preview_url_builder(str(session_key_fields["preview_session_key"]))
            preview_env = {
                "ATOMS_PREVIEW_FRONTEND_BASE": preview_urls["preview_frontend_url"],
                "ATOMS_PREVIEW_BACKEND_BASE": preview_urls["preview_backend_url"],
                "VITE_ATOMS_PREVIEW_FRONTEND_BASE": preview_urls["preview_frontend_url"],
                "VITE_ATOMS_PREVIEW_BACKEND_BASE": preview_urls["preview_backend_url"],
            }

            logger.info("%s starting preview services", prefix)
            returncode, _, stderr = await sandbox_service.start_preview_services(container_name, env=preview_env)
            if returncode != 0:
                stderr_tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
                logger.warning(
                    "%s preview start failed returncode=%s stderr=%s",
                    prefix,
                    returncode,
                    stderr_tail,
                )
                await event_sink(
                    {
                        "type": "preview_failed",
                        "reason": "start_preview_failed",
                        "returncode": returncode,
                        "stderr": stderr_tail,
                    }
                )
            else:
                ports = await sandbox_service.get_runtime_ports(container_name)
                frontend_port = ports.get("frontend_port")
                preview_port = ports.get("preview_port")
                backend_port = ports.get("backend_port")
                logger.info(
                    "%s runtime ports frontend=%s backend=%s preview=%s",
                    prefix,
                    frontend_port,
                    backend_port,
                    preview_port,
                )

                if not frontend_port:
                    logger.warning("%s preview failed no frontend port", prefix)
                    await event_sink({"type": "preview_failed", "reason": "no_frontend_port"})
                else:
                    contract = preview_contract_loader(paths.host_root)
                    frontend_health_path = contract.frontend.healthcheck_path if contract else "/"
                    backend_health_path = (
                        contract.backend.healthcheck_path if contract and contract.backend else "/health"
                    )

                    logger.info("%s waiting for frontend service path=%s", prefix, frontend_health_path)
                    frontend_ready = await sandbox_service.wait_for_service(
                        container_name=container_name,
                        port=3000,
                        path=frontend_health_path,
                        timeout_seconds=60.0,
                        poll_interval_seconds=1.0,
                    )
                    logger.info("%s frontend wait completed ready=%s", prefix, frontend_ready)

                    backend_ready = False
                    if contract and contract.backend:
                        logger.info("%s waiting for backend service path=%s", prefix, backend_health_path)
                        backend_ready = await sandbox_service.wait_for_service(
                            container_name=container_name,
                            port=8000,
                            path=backend_health_path,
                            timeout_seconds=60.0,
                            poll_interval_seconds=1.0,
                        )
                        logger.info("%s backend wait completed ready=%s", prefix, backend_ready)

                    if not frontend_ready:
                        await event_sink({"type": "preview_failed", "reason": "timeout"})
                    else:
                        backend_status = "not_configured"
                        if contract and contract.backend:
                            backend_status = "running" if backend_ready else "stopped"

                        session = await sessions_service.create(
                            {
                                **session_key_fields,
                                "user_id": user_id,
                                "project_id": project_id,
                                "container_name": container_name,
                                "status": "running",
                                "preview_port": preview_port,
                                "frontend_port": frontend_port,
                                "backend_port": backend_port,
                                "frontend_status": "running",
                                "backend_status": backend_status,
                            }
                        )
                        logger.info("%s preview ready session_key=%s", prefix, session.preview_session_key)
                        await event_sink(
                            {
                                "type": "preview_ready",
                                "preview_session_key": session.preview_session_key,
                                "preview_expires_at": (
                                    session.preview_expires_at.isoformat() if session.preview_expires_at else None
                                ),
                                "preview_frontend_url": preview_urls["preview_frontend_url"],
                                "preview_backend_url": preview_urls["preview_backend_url"],
                                "frontend_port": frontend_port,
                                "backend_port": backend_port,
                                "frontend_status": session.frontend_status,
                                "backend_status": session.backend_status,
                            }
                        )
        except Exception as preview_exc:
            logger.exception("%s preview startup failed", prefix)
            await event_sink(
                {
                    "type": "preview_failed",
                    "reason": "exception",
                    "error": str(preview_exc),
                }
            )

        await event_sink(
            {
                "type": "done",
                "agent": agent.name,
                "status": "success",
                "result": result,
            }
        )
        logger.info("%s run_engineer_session finished successfully", prefix)
        return True
    except Exception as exc:
        logger.exception("%s run_engineer_session failed", prefix)
        await event_sink({"type": "error", "status": "failure", "error": str(exc)})
        return False
