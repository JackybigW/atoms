import json
import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
START_PREVIEW_SCRIPT = REPO_ROOT / "docker" / "atoms-sandbox" / "start-preview"


def _wait_for_file(path: Path, timeout_seconds: float = 5.0) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if path.exists():
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {path}")


def test_start_preview_delegates_cd_prefixed_pnpm_dev_to_start_dev(tmp_path):
    workspace_root = tmp_path / "workspace"
    frontend_root = workspace_root / "app" / "frontend"
    frontend_root.mkdir(parents=True)
    (frontend_root / "package.json").write_text(
        '{"name":"test","scripts":{"dev":"vite"}}',
        encoding="utf-8",
    )
    (frontend_root / "node_modules").mkdir()

    atoms_dir = workspace_root / ".atoms"
    atoms_dir.mkdir()
    (atoms_dir / "preview.json").write_text(
        """
{
  "frontend": {
    "command": "cd /workspace/app/frontend && pnpm run dev -- --host 0.0.0.0 --port 3000",
    "healthcheck_path": "/"
  }
}
""".strip(),
        encoding="utf-8",
    )

    marker_file = tmp_path / "start-dev-marker.txt"
    fake_start_dev = tmp_path / "fake-start-dev.sh"
    fake_start_dev.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'printf "%s\\n" "${{ATOMS_PREVIEW_FRONTEND_BASE:-missing}}" > "{marker_file}"',
            ]
        ),
        encoding="utf-8",
    )
    fake_start_dev.chmod(0o755)
    fake_cache = tmp_path / "atoms-deps-cache"
    fake_cache.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_cache.chmod(0o755)

    fake_script = tmp_path / "start-preview"
    original = START_PREVIEW_SCRIPT.read_text(encoding="utf-8")
    patched = original.replace('WORKSPACE_ROOT="/workspace"', f'WORKSPACE_ROOT="{workspace_root}"')
    patched = patched.replace('Path("/workspace/.atoms/preview.json")', f'Path("{workspace_root}/.atoms/preview.json")')
    patched = patched.replace("/usr/local/bin/start-dev", str(fake_start_dev))
    patched = patched.replace("/usr/local/bin/atoms-deps-cache", str(fake_cache))
    kill_fn_start = patched.index("kill_listeners_on_port() {")
    install_fn_start = patched.index("install_node_deps_if_needed() {")
    patched = (
        patched[:kill_fn_start]
        + 'kill_listeners_on_port() {\n  :\n}\n\n'
        + patched[install_fn_start:]
    )
    fake_script.write_text(patched, encoding="utf-8")
    fake_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(fake_script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "ATOMS_PREVIEW_FRONTEND_BASE": "/preview/test/frontend/",
            "ATOMS_PREVIEW_BACKEND_BASE": "/preview/test/backend/",
            "VITE_ATOMS_PREVIEW_FRONTEND_BASE": "/preview/test/frontend/",
            "VITE_ATOMS_PREVIEW_BACKEND_BASE": "/preview/test/backend/",
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _wait_for_file(marker_file).strip() == "/preview/test/frontend/"


def test_start_preview_delegates_missing_frontend_to_placeholder_start_dev(tmp_path):
    workspace_root = tmp_path / "workspace"
    frontend_root = workspace_root / "app" / "frontend"
    frontend_root.mkdir(parents=True)
    backend_root = workspace_root / "app" / "backend"
    backend_root.mkdir(parents=True)
    (backend_root / "requirements.txt").write_text("", encoding="utf-8")

    atoms_dir = workspace_root / ".atoms"
    atoms_dir.mkdir()
    (atoms_dir / "preview.json").write_text(
        json.dumps({
            "frontend": {
                "command": "cd /workspace/app/frontend && npm run preview",
                "healthcheck_path": "/"
            },
            "backend": {
                "command": "cd /workspace/app/backend && uv run uvicorn server:app --host 0.0.0.0 --port 8000",
                "healthcheck_path": "/health"
            }
        }),
        encoding="utf-8",
    )

    start_dev_marker = tmp_path / "start-dev-marker.txt"
    backend_cmd_marker = tmp_path / "backend-cmd.txt"
    fake_start_dev = tmp_path / "fake-start-dev.sh"
    fake_start_dev.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'printf "called" > "{start_dev_marker}"',
            ]
        ),
        encoding="utf-8",
    )
    fake_start_dev.chmod(0o755)

    original = START_PREVIEW_SCRIPT.read_text(encoding="utf-8")
    patched = original.replace('WORKSPACE_ROOT="/workspace"', f'WORKSPACE_ROOT="{workspace_root}"')
    patched = patched.replace('Path("/workspace/.atoms/preview.json")', f'Path("{workspace_root}/.atoms/preview.json")')
    patched = patched.replace("/usr/local/bin/start-dev", str(fake_start_dev))
    patched = patched.replace(
        'nohup bash -lc "${BACKEND_COMMAND}" >"${BACKEND_LOG}" 2>&1 &',
        f'printf "%s" "${{BACKEND_COMMAND}}" > "{backend_cmd_marker}"',
    )
    kill_fn_start = patched.index("kill_listeners_on_port() {")
    install_fn_start = patched.index("install_node_deps_if_needed() {")
    patched = (
        patched[:kill_fn_start]
        + 'kill_listeners_on_port() {\n  :\n}\n\n'
        + patched[install_fn_start:]
    )
    ensure_venv_fn_start = patched.index("ensure_backend_venv() {")
    ensure_venv_fn_end = patched.index("\n}", ensure_venv_fn_start) + 2
    patched = (
        patched[:ensure_venv_fn_start]
        + 'ensure_backend_venv() {\n  :\n}\n'
        + patched[ensure_venv_fn_end:]
    )

    fake_script = tmp_path / "start-preview-missing-frontend-test"
    fake_script.write_text(patched, encoding="utf-8")
    fake_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(fake_script)],
        capture_output=True,
        text=True,
        env={**os.environ},
        check=False,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert _wait_for_file(start_dev_marker) == "called"
    assert "uvicorn server:app" in _wait_for_file(backend_cmd_marker)


def test_start_preview_normalizes_uv_run_backend_command_and_sets_backend_root(tmp_path):
    """start-preview should detect a backend root with requirements.txt and normalize uv run commands."""
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
    frontend_root = workspace_root / "app" / "frontend"
    frontend_root.mkdir(parents=True)
    (frontend_root / "package.json").write_text('{"name":"test","scripts":{"dev":"vite"}}', encoding="utf-8")
    (frontend_root / "node_modules").mkdir()

    atoms_dir = workspace_root / ".atoms"
    atoms_dir.mkdir()
    (atoms_dir / "preview.json").write_text(
        json.dumps({
            "frontend": {
                "command": "cd /workspace/app/frontend && pnpm run dev -- --host 0.0.0.0 --port 3000",
                "healthcheck_path": "/"
            },
            "backend": {
                "command": f"cd /workspace/app/backend && uv run uvicorn server:app --host 0.0.0.0 --port 8000",
                "healthcheck_path": "/health"
            }
        }),
        encoding="utf-8",
    )

    # Capture the normalized backend command by writing it to a marker file from a fake backend launcher
    backend_cmd_marker = tmp_path / "backend-cmd.txt"

    fake_start_dev = tmp_path / "fake-start-dev.sh"
    fake_start_dev.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_start_dev.chmod(0o755)
    fake_cache = tmp_path / "atoms-deps-cache"
    fake_cache.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_cache.chmod(0o755)

    original = START_PREVIEW_SCRIPT.read_text(encoding="utf-8")
    patched = original.replace('WORKSPACE_ROOT="/workspace"', f'WORKSPACE_ROOT="{workspace_root}"')
    patched = patched.replace('Path("/workspace/.atoms/preview.json")', f'Path("{workspace_root}/.atoms/preview.json")')
    patched = patched.replace("/usr/local/bin/start-dev", str(fake_start_dev))
    patched = patched.replace("/usr/local/bin/atoms-deps-cache", str(fake_cache))

    # Replace the backend nohup launch with a marker write instead
    patched = patched.replace(
        'nohup bash -lc "${BACKEND_COMMAND}" >"${BACKEND_LOG}" 2>&1 &',
        f'printf "%s" "${{BACKEND_COMMAND}}" > "{backend_cmd_marker}"',
    )

    # Stub out kill_listeners_on_port and ensure_backend_venv (no actual pip install)
    kill_fn_start = patched.index("kill_listeners_on_port() {")
    install_fn_start = patched.index("install_node_deps_if_needed() {")
    patched = (
        patched[:kill_fn_start]
        + 'kill_listeners_on_port() {\n  :\n}\n\n'
        + patched[install_fn_start:]
    )
    # Stub ensure_backend_venv to skip actual pip (just create the venv binary stub)
    ensure_venv_fn_start = patched.index("ensure_backend_venv() {")
    ensure_venv_fn_end = patched.index("\n}", ensure_venv_fn_start) + 2
    stub_venv = (
        "ensure_backend_venv() {\n"
        "  local backend_root=\"$1\"\n"
        "  local venv_dir=\"${backend_root}/.venv\"\n"
        "  mkdir -p \"${venv_dir}/bin\"\n"
        f'  printf "#!/usr/bin/env bash\\nexec python3 \\"\\$@\\"\\n" > "${{venv_dir}}/bin/python"\n'
        "  chmod +x \"${venv_dir}/bin/python\"\n"
        "}\n"
    )
    patched = patched[:ensure_venv_fn_start] + stub_venv + patched[ensure_venv_fn_end:]

    fake_script = tmp_path / "start-preview-backend-test"
    fake_script.write_text(patched, encoding="utf-8")
    fake_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(fake_script)],
        capture_output=True,
        text=True,
        env={**os.environ},
        check=False,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert backend_cmd_marker.exists(), "Backend command marker was not written — backend was not launched"
    backend_cmd = backend_cmd_marker.read_text(encoding="utf-8")
    assert "uv run" not in backend_cmd, f"Expected uv run to be normalized away, got: {backend_cmd}"
    assert "uvicorn server:app" in backend_cmd, f"Expected server:app import target, got: {backend_cmd}"
    assert ".venv/bin/python" in backend_cmd, f"Expected venv python, got: {backend_cmd}"


def test_start_preview_uses_dependency_cache_for_backend(tmp_path):
    workspace_root = tmp_path / "workspace"
    frontend_root = workspace_root / "app" / "frontend"
    frontend_root.mkdir(parents=True)
    (frontend_root / "package.json").write_text('{"name":"test","scripts":{"dev":"vite"}}', encoding="utf-8")
    (frontend_root / "node_modules").mkdir()
    backend_root = workspace_root / "app" / "backend"
    backend_root.mkdir(parents=True)
    (backend_root / "requirements.txt").write_text("fastapi\nuvicorn[standard]\n", encoding="utf-8")

    atoms_dir = workspace_root / ".atoms"
    atoms_dir.mkdir()
    (atoms_dir / "preview.json").write_text(
        json.dumps(
            {
                "frontend": {
                    "command": "cd /workspace/app/frontend && pnpm run dev -- --host 0.0.0.0 --port 3000",
                    "healthcheck_path": "/",
                },
                "backend": {
                    "command": "cd /workspace/app/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000",
                    "healthcheck_path": "/health",
                },
            }
        ),
        encoding="utf-8",
    )

    cache_args = tmp_path / "cache-args.txt"
    fake_cache = tmp_path / "atoms-deps-cache"
    fake_cache.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f'printf "%s\\n" "$@" >> "{cache_args}"',
                'if [[ "$1" == "backend" ]]; then mkdir -p "$3/.venv/bin"; printf "#!/usr/bin/env bash\\nexec python3 \\"\\$@\\"\\n" > "$3/.venv/bin/python"; chmod +x "$3/.venv/bin/python"; fi',
            ]
        ),
        encoding="utf-8",
    )
    fake_cache.chmod(0o755)

    original = START_PREVIEW_SCRIPT.read_text(encoding="utf-8")
    patched = original.replace('WORKSPACE_ROOT="/workspace"', f'WORKSPACE_ROOT="{workspace_root}"')
    patched = patched.replace('Path("/workspace/.atoms/preview.json")', f'Path("{workspace_root}/.atoms/preview.json")')
    patched = patched.replace("/usr/local/bin/start-dev", "true")
    patched = patched.replace("/usr/local/bin/atoms-deps-cache", str(fake_cache))
    patched = patched.replace('nohup bash -lc "${BACKEND_COMMAND}" >"${BACKEND_LOG}" 2>&1 &', ":")
    kill_fn_start = patched.index("kill_listeners_on_port() {")
    install_fn_start = patched.index("install_node_deps_if_needed() {")
    patched = patched[:kill_fn_start] + "kill_listeners_on_port() {\n  :\n}\n\n" + patched[install_fn_start:]

    fake_script = tmp_path / "start-preview-cache-test"
    fake_script.write_text(patched, encoding="utf-8")
    fake_script.chmod(0o755)

    result = subprocess.run(["bash", str(fake_script)], capture_output=True, text=True, env=os.environ.copy(), check=False)

    assert result.returncode == 0, result.stderr
    assert "backend\ninstall\n" in _wait_for_file(cache_args)
    assert str(backend_root) in cache_args.read_text(encoding="utf-8")
