import asyncio
import os
import shlex
from pathlib import Path
from typing import Optional

from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.tool.base import BaseTool, CLIResult
from openmanus_runtime.tool.file_operators import validate_workspace_write_path


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


_WRITE_REDIRECTION_TOKENS = {">", ">>", ">|", "1>", "1>>", "2>", "2>>", "&>", "&>>"}
_WRITE_COMMANDS_WITH_DESTINATION = {"cp", "mv", "ln", "install"}
_WRITE_COMMANDS_WITH_TARGETS = {"touch", "mkdir", "rm", "rmdir", "tee"}
_IN_PLACE_EDIT_COMMANDS = {"sed", "perl"}


def _is_option(token: str) -> bool:
    return token.startswith("-")


def _extract_targets(command: str) -> list[Path]:
    tokens = shlex.split(command)
    if not tokens:
        return []

    targets: list[Path] = []
    command_name = tokens[0]
    idx = 1
    while idx < len(tokens):
        token = tokens[idx]
        matched_redirection = next(
            (redir for redir in sorted(_WRITE_REDIRECTION_TOKENS, key=len, reverse=True) if token == redir or token.startswith(redir)),
            None,
        )
        if matched_redirection is not None:
            remainder = token[len(matched_redirection) :]
            if remainder:
                targets.append(Path(remainder))
            elif idx + 1 < len(tokens):
                targets.append(Path(tokens[idx + 1]))
            idx += 2
            continue
        if token in _WRITE_REDIRECTION_TOKENS:
            if idx + 1 < len(tokens):
                targets.append(Path(tokens[idx + 1]))
            idx += 2
            continue
        idx += 1

    positional_tokens = [token for token in tokens[1:] if not _is_option(token)]

    if command_name in _WRITE_COMMANDS_WITH_DESTINATION and positional_tokens:
        targets.append(Path(positional_tokens[-1]))
    elif command_name in _WRITE_COMMANDS_WITH_TARGETS:
        targets.extend(Path(token) for token in positional_tokens)
    elif command_name in _IN_PLACE_EDIT_COMMANDS and any(
        token == "-i" or token.startswith("-i") for token in tokens[1:]
    ) and positional_tokens:
        targets.append(Path(positional_tokens[-1]))

    return targets


def _validate_bash_write_targets(command: str) -> None:
    for target in _extract_targets(command):
        if str(target).startswith("/workspace"):
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

    def __init__(self, runtime_service, container_name: str):
        self.runtime_service = runtime_service
        self.container_name = container_name

    async def run(self, command: str) -> CLIResult:
        _validate_bash_write_targets(command)
        returncode, stdout, stderr = await self.runtime_service.exec(
            self.container_name,
            f"cd /workspace && {command}",
        )
        return CLIResult(output=stdout.rstrip(), error=stderr.rstrip(), system=str(returncode))


class ContainerBash(Bash):
    """A Bash tool variant that executes commands inside a Docker container."""

    _container_session: Optional[ContainerBashSession] = None

    @classmethod
    def with_session(cls, runtime_service, container_name: str) -> "ContainerBash":
        tool = cls()
        tool._container_session = ContainerBashSession(runtime_service, container_name)
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
