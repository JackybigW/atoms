# Agent Sandbox Workspace Runtime Progress

Date: 2026-04-20
Plan: `/Users/jackywang/Documents/atoms/docs/superpowers/plans/2026-04-20-agent-sandbox-workspace-runtime.md`
Execution mode: Subagent-Driven for Task 1 and Task 2, stopped after Task 2 per request.

## Status Summary

- Task 1: Completed
- Task 2: Completed
- Task 3: Pending
- Task 4: Pending
- Task 5: Pending
- Task 6: Pending

## Completed Work

### Task 1: Project Workspace Materialization

Implemented:
- `app/backend/services/project_workspace.py`
- `app/backend/tests/test_project_workspace.py`

Delivered:
- Per-user / per-project host workspace resolution
- Workspace path validation and escape prevention
- Materialize `project_files`-style content onto disk
- Snapshot host workspace content back into source records
- Ignore runtime/build directories such as `.venv`, `node_modules`, `dist`, `build`, `__pycache__`, `.git`

Verification:
- `cd /Users/jackywang/Documents/atoms/app/backend && ./.venv/bin/python -m pytest tests/test_project_workspace.py -v`

### Task 2: Docker-Backed Sandbox Runtime Sessions

Implemented:
- `app/backend/models/workspace_runtime_sessions.py`
- `app/backend/services/workspace_runtime_sessions.py`
- `app/backend/services/sandbox_runtime.py`
- `app/backend/tests/test_sandbox_runtime.py`

Supporting changes needed to make Task 2 production-usable:
- `app/backend/models/__init__.py`
- `app/backend/services/database.py`
- `app/backend/core/database.py`

Delivered:
- `WorkspaceRuntimeSessions` model with runtime metadata fields
- `WorkspaceRuntimeSessionsService.get_by_project(...)`
- `WorkspaceRuntimeSessionsService.create(...)` with:
  - field allowlist
  - required/non-empty validation
  - idempotent upsert semantics for the same `(user_id, project_id)`
  - rollback on failed upsert commit
- `SandboxRuntimeService.ensure_runtime(...)` with:
  - Docker `run -d`
  - bind mount to `/workspace`
  - published ports for `3000` and `8000`
  - Docker-safe container naming
  - digest-based disambiguation when IDs normalize/truncate/case-fold
  - host workspace existence checks
  - immutable image-ID verification before container reuse
  - runtime shape checks before reuse (`WorkingDir`, `Cmd`, required port bindings)
- `SandboxRuntimeService.exec(...)`
- `SandboxRuntimeService.get_runtime_ports(...)`
- timeout guards around Docker commands
- subprocess kill on timeout
- metadata registration before `create_all()`
- existing-table repair plus `workspace_runtime_sessions` uniqueness repair on startup

Verification:
- `cd /Users/jackywang/Documents/atoms/app/backend && ./.venv/bin/python -m pytest tests/test_sandbox_runtime.py -q`
- Current result: `27 passed, 1 warning`

## Remaining Plan Work

### Task 3: Inject Project-Scoped Tools into OpenManus SWE Runs

Not started.

Planned scope:
- Hydrate DB files into project workspace before agent execution
- Ensure sandbox exists before engineer agent runs
- Point OpenManus file/bash tools at the project-specific workspace
- Sync changed files back into persistent `project_files`
- Emit `workspace_sync` and `preview_ready` events

### Task 4: Runtime Status and Preview Proxy APIs

Not started.

Planned scope:
- Runtime ensure/status endpoints
- Preview proxy for sandbox app traffic
- Runtime response schemas

### Task 5: Frontend Workspace Refresh and App View Wiring

Not started.

Planned scope:
- Consume runtime status from frontend
- Replace hardcoded preview URL behavior
- Reload workspace on sync events
- Point `App View` at real sandbox preview URLs

### Task 6: End-to-End Verification and Guardrails

Not started.

Planned scope:
- End-to-end runtime + sync verification
- Preview event verification
- Operational checks and regression coverage

## Notes

- This progress file reflects the stop point requested after Task 2.
- Task 2 is complete locally from an implementation and targeted-test perspective.
- No claim is made here about Task 3+ functionality; those remain pending.
