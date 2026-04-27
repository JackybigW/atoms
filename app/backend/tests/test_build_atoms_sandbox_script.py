import os
import subprocess
from pathlib import Path


def test_build_atoms_sandbox_retries_and_refreshes_clash(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "build-atoms-sandbox.sh"
    state_dir = tmp_path / "state"
    bin_dir = tmp_path / "bin"
    state_dir.mkdir()
    bin_dir.mkdir()

    (bin_dir / "docker").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
count_file="${STATE_DIR}/docker-count"
count=0
if [[ -f "${count_file}" ]]; then
  count="$(cat "${count_file}")"
fi
count=$((count + 1))
echo "${count}" > "${count_file}"
printf '%s\\n' "$*" >> "${STATE_DIR}/docker-args"
if [[ "${count}" -lt 3 ]]; then
  exit 1
fi
exit 0
""",
        encoding="utf-8",
    )
    (bin_dir / "curl").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "${STATE_DIR}/curl-args"
exit 0
""",
        encoding="utf-8",
    )
    (bin_dir / "docker").chmod(0o755)
    (bin_dir / "curl").chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STATE_DIR": str(state_dir),
        "ATOMS_SANDBOX_MAX_ATTEMPTS": "3",
        "ATOMS_SANDBOX_IMAGE_TAG": "atoms-sandbox:test-retry",
    }

    result = subprocess.run(
        [str(script_path)],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (state_dir / "docker-count").read_text(encoding="utf-8").strip() == "3"

    docker_args = (state_dir / "docker-args").read_text(encoding="utf-8")
    assert "build --network=host" in docker_args
    assert "--build-arg http_proxy=http://127.0.0.1:7890" in docker_args
    assert "-t atoms-sandbox:test-retry docker/atoms-sandbox" in docker_args

    curl_args = (state_dir / "curl-args").read_text(encoding="utf-8").splitlines()
    assert len(curl_args) == 2
    assert all("http://127.0.0.1:9090/proxies/Auto/delay" in args for args in curl_args)
