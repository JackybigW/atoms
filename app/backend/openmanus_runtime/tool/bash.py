import asyncio
import os
import re
from pathlib import Path
from typing import Optional

from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.tool.base import BaseTool, CLIResult
from openmanus_runtime.tool.file_operators import normalize_workspace_path, validate_workspace_write_path


_BASH_DESCRIPTION = """Execute a bash command in the terminal.
* Long running commands: For commands that may run indefinitely, it should be run in the background and the output should be redirected to a file, e.g. command = `python3 app.py > server.log 2>&1 &`.
* Interactive: If a bash command returns exit code `-1`, this means the process is not yet finished. The assistant must then send a second call to terminal with an empty `command` (which will retrieve any additional logs), or it can send additional text (set `command` to the text) to STDIN of the running process, or it can send command=`ctrl+c` to interrupt the process.
* Timeout: If a command execution result says "Command timed out. Sending SIGINT to the process", the assistant should retry running the command in the background.
"""


class _BashSession:
    """A session of a bash shell."""

    _started: bool
    _process: asyncio.subprocess.Process

    command: str = "/bin/bash"
    _output_delay: float = 0.2  # seconds
    _timeout: float = 120.0  # seconds
    _sentinel: str = "<<exit>>"

    def __init__(self):
        self._started = False
        self._timed_out = False

    async def start(self):
        if self._started:
            return

        self._process = await asyncio.create_subprocess_shell(
            self.command,
            preexec_fn=os.setsid,
            shell=True,
            bufsize=0,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._started = True

    def stop(self):
        """Terminate the bash shell."""
        if not self._started:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return
        self._process.terminate()

    async def run(self, command: str):
        """Execute a command in the bash shell."""
        if not self._started:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return CLIResult(
                system="tool must be restarted",
                error=f"bash has exited with returncode {self._process.returncode}",
            )
        if self._timed_out:
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            )

        _validate_bash_write_targets(command)

        # we know these are not None because we created the process with PIPEs
        assert self._process.stdin
        assert self._process.stdout
        assert self._process.stderr

        # send command to the process
        self._process.stdin.write(
            command.encode() + f"; echo '{self._sentinel}'\n".encode()
        )
        await self._process.stdin.drain()

        # read output from the process, until the sentinel is found
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    await asyncio.sleep(self._output_delay)
                    # if we read directly from stdout/stderr, it will wait forever for
                    # EOF. use the StreamReader buffer directly instead.
                    output = (
                        self._process.stdout._buffer.decode()
                    )  # pyright: ignore[reportAttributeAccessIssue]
                    if self._sentinel in output:
                        # strip the sentinel and break
                        output = output[: output.index(self._sentinel)]
                        break
        except asyncio.TimeoutError:
            self._timed_out = True
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            ) from None

        if output.endswith("\n"):
            output = output[:-1]

        error = (
            self._process.stderr._buffer.decode()
        )  # pyright: ignore[reportAttributeAccessIssue]
        if error.endswith("\n"):
            error = error[:-1]

        # clear the buffers so that the next output can be read correctly
        self._process.stdout._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]
        self._process.stderr._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]

        return CLIResult(output=output, error=error)


_WRITE_INTENT_RE = re.compile(
    r"(?:"
    r"\b(?:cp|mv|ln|install|touch|mkdir|rm|rmdir|tee)\b"
    r"|"
    r"\b(?:sed|perl)\b[^\n;|&]*?\s-i(?:\b|$)"
    r"|"
    r"\bopen\s*\([^)]*['\"](?:w|a|x)(?:\+|b)?['\"]"
    r"|"
    r"\.write(?:_text|_bytes)?\s*\("
    r"|"
    r"(?:^|[^\S\r\n])(?:>>?|>\||&>>?|&>)\s*"
    r")",
    re.IGNORECASE,
)
_HIGHRISK_INLINE_RE = re.compile(r"\b(?:sh|bash|python3?|perl|ruby|node)\s+-[a-z]*c\b", re.IGNORECASE)
_HEREDOC_RE = re.compile(r"<<[-~]?(?:'[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_]*)")
_WORKSPACE_PATH_LITERAL_RE = re.compile(r"(/workspace(?:/[^\s'\"`;&|<>]+)+)")
_RELATIVE_WORKSPACE_PATH_RE = re.compile(r"((?:app/(?:frontend|backend)|docs|\.atoms)(?:/[^\s'\"`;&|<>]+)+)")
_BACKEND_UV_INSTALL_RE = re.compile(
    r"^\s*cd\s+(/workspace/(?:app/)?backend)\s*&&\s*uv\s+pip\s+install\b.*(?:-r|--requirements)\s+(?:requirements\.txt|/workspace/(?:app/)?backend/requirements\.txt).*$"
)
_FRONTEND_PNPM_INSTALL_RE = re.compile(
    r"^\s*cd\s+(/workspace/(?:app/)?frontend|/workspace)\s*&&\s*pnpm\s+install\b.*$"
)


def _rewrite_dependency_install_command(command: str) -> tuple[str, bool]:
    backend_match = _BACKEND_UV_INSTALL_RE.match(command)
    if backend_match:
        return f"/usr/local/bin/atoms-deps-cache backend install {backend_match.group(1)}", True

    frontend_match = _FRONTEND_PNPM_INSTALL_RE.match(command)
    if frontend_match:
        return f"/usr/local/bin/atoms-deps-cache frontend install {frontend_match.group(1)}", True

    return command, False


def _validate_bash_write_targets(command: str, approval_gate=None) -> None:
    if not _WRITE_INTENT_RE.search(command):
        return

    if _HIGHRISK_INLINE_RE.search(command) or _HEREDOC_RE.search(command):
        raise ToolError(
            "Inline interpreter and heredoc writes are not allowed in bash. "
            "Use str_replace_editor for workspace file mutations."
        )

    workspace_targets = [normalize_workspace_path(match.group(1)) for match in _WORKSPACE_PATH_LITERAL_RE.finditer(command)]
    relative_targets = [
        normalize_workspace_path(f"/workspace/{match.group(1)}")
        for match in _RELATIVE_WORKSPACE_PATH_RE.finditer(command)
    ]
    candidate_targets = workspace_targets + relative_targets
    if not candidate_targets:
        raise ToolError(
            "Workspace write target could not be validated. "
            "Use a direct absolute /workspace path or str_replace_editor."
        )
    for target in candidate_targets:
        if approval_gate is not None:
            approval_gate.check_write(target)
        validate_workspace_write_path(target)


class Bash(BaseTool):
    """A tool for executing bash commands"""

    name: str = "bash"
    description: str = _BASH_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute. Can be empty to view additional logs when previous exit code is `-1`. Can be `ctrl+c` to interrupt the currently running process.",
            },
        },
        "required": ["command"],
    }

    _session: Optional[_BashSession] = None

    async def execute(
        self, command: str | None = None, restart: bool = False, **kwargs
    ) -> CLIResult:
        if restart:
            if self._session:
                self._session.stop()
            self._session = _BashSession()
            await self._session.start()

            return CLIResult(system="tool has been restarted.")

        if self._session is None:
            self._session = _BashSession()
            await self._session.start()

        if command is not None:
            return await self._session.run(command)

        raise ToolError("no command provided.")


class ContainerBashSession:
    """A bash session that delegates command execution to a running Docker container."""

    def __init__(self, runtime_service, container_name: str, approval_gate=None, telemetry_sink=None):
        self.runtime_service = runtime_service
        self.container_name = container_name
        self.approval_gate = approval_gate
        self.telemetry_sink = telemetry_sink
        self.executed_commands: list[str] = []

    def has_verification_run(self) -> bool:
        """Returns True if the agent has executed any bash commands (likely for verification)."""
        return len(self.executed_commands) > 0

    async def run(self, command: str) -> CLIResult:
        rewritten_command, rewritten = _rewrite_dependency_install_command(command)
        if not rewritten:
            _validate_bash_write_targets(command, approval_gate=self.approval_gate)
        self.executed_commands.append(command)
        returncode, stdout, stderr = await self.runtime_service.exec(
            self.container_name,
            f"cd /workspace && {rewritten_command}",
        )
        if self.telemetry_sink is not None:
            self.telemetry_sink(
                {
                    "name": "bash.command",
                    "category": "bash",
                    "attrs": {
                        "command": command,
                        "rewritten": rewritten,
                        "returncode": returncode,
                    },
                }
            )
        return CLIResult(output=stdout.rstrip(), error=stderr.rstrip(), system=str(returncode))


class ContainerBash(Bash):
    """A Bash tool variant that executes commands inside a Docker container."""

    _container_session: Optional[ContainerBashSession] = None

    @classmethod
    def with_session(cls, runtime_service, container_name: str, approval_gate=None, telemetry_sink=None) -> "ContainerBash":
        tool = cls()
        tool._container_session = ContainerBashSession(
            runtime_service,
            container_name,
            approval_gate=approval_gate,
            telemetry_sink=telemetry_sink,
        )
        return tool

    @classmethod
    def with_existing_session(cls, session: ContainerBashSession) -> "ContainerBash":
        """Wrap an existing ContainerBashSession so executed_commands tracking is shared."""
        tool = cls()
        tool._container_session = session
        return tool

    async def execute(
        self, command: str | None = None, restart: bool = False, **kwargs
    ) -> CLIResult:
        if self._container_session is None:
            return await super().execute(command=command, restart=restart, **kwargs)

        if command is None:
            raise ToolError("no command provided.")

        return await self._container_session.run(command)


if __name__ == "__main__":
    bash = Bash()
    rst = asyncio.run(bash.execute("ls -l"))
    print(rst)
