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

    fake_script = tmp_path / "start-preview"
    original = START_PREVIEW_SCRIPT.read_text(encoding="utf-8")
    patched = original.replace('WORKSPACE_ROOT="/workspace"', f'WORKSPACE_ROOT="{workspace_root}"')
    patched = patched.replace('Path("/workspace/.atoms/preview.json")', f'Path("{workspace_root}/.atoms/preview.json")')
    patched = patched.replace("/usr/local/bin/start-dev", str(fake_start_dev))
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
