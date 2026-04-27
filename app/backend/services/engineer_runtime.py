import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from routers.workspace_runtime import build_runtime_failure_report

from openmanus_runtime.streaming import StreamingSWEAgent, build_agent_llm
from openmanus_runtime.tool.bash import ContainerBashSession
from openmanus_runtime.tool.file_operators import ProjectFileOperator
from services.agent_run_logs import AgentRunLogStore
from services.messages import MessagesService
from services.preview_contract import load_preview_contract
from services.preview_sessions import build_preview_urls, can_reuse_preview_session, new_preview_session_fields
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


def _extract_backend_dir(command: str) -> str:
    """Extract the working directory from a preview.json backend command like 'cd /x/y && ...'."""
    import re
    m = re.match(r"cd\s+(\S+)\s*&&", command)
    return m.group(1) if m else "/workspace/app/backend"


def _build_backend_check_command(backend_dir: str, healthcheck_path: str) -> str:
    import shlex

    import_scan = r"""
import ast
import importlib
from pathlib import Path

root = Path.cwd()
local_names = {
    path.stem
    for path in root.glob("*.py")
    if path.name != "__init__.py"
}
local_names.update(
    path.name
    for path in root.iterdir()
    if path.is_dir() and path.name != ".venv"
)

module_names = set()
for path in root.rglob("*.py"):
    if ".venv" in path.parts:
        continue
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module_names.add(node.module)

for module_name in sorted(module_names):
    if module_name.split(".", 1)[0] in local_names:
        continue
    importlib.import_module(module_name)
"""
    route_check = (
        "from main import app; "
        "routes = [getattr(r, 'path', '') for r in app.routes]; "
        f"assert {healthcheck_path!r} in routes, "
        f"{('Missing ' + healthcheck_path + ' route. Found: ')!r} + str(routes); "
        "print('ATOMS_BACKEND_CHECK_OK')"
    )
    return (
        f"cd {shlex.quote(backend_dir)} && "
        "([ -x .venv/bin/python ] || uv venv .venv) && "
        "([ ! -f requirements.txt ] || "
        "uv pip install --python .venv/bin/python -r requirements.txt -q 2>&1) && "
        ".venv/bin/python - <<'PY' && "
        f".venv/bin/python -c {shlex.quote(route_check)} 2>&1\n"
        f"{import_scan.strip()}\n"
        "PY\n"
    )


def _extract_terminate_summary(result: object) -> str:
    if not isinstance(result, str):
        return ""

    terminate_prefix = "The interaction has been completed with status:"
    terminate_index = result.rfind(terminate_prefix)
    if terminate_index == -1:
        return ""

    segment = result[terminate_index:]
    next_observation_index = segment.find("\n\nObserved output of cmd `")
    if next_observation_index != -1:
        segment = segment[:next_observation_index]

    marker = "\nSummary:"
    marker_index = segment.find(marker)
    if marker_index == -1:
        return ""
    return segment[marker_index + len(marker):].strip()


async def _probe_backend_health(
    sandbox_service: Any,
    container_name: str,
    host_root: Path,
    preview_contract_loader: "PreviewContractLoader",
) -> tuple[bool, str | None]:
    """Check backend code correctness via static import rather than probing a live port.

    Returns (has_backend, error_message):
      (False, None)  — no backend section in preview.json, nothing to check
      (True,  None)  — backend exists and import check passed
      (True,  str)   — backend exists but check failed; str is the pushback message
    """
    try:
        contract = preview_contract_loader(host_root)
    except Exception:
        return False, None
    if contract is None or contract.backend is None:
        return False, None

    backend_dir = _extract_backend_dir(contract.backend.command)
    healthcheck_path = contract.backend.healthcheck_path or "/health"
    check_cmd = _build_backend_check_command(backend_dir, healthcheck_path)

    try:
        returncode, out, err = await sandbox_service.exec(container_name, check_cmd)
        stdout_lines = out.strip().splitlines()
        if returncode == 0 and stdout_lines and stdout_lines[-1] == "ATOMS_BACKEND_CHECK_OK":
            return True, None
        error_output = (out + err).strip() or "(no output)"
    except Exception as exc:
        error_output = str(exc)

    return True, (
        f"Backend code check FAILED. The app could not be imported or is missing the {healthcheck_path} route.\n"
        "Fix the import errors or add the missing endpoint, then verify with:\n"
        f"  cd {backend_dir} && .venv/bin/python -c \"from main import app; print('ok')\"\n"
        f"Error output:\n```\n{error_output}\n```"
    )


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
        run_logs = AgentRunLogStore(base_root=_WORKSPACES_ROOT)
        recorder = run_logs.start_run(user_id=user_id, project_id=project_id)

        async def traced_event_sink(event: dict[str, Any]) -> None:
            event_type = str(event.get("type") or "")
            if event_type == "progress":
                recorder.progress(str(event.get("label") or ""))
            elif event_type == "terminal.log":
                terminal_content = str(event.get("content") or "")
                if not terminal_content.startswith("$ [system] "):
                    recorder.terminal(terminal_content)
            elif event_type == "error":
                recorder.error(str(event.get("error") or event.get("message") or "Unknown error"))
            await event_sink(event)

        async def log_step(message: str) -> None:
            logger.info("%s %s", prefix, message)
            recorder.system(message)
            await traced_event_sink({"type": "terminal.log", "content": f"$ [system] {message}"})

        workspace_events = WorkspaceEventEmitter(traced_event_sink)
        await log_step("run started")

        if stop_event is not None and stop_event.is_set():
            logger.info("%s run_engineer_session skipped because stop_event already set", prefix)
            recorder.set_status("stopped")
            return False

        workspace_service = workspace_service_factory()
        paths = workspace_service.resolve_paths(user_id=user_id, project_id=project_id)
        await log_step(f"workspace resolved host_root={paths.host_root}")

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
        await log_step(f"materialized {len(file_records)} project files")

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
        await log_step(f"loaded {len(persisted_history)} persisted messages")

        # We completely remove the DeepSeek classifier intercept.
        # Always assume we might need the sandbox and let the agent naturally decide
        # if it needs to use implementation tools (which will enforce the plan gate).
        requires_draft_plan = True

        sandbox_service = sandbox_service_factory()
        try:
            await log_step("ensuring sandbox runtime")
            container_name = await sandbox_service.ensure_runtime(
                user_id=user_id,
                project_id=project_id,
                host_root=paths.host_root,
            )
            await log_step(f"sandbox ready container={container_name}")
        except Exception as exc:
            logger.exception("%s sandbox startup failed", prefix)
            recorder.set_status("failed")
            await traced_event_sink({"type": "error", "status": "failure", "error": f"Could not start sandbox: {exc}"})
            return False

        from services.approval_gate import ApprovalGate
        gate = ApprovalGate(requires_approval=requires_draft_plan)
        file_operator = ProjectFileOperator(
            host_root=paths.host_root,
            container_root=paths.container_root,
            event_sink=workspace_events.emit,
            approval_gate=gate,
        )
        bash_session = ContainerBashSession(
            runtime_service=sandbox_service,
            container_name=container_name,
            approval_gate=gate,
        )
        from openmanus_runtime.tool.draft_plan import DraftPlanTool
        from openmanus_runtime.tool.load_skill import LoadSkillTool
        from openmanus_runtime.tool.todo_write import TodoWriteTool
        from openmanus_runtime.tool.task_update import TaskUpdateTool
        from services.agent_draft_plan import get_agent_draft_plan_service
        from services.agent_task_store import AgentTaskStore

        draft_plan_service = get_agent_draft_plan_service()

        def build_workspace_agent():
            llm = llm_builder(model)
            logger.info("%s llm constructed model=%s", prefix, model)
            agent = agent_cls.build_for_workspace(
                llm=llm,
                event_emitter=traced_event_sink,
                file_operator=file_operator,
                bash_session=bash_session,
            )
            draft_plan_tool = DraftPlanTool.create(
                event_sink=traced_event_sink,
                service=draft_plan_service,
                project_id=project_id,
                approval_gate=gate,
                stop_event=stop_event,
            )
            load_skill_tool = LoadSkillTool.create(loader=_skill_loader)
            todo_write_tool = TodoWriteTool.create(
                file_operator=file_operator,
                event_sink=traced_event_sink,
                task_store_factory=lambda: AgentTaskStore(db),
                project_id=project_id,
                approval_gate=gate,
            )
            task_update_tool = TaskUpdateTool.create(
                event_sink=traced_event_sink,
                task_store_factory=lambda: AgentTaskStore(db),
                project_id=project_id,
                approval_gate=gate,
            )
            if hasattr(agent, "available_tools") and agent.available_tools is not None:
                agent.available_tools.add_tool(draft_plan_tool)
                agent.available_tools.add_tool(load_skill_tool)
                agent.available_tools.add_tool(todo_write_tool)
                agent.available_tools.add_tool(task_update_tool)
            logger.info("%s agent built name=%s", prefix, agent.name)
            return agent

        readme_block = ""
        readme_path = paths.host_root / "app" / "backend" / "README.md"
        try:
            readme_content = readme_path.read_text(encoding="utf-8").strip()
            if readme_content:
                readme_block = (
                    "## Backend README (mandatory reading before implementing backend features)\n\n"
                    f"{readme_content}\n\n---\n\n"
                )
                await log_step(f"injected backend README chars={len(readme_content)}")
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
            '  "frontend": {"command": "cd /workspace/app/frontend && pnpm run dev -- --host 0.0.0.0 --port 3000", "healthcheck_path": "/"},\n'
            '  "backend": {"command": "cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000", "healthcheck_path": "/health"}\n'
            '}\n'
            'The "backend" object is optional when the app is frontend-only.\n'
            "If your app has a backend API, set the VITE_ATOMS_PREVIEW_BACKEND_BASE "
            "environment variable in the frontend and use it as the base for every API call:\n"
            "  const backendBase = (import.meta.env.VITE_ATOMS_PREVIEW_BACKEND_BASE ?? '').replace(/\\/$/, '');\n"
            "  const res = await fetch(`${backendBase}/api/your-endpoint`);\n"
            "Never hardcode 'http://localhost:8000'; never concatenate two slashes.\n"
            "If you build a backend, you MUST create app/backend/main.py as the entrypoint "
            "and include a GET /health endpoint that returns HTTP 200 `{\"status\": \"healthy\"}`. "
            "You MUST also create app/backend/requirements.txt containing `fastapi`, `uvicorn`, and any other dependencies.\n"
            "Python imports inside backend files MUST be relative to /workspace/app/backend/ "
            "(that is the working directory when uvicorn launches via `cd /workspace/app/backend && uvicorn main:app`). "
            "Write `from routers import color`, `from services.color_service import generate_palette` — "
            "NEVER `from app.backend.routers import` or `from app.backend.services import`.\n"
            "If you build a React SPA with React Router or any client-side router, "
            "it must work under the preview base path instead of assuming '/'. "
            "For BrowserRouter, set a basename derived from the current preview path or an equivalent mechanism.\n"
            "Do not start the preview services yourself (start-preview handles this automatically).\n"
            "Do NOT create a .env file that sets VITE_ATOMS_PREVIEW_BACKEND_BASE — "
            "this variable is injected by start-preview at runtime and must not be hardcoded.\n"
            "For backend verification use ONLY the static import test — never start uvicorn manually:\n"
            "  cd /workspace/app/backend && ([ -x .venv/bin/python ] || uv venv .venv) && uv pip install --python .venv/bin/python -r requirements.txt -q 2>&1 && .venv/bin/python -c \"from main import app; print('ok')\"\n"
            "Always install requirements with `uv pip install --python .venv/bin/python`; do not use `.venv/bin/pip`, `pip3 install`, or `python3 -m pip install`. "
            "This confirms imports and app construction without starting a process.\n"
            "If you ever start a background process for any reason, do NOT kill it before calling terminate.\n\n"
            "## Orchestration Workflow\n\n"
            "For implementation requests:\n"
            "1. Briefly acknowledge the user's request in 1-2 sentences, then call `draft_plan` with a short numbered list (3-7 items) — keep draft_plan in its own turn, separate from other tool calls. Wait for user approval.\n"
            "2. After approval, write a detailed implementation plan to "
            "`docs/plans/{YYYY-MM-DD}-{feature-slug}.md` using str_replace_editor. "
            "This plan must expand each approved item into concrete steps with specific file paths, "
            "what to create/modify, and execution order.\n"
            "3. You must call `todo_write` BEFORE implementation to initialize the task system with your full checklist (max 8 items). "
            "Each item can specify a `blocked_by` array if it depends on other tasks.\\n"
            "4. Implement the plan step by step. You MUST continuously call `task_update` to update task statuses "
            "to 'completed' as you finish them, and set the next task to 'in_progress'. "
            "When a task is marked 'completed', it will automatically unblock dependent tasks.\\n"
            "   For frontend apps, create infrastructure files FIRST (package.json, vite.config.ts, index.html, main.tsx, tsconfig.json) "
            "before writing any component files. This ensures pnpm install and builds work from the start.\\n"
            "5. Verify before finishing — use ONLY these commands, do NOT start background servers:\\n"
            "   Backend: cd /workspace/app/backend && ([ -x .venv/bin/python ] || uv venv .venv) && uv pip install --python .venv/bin/python -r requirements.txt -q 2>&1 && .venv/bin/python -c \"from main import app; print('ok')\"\\n"
            "   Frontend: cd /workspace/app/frontend && pnpm run build 2>&1 | tail -5\\n"
            "   Always install requirements with `uv pip install --python .venv/bin/python`; do not use `.venv/bin/pip`, `pip3 install`, or `python3 -m pip install`.\\n"
            "   A completion gate re-runs the backend import check after you terminate; if it fails you will be asked to fix it.\\n"
            "6. You MUST ensure ALL tasks are marked as 'completed' via `task_update` BEFORE you finish. Do NOT attempt "
            "to finish if any task is still pending or in_progress.\\n"
            "7. After ALL tasks are completed and verification has passed, you MUST send one final message to the user "
            "that briefly summarizes: what was built, which files were created or modified, and how to use the app. "
            "This is required — do not skip it.\\n\\n"

            f"User request:\n{prompt}"
        )
        skill_listing = _skill_loader.describe_available()
        if skill_listing:
            skill_summary = "\n".join(f"- {name}: {desc}" for name, desc in skill_listing.items())
            task_prompt = task_prompt.replace(
                f"User request:\n{prompt}",
                f"Available skills (use load_skill tool to get full content):\n{skill_summary}\n\nUser request:\n{prompt}",
            )
        await log_step("agent run started")
        
        MAX_COMPLETION_ROUNDS = 10
        agent = build_workspace_agent()
        session_started = False
        current_prompt = task_prompt
        result = None

        for _round in range(MAX_COMPLETION_ROUNDS):
            if not session_started:
                await traced_event_sink(
                    {
                        "type": "session",
                        "agent": agent.name,
                        "workspace_root": str(paths.host_root),
                        "status": "started",
                    }
                )
                session_started = True

            result = await agent.run(current_prompt)

            request_key = gate.approved_request_key
            if not request_key:
                break

            task_store = AgentTaskStore(db)
            tasks = await task_store.list_tasks(project_id, request_key)
            incomplete_tasks = [t for t in tasks if t.status != "completed"]

            has_verification = bash_session.has_verification_run()

            has_backend, backend_error = await _probe_backend_health(
                sandbox_service, container_name, paths.host_root, preview_contract_loader
            )

            # Backend import check passing IS meaningful verification — don't require a
            # separate agent-run command when the gate already confirmed the code is sound.
            backend_passed = has_backend and backend_error is None
            verification_ok = has_verification or backend_passed

            tasks_done = not tasks or (not incomplete_tasks and verification_ok)
            if tasks_done and not backend_error:
                break

            pushback_msgs = []
            if backend_error:
                pushback_msgs.append(f"- {backend_error}")
            if incomplete_tasks:
                titles = ", ".join([f"'{t.subject}'" for t in incomplete_tasks])
                pushback_msgs.append(
                    f"- Tasks not completed: {titles}. Mark each completed via task_update."
                )
            if tasks and not verification_ok:
                pushback_msgs.append(
                    "- No verification command run. For backend run: "
                    "cd /workspace/app/backend && ([ -x .venv/bin/python ] || uv venv .venv) && uv pip install --python .venv/bin/python -r requirements.txt -q 2>&1 && .venv/bin/python -c \"from main import app; print('ok')\". "
                    "For frontend run: pnpm run build."
                )

            current_prompt = (
                "COMPLETION GATE — you may not finish until all tasks are marked completed, "
                "verification has run, and the backend (if any) passes the import check:\n"
                + "\n".join(pushback_msgs)
            )

            await log_step(
                f"Completion gate: {len(incomplete_tasks)} incomplete task(s), "
                f"verification={has_verification}, backend_passed={backend_passed}. Continuing same agent."
            )
            await traced_event_sink({
                "type": "assistant",
                "agent": "system",
                "content": current_prompt,
            })
        else:
            await log_step(f"Completion gate: reached max rounds ({MAX_COMPLETION_ROUNDS}), proceeding.")

        await log_step("agent run completed")
        final_summary = _extract_terminate_summary(result)
        if final_summary:
            await traced_event_sink({"type": "assistant", "agent": agent.name, "content": final_summary})

        if stop_event is not None and stop_event.is_set():
            logger.info("%s run_engineer_session stopped after agent.run", prefix)
            recorder.set_status("stopped")
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
            await log_step(f"snapshot captured files={len(snapshot)}")

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

            await traced_event_sink({"type": "workspace_sync", "changed_files": changed_paths})
            await log_step(f"workspace sync updated_files={len(changed_paths)}")
        except Exception as sync_exc:
            logger.warning("%s workspace sync failed: %s", prefix, sync_exc)
            recorder.error(f"workspace sync failed: {sync_exc}")

        if stop_event is not None and stop_event.is_set():
            logger.info("%s run_engineer_session stopped before preview startup", prefix)
            recorder.set_status("stopped")
            return False

        try:
            sessions_service = workspace_runtime_sessions_service_cls(db)
            existing_session = await sessions_service.get_by_project(user_id=user_id, project_id=project_id)
            if existing_session and can_reuse_preview_session(
                existing_session.preview_session_key,
                existing_session.preview_expires_at,
            ):
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

            await log_step("starting preview services")
            returncode, stdout, stderr = await sandbox_service.start_preview_services(container_name, env=preview_env)
            if returncode != 0:
                stderr_lines = stderr.strip().splitlines() if stderr.strip() else []
                stderr_tail = stderr_lines[-1] if stderr_lines else ""
                stderr_full = "\n".join(stderr_lines[-10:]) if stderr_lines else ""
                logger.warning(
                    "%s preview start failed returncode=%s\nstdout=%s\nstderr=%s",
                    prefix,
                    returncode,
                    stdout.strip() or "<empty>",
                    stderr_full or "<empty>",
                )
                recorder.error(f"preview start failed returncode={returncode} stderr={stderr_tail}")
                await traced_event_sink(
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
                await log_step(
                    f"runtime ports frontend={frontend_port} backend={backend_port} preview={preview_port}"
                )

                if not frontend_port:
                    logger.warning("%s preview failed no frontend port", prefix)
                    recorder.error("preview failed no_frontend_port")
                    await traced_event_sink({"type": "preview_failed", "reason": "no_frontend_port"})
                else:
                    contract = preview_contract_loader(paths.host_root)
                    frontend_health_path = contract.frontend.healthcheck_path if contract else "/"
                    backend_health_path = (
                        contract.backend.healthcheck_path if contract and contract.backend else "/health"
                    )

                    await log_step(f"waiting for frontend service path={frontend_health_path}")
                    frontend_ready = await sandbox_service.wait_for_service(
                        container_name=container_name,
                        port=3000,
                        path=frontend_health_path,
                        timeout_seconds=60.0,
                        poll_interval_seconds=1.0,
                    )
                    await log_step(f"frontend wait completed ready={frontend_ready}")

                    backend_ready = False
                    if contract and contract.backend:
                        await log_step(f"waiting for backend service path={backend_health_path}")
                        backend_ready = await sandbox_service.wait_for_service(
                            container_name=container_name,
                            port=8000,
                            path=backend_health_path,
                            timeout_seconds=60.0,
                            poll_interval_seconds=1.0,
                        )
                        await log_step(f"backend wait completed ready={backend_ready}")

                    if not frontend_ready:
                        recorder.error("preview failed timeout")
                        await traced_event_sink({"type": "preview_failed", "reason": "timeout"})
                    elif contract and contract.backend and not backend_ready:
                        # Backend is configured but healthcheck timed out — emit failure and
                        # feed a repair continuation prompt back to the agent.
                        recorder.error("preview failed backend_healthcheck_timeout")
                        diag = build_runtime_failure_report(
                            service="backend",
                            phase="healthcheck",
                            reason_code="backend_healthcheck_timeout",
                            detected_root=str(paths.host_root),
                            attempted_command=contract.backend.command,
                            stderr_tail="",
                            suggested_fix=(
                                "Ensure the backend server binds to 0.0.0.0 and the correct port, "
                                "check for import errors or missing dependencies, and verify the "
                                f"healthcheck path '{contract.backend.healthcheck_path}' responds with HTTP 200."
                            ),
                        )
                        await traced_event_sink(
                            {
                                "type": "preview_failed",
                                "reason": "backend_healthcheck_timeout",
                                "diagnostic": diag,
                            }
                        )
                        await traced_event_sink(
                            {
                                "type": "assistant",
                                "agent": "system",
                                "content": (
                                    "CRITICAL PREVIEW FAILURE:\n"
                                    f"- service: {diag['service']}\n"
                                    f"- phase: {diag['phase']}\n"
                                    f"- reason_code: {diag['reason_code']}\n"
                                    f"- detected_root: {diag['detected_root']}\n"
                                    f"- attempted_command: {diag['attempted_command']}\n"
                                    f"- stderr_tail: {diag['stderr_tail']}\n"
                                    f"- suggested_fix: {diag['suggested_fix']}\n"
                                    "Fix the workspace so preview can start, "
                                    "then update todo status and run verification."
                                ),
                            }
                        )
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
                        await log_step(f"preview ready session_key={session.preview_session_key}")
                        await traced_event_sink(
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
            recorder.error(f"preview startup failed: {preview_exc}")
            await traced_event_sink(
                {
                    "type": "preview_failed",
                    "reason": "exception",
                    "error": str(preview_exc),
                }
            )

        recorder.set_status("completed")
        await log_step("run completed")
        await traced_event_sink(
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
        try:
            recorder.error(str(exc))
            recorder.set_status("failed")
        except Exception:
            logger.debug("%s failed to record run error", prefix)
        await event_sink({"type": "error", "status": "failure", "error": str(exc)})
        return False
