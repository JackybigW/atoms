import os
import subprocess
import time
from pathlib import Path


def _wait_for_file(path: Path, timeout_seconds: float = 5.0) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if path.exists():
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {path}")


def test_start_dev_passes_vite_flags_without_extra_separator(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "package.json").write_text('{"name":"test","scripts":{"dev":"vite"}}', encoding="utf-8")
    (workspace_root / "node_modules").mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_file = tmp_path / "pnpm-args.txt"
    fake_pnpm = bin_dir / "pnpm"
    fake_pnpm.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f'printf "%s\\n" "$@" > "{args_file}"',
            ]
        ),
        encoding="utf-8",
    )
    fake_pnpm.chmod(0o755)

    script_path = Path(__file__).resolve().parents[3] / "docker" / "atoms-sandbox" / "start-dev"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["ATOMS_PROJECT_ID"] = "test-project"
    env["ATOMS_WORKSPACE_ROOT"] = str(workspace_root)
    env["ATOMS_PREVIEW_FRONTEND_BASE"] = "/preview/test/frontend/"

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    captured_args = _wait_for_file(args_file).splitlines()
    assert captured_args == [
        "run",
        "dev",
        "--host",
        "0.0.0.0",
        "--port",
        "3000",
        "--base",
        "/preview/test/frontend/",
    ]


def test_start_dev_launches_placeholder_when_package_json_is_missing(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_file = tmp_path / "python3-args.txt"
    fake_python = bin_dir / "python3"
    fake_python.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "-" ]]; then',
                "  exit 0",
                "fi",
                f'printf "%s\\n" "$@" > "{args_file}"',
            ]
        ),
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    script_path = Path(__file__).resolve().parents[3] / "docker" / "atoms-sandbox" / "start-dev"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["ATOMS_PROJECT_ID"] = "test-project"
    env["ATOMS_WORKSPACE_ROOT"] = str(workspace_root)

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "start-dev: launched placeholder preview" in result.stdout
    assert "/tmp/atoms-placeholder-server.py" in _wait_for_file(args_file)
