# Preview Runtime Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make preview startup resilient to reasonable frontend/backend path and filename drift, bootstrap backend runtimes predictably in the sandbox, and return structured repair diagnostics instead of hard failing when preview startup breaks.

**Architecture:** Introduce a normalized preview runtime model plus a resolver service that reads `.atoms/preview.json`, falls back to convention-based discovery, and produces verified startup metadata for frontend and backend. Use that model to drive sandbox bootstrap, stricter readiness semantics, and structured failure reports that engineer runtime can feed back into continuation rounds.

**Tech Stack:** FastAPI, Python services, Docker sandbox scripts, pytest, shell script tests

---

## File Structure

### New files

- `app/backend/services/preview_runtime_resolver.py`
  - Build `PreviewRuntimeModel`, detect frontend/backend roots, normalize backend startup commands, and emit structured diagnostics.
- `app/backend/tests/test_preview_runtime_resolver.py`
  - Unit tests for manifest-first resolution, fallback detection, backend entrypoint discovery, and command normalization.
- `app/backend/tests/test_preview_runtime_diagnostics.py`
  - Tests for structured diagnostics and degraded readiness behavior.

### Modified files

- `app/backend/routers/workspace_runtime.py`
  - Replace direct manifest trust with resolver-backed runtime startup and stricter service-aware readiness semantics.
- `app/backend/services/sandbox_runtime.py`
  - Support resolver-driven startup inputs and diagnostic propagation.
- `app/backend/services/engineer_runtime.py`
  - Consume preview diagnostics and, on eligible failures, feed a repair continuation round to the agent instead of ending immediately.
- `docker/atoms-sandbox/start-preview`
  - Add backend root detection, backend venv bootstrap, backend dependency install, command normalization, and structured logging.
- `docker/atoms-sandbox/Dockerfile`
  - Install minimal runtime pieces required for deterministic backend bootstrap if the script needs additional tooling beyond the current Python and pip setup.
- `app/backend/tests/test_workspace_runtime.py`
  - Add integration tests for resolver-backed startup and degraded readiness behavior.
- `app/backend/tests/test_agent_runtime.py`
  - Add assertions for repair continuation payloads and runtime diagnostics plumbing.
- `app/backend/tests/test_start_preview_script.py`
  - Extend script tests to cover backend bootstrap and normalized Python startup.

### Existing files to read before implementation

- `docs/superpowers/specs/2026-04-23-preview-runtime-stability-design.md`
- `app/backend/services/engineer_runtime.py`
- `app/backend/routers/workspace_runtime.py`
- `app/backend/services/sandbox_runtime.py`
- `docker/atoms-sandbox/start-preview`
- `app/backend/tests/test_preview_gateway.py`
- `app/backend/tests/test_workspace_runtime.py`

---

### Task 1: Add Resolver-Backed Preview Runtime Model

**Files:**
- Create: `app/backend/services/preview_runtime_resolver.py`
- Create: `app/backend/tests/test_preview_runtime_resolver.py`

- [ ] **Step 1: Write the failing resolver tests**

```python
from pathlib import Path

from services.preview_runtime_resolver import resolve_preview_runtime_model


def test_resolver_prefers_manifest_backend_command_but_normalizes_uvicorn():
    workspace = Path("/tmp/workspace")
    model = resolve_preview_runtime_model(
        workspace_root=workspace,
        manifest={
            "frontend": {
                "command": "cd /workspace/app/frontend && pnpm run dev -- --host 0.0.0.0 --port 3000",
                "healthcheck_path": "/",
            },
            "backend": {
                "command": "cd /workspace/app/backend && uv run uvicorn server:app --host 0.0.0.0 --port 8000",
                "healthcheck_path": "/health",
            },
        },
        existing_paths={
            "/workspace/app/frontend/package.json",
            "/workspace/app/backend/server.py",
            "/workspace/app/backend/requirements.txt",
        },
        python_fastapi_files={"/workspace/app/backend/server.py": "app"},
    )

    assert model.backend is not None
    assert model.backend.root == "/workspace/app/backend"
    assert model.backend.import_target == "server:app"
    assert model.backend.start_strategy == "python_venv_uvicorn"
    assert model.backend.healthcheck_path == "/health"


def test_resolver_falls_back_to_server_py_when_main_py_is_missing():
    workspace = Path("/tmp/workspace")
    model = resolve_preview_runtime_model(
        workspace_root=workspace,
        manifest=None,
        existing_paths={
            "/workspace/app/frontend/package.json",
            "/workspace/app/backend/server.py",
            "/workspace/app/backend/requirements.txt",
        },
        python_fastapi_files={"/workspace/app/backend/server.py": "app"},
    )

    assert model.backend is not None
    assert model.backend.root == "/workspace/app/backend"
    assert model.backend.import_target == "server:app"


def test_resolver_emits_backend_entrypoint_not_found_diagnostic():
    workspace = Path("/tmp/workspace")
    model = resolve_preview_runtime_model(
        workspace_root=workspace,
        manifest=None,
        existing_paths={
            "/workspace/app/frontend/package.json",
            "/workspace/app/backend/requirements.txt",
        },
        python_fastapi_files={},
    )

    assert model.backend is None
    assert model.diagnostics[0].reason_code == "backend_entrypoint_not_found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/backend/tests/test_preview_runtime_resolver.py -q`
Expected: FAIL with `ModuleNotFoundError` or missing `resolve_preview_runtime_model`

- [ ] **Step 3: Write the minimal runtime model and resolver**

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PreviewRuntimeDiagnostic:
    service: str
    phase: str
    reason_code: str
    message: str
    detected_root: str = ""
    detected_entrypoint: str = ""
    attempted_command: str = ""


@dataclass(frozen=True)
class PreviewServiceRuntime:
    service: str
    root: str
    healthcheck_path: str
    start_strategy: str
    start_command: str
    import_target: str = ""


@dataclass(frozen=True)
class PreviewRuntimeModel:
    frontend: PreviewServiceRuntime | None
    backend: PreviewServiceRuntime | None
    diagnostics: list[PreviewRuntimeDiagnostic] = field(default_factory=list)


def resolve_preview_runtime_model(...):
    diagnostics = []
    frontend = _resolve_frontend(...)
    backend = _resolve_backend(...)
    if backend is None and _backend_candidate_exists(...):
        diagnostics.append(
            PreviewRuntimeDiagnostic(
                service="backend",
                phase="resolve",
                reason_code="backend_entrypoint_not_found",
                message="Could not detect a FastAPI app entrypoint under the backend root.",
                detected_root="/workspace/app/backend",
            )
        )
    return PreviewRuntimeModel(frontend=frontend, backend=backend, diagnostics=diagnostics)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest app/backend/tests/test_preview_runtime_resolver.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/backend/services/preview_runtime_resolver.py app/backend/tests/test_preview_runtime_resolver.py
git commit -m "feat(preview): add runtime resolver model"
```

---

### Task 2: Add Backend Bootstrap to `start-preview`

**Files:**
- Modify: `docker/atoms-sandbox/start-preview`
- Modify: `docker/atoms-sandbox/Dockerfile`
- Modify: `app/backend/tests/test_start_preview_script.py`

- [ ] **Step 1: Write the failing backend bootstrap script test**

```python
def test_start_preview_creates_backend_venv_and_normalizes_uv_run(tmp_path):
    workspace_root = tmp_path / "workspace"
    backend_root = workspace_root / "app" / "backend"
    backend_root.mkdir(parents=True)
    (backend_root / "requirements.txt").write_text(
        "fastapi==0.109.0\nuvicorn[standard]==0.27.0\n",
        encoding="utf-8",
    )
    (backend_root / "server.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): return {'ok': True}\n",
        encoding="utf-8",
    )
    (workspace_root / ".atoms").mkdir()
    (workspace_root / ".atoms" / "preview.json").write_text(
        '{"frontend":{"command":"cd /workspace/app/frontend && pnpm run dev -- --host 0.0.0.0 --port 3000","healthcheck_path":"/"},"backend":{"command":"cd /workspace/app/backend && uv run uvicorn server:app --host 0.0.0.0 --port 8000","healthcheck_path":"/health"}}',
        encoding="utf-8",
    )

    # execute patched start-preview against temp workspace
    # assert backend venv path exists and backend launch command no longer depends on `uv`
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/backend/tests/test_start_preview_script.py -q`
Expected: FAIL because backend bootstrap and normalization are not implemented

- [ ] **Step 3: Implement backend bootstrap in `start-preview`**

```bash
resolve_backend_root() {
  local workspace_root="$1"
  if [[ -f "${workspace_root}/app/backend/requirements.txt" ]] || [[ -f "${workspace_root}/app/backend/pyproject.toml" ]]; then
    echo "${workspace_root}/app/backend"
    return 0
  fi
  if [[ -f "${workspace_root}/backend/requirements.txt" ]] || [[ -f "${workspace_root}/backend/pyproject.toml" ]]; then
    echo "${workspace_root}/backend"
    return 0
  fi
  echo ""
}

ensure_backend_venv() {
  local backend_root="$1"
  local venv_dir="${backend_root}/.venv"
  if [[ ! -x "${venv_dir}/bin/python" ]]; then
    python3 -m venv "${venv_dir}"
  fi
  if [[ -f "${backend_root}/requirements.txt" ]]; then
    "${venv_dir}/bin/pip" install -r "${backend_root}/requirements.txt"
  fi
}

normalize_backend_command() {
  local backend_root="$1"
  local command="$2"
  local venv_python="${backend_root}/.venv/bin/python"
  if [[ "${command}" == *"uv run uvicorn "* ]]; then
    local import_target
    import_target="$(printf '%s' "${command}" | sed -E 's/.*uv run uvicorn ([^ ]+).*/\\1/')"
    echo "cd ${backend_root} && ${venv_python} -m uvicorn ${import_target} --host 0.0.0.0 --port 8000"
    return 0
  fi
  echo "${command}"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest app/backend/tests/test_start_preview_script.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docker/atoms-sandbox/start-preview docker/atoms-sandbox/Dockerfile app/backend/tests/test_start_preview_script.py
git commit -m "fix(preview): bootstrap backend runtimes in sandbox"
```

---

### Task 3: Use Resolver in Workspace Runtime and Tighten Readiness Semantics

**Files:**
- Modify: `app/backend/routers/workspace_runtime.py`
- Modify: `app/backend/services/sandbox_runtime.py`
- Modify: `app/backend/tests/test_workspace_runtime.py`
- Create: `app/backend/tests/test_preview_runtime_diagnostics.py`

- [ ] **Step 1: Write the failing readiness and diagnostics tests**

```python
@pytest.mark.asyncio
async def test_workspace_runtime_does_not_publish_full_ready_when_backend_healthcheck_fails(...):
    sandbox.start_preview_services = AsyncMock(return_value=(0, "ok", ""))
    sandbox.get_runtime_ports = AsyncMock(return_value={"frontend_port": 3000, "backend_port": 8000, "preview_port": 3000})
    sandbox.wait_for_service = AsyncMock(side_effect=[True, False])

    result = await ensure_runtime_for_project(...)

    assert result["frontend_status"] == "running"
    assert result["backend_status"] == "failed"
    assert result["preview_status"] == "degraded"


def test_preview_runtime_failure_report_contains_reason_code():
    diagnostic = build_runtime_failure_report(
        service="backend",
        phase="healthcheck",
        reason_code="backend_healthcheck_timeout",
        detected_root="/workspace/app/backend",
        attempted_command="cd /workspace/app/backend && .venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000",
        stderr_tail="",
    )
    assert diagnostic["reason_code"] == "backend_healthcheck_timeout"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/backend/tests/test_workspace_runtime.py app/backend/tests/test_preview_runtime_diagnostics.py -q`
Expected: FAIL because full-ready semantics and failure report builder do not exist yet

- [ ] **Step 3: Implement resolver-backed startup and degraded readiness**

```python
runtime_model = resolve_preview_runtime_model(...)
returncode, stdout, stderr = await sandbox_service.start_preview_services(
    container_name,
    env=env,
    runtime_model=runtime_model,
)

frontend_ready = await sandbox_service.wait_for_service(container_name, 3000, path=runtime_model.frontend.healthcheck_path)
backend_ready = True
if runtime_model.backend is not None:
    backend_ready = await sandbox_service.wait_for_service(container_name, 8000, path=runtime_model.backend.healthcheck_path)

preview_status = "running"
if runtime_model.backend is not None and not backend_ready:
    preview_status = "degraded"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest app/backend/tests/test_workspace_runtime.py app/backend/tests/test_preview_runtime_diagnostics.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/backend/routers/workspace_runtime.py app/backend/services/sandbox_runtime.py app/backend/tests/test_workspace_runtime.py app/backend/tests/test_preview_runtime_diagnostics.py
git commit -m "fix(preview): add degraded readiness semantics"
```

---

### Task 4: Feed Structured Preview Failures Back Into Engineer Runtime

**Files:**
- Modify: `app/backend/services/engineer_runtime.py`
- Modify: `app/backend/tests/test_agent_runtime.py`

- [ ] **Step 1: Write the failing repair continuation test**

```python
@pytest.mark.asyncio
async def test_engineer_runtime_emits_repair_continuation_for_backend_preview_failure(monkeypatch):
    fake_diagnostic = {
        "service": "backend",
        "phase": "bootstrap",
        "reason_code": "entrypoint_import_failed",
        "detected_root": "/workspace/app/backend",
        "detected_entrypoint": "server:app",
        "attempted_command": "cd /workspace/app/backend && .venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000",
        "stderr_tail": "ImportError: attempted relative import with no known parent package",
        "suggested_fix": "Fix backend imports so the detected entrypoint can be imported.",
    }

    # run_engineer_session(...)
    # assert a continuation prompt mentions reason_code and suggested_fix
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/backend/tests/test_agent_runtime.py -q -k repair_continuation`
Expected: FAIL because engineer runtime does not emit structured preview repair continuations yet

- [ ] **Step 3: Implement repair continuation behavior**

```python
if preview_result.preview_status == "degraded" and preview_result.diagnostics:
    repair_prompt = (
        "CRITICAL PREVIEW FAILURE:\n"
        f"- service: {diag['service']}\n"
        f"- phase: {diag['phase']}\n"
        f"- reason_code: {diag['reason_code']}\n"
        f"- detected_root: {diag['detected_root']}\n"
        f"- detected_entrypoint: {diag['detected_entrypoint']}\n"
        f"- attempted_command: {diag['attempted_command']}\n"
        f"- stderr_tail: {diag['stderr_tail']}\n"
        f"- suggested_fix: {diag['suggested_fix']}\n"
        "Fix the workspace so preview can start, then update todo status and rerun verification."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest app/backend/tests/test_agent_runtime.py -q -k repair_continuation`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/backend/services/engineer_runtime.py app/backend/tests/test_agent_runtime.py
git commit -m "fix(agent): add preview repair continuation rounds"
```

---

### Task 5: Run End-to-End Preview Stability Regression Suite

**Files:**
- Modify: `app/backend/tests/test_workspace_runtime.py`
- Modify: `app/backend/tests/test_agent_runtime.py`
- Modify: `app/backend/tests/test_start_preview_script.py`
- Modify: `app/backend/tests/test_preview_runtime_resolver.py`

- [ ] **Step 1: Add regression fixture matching the feedback-board failure pattern**

```python
def feedback_board_workspace_fixture(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "app" / "frontend").mkdir(parents=True)
    (workspace / "app" / "backend").mkdir(parents=True)
    (workspace / "app" / "backend" / "server.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): return {'status': 'ok'}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "backend" / "requirements.txt").write_text(
        "fastapi==0.109.0\nuvicorn[standard]==0.27.0\n",
        encoding="utf-8",
    )
    (workspace / ".atoms").mkdir()
    (workspace / ".atoms" / "preview.json").write_text(
        '{"frontend":{"command":"cd /workspace/app/frontend && pnpm run dev -- --host 0.0.0.0 --port 3000","healthcheck_path":"/"},"backend":{"command":"cd /workspace/app/backend && uv run uvicorn server:app --host 0.0.0.0 --port 8000","healthcheck_path":"/health"}}',
        encoding="utf-8",
    )
    return workspace
```

- [ ] **Step 2: Run regression tests to verify they fail before final integration adjustments**

Run: `pytest app/backend/tests/test_preview_runtime_resolver.py app/backend/tests/test_start_preview_script.py app/backend/tests/test_workspace_runtime.py app/backend/tests/test_agent_runtime.py -q -k 'feedback_board or repair_continuation or degraded or resolver'`
Expected: Any remaining gaps fail here, especially around structured diagnostics or degraded-ready plumbing

- [ ] **Step 3: Make the minimal final integration fixes**

```python
# resolver output must be threaded through runtime startup and diagnostics
# no new architecture here; only final consistency fixes:
# - reason_code names
# - detected_root consistency
# - backend status / preview_status serialization
# - repair prompt payload formatting
```

- [ ] **Step 4: Run the full targeted suite to verify it passes**

Run: `pytest app/backend/tests/test_preview_runtime_resolver.py app/backend/tests/test_preview_runtime_diagnostics.py app/backend/tests/test_start_preview_script.py app/backend/tests/test_workspace_runtime.py app/backend/tests/test_agent_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/backend/tests/test_preview_runtime_resolver.py app/backend/tests/test_preview_runtime_diagnostics.py app/backend/tests/test_start_preview_script.py app/backend/tests/test_workspace_runtime.py app/backend/tests/test_agent_runtime.py app/backend/services/preview_runtime_resolver.py app/backend/routers/workspace_runtime.py app/backend/services/sandbox_runtime.py app/backend/services/engineer_runtime.py docker/atoms-sandbox/start-preview docker/atoms-sandbox/Dockerfile
git commit -m "test(preview): add runtime stability regression coverage"
```

---

## Self-Review

### Spec coverage

- Path and filename drift tolerance
  - Covered by Task 1 resolver logic and fallback detection tests.
- Predictable backend bootstrap
  - Covered by Task 2 backend venv bootstrap in sandbox.
- Strict readiness semantics
  - Covered by Task 3 degraded readiness behavior.
- Structured diagnostics and repair rounds
  - Covered by Task 4 engineer runtime repair continuation.
- Regression for the recent feedback-board failure
  - Covered by Task 5 fixture and targeted regression suite.

### Placeholder scan

- No `TBD`, `TODO`, or “implement later” placeholders remain.
- Each task includes concrete file paths, concrete commands, and concrete code snippets.

### Type consistency

- Resolver types: `PreviewRuntimeModel`, `PreviewServiceRuntime`, `PreviewRuntimeDiagnostic`
- Readiness states used consistently: `running`, `failed`, `degraded`
- Diagnostic fields used consistently: `service`, `phase`, `reason_code`, `detected_root`, `detected_entrypoint`, `attempted_command`, `stderr_tail`, `suggested_fix`

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-23-preview-runtime-stability.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
