import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional


RunCommand = Callable[..., Awaitable[str]]


class SandboxRuntimeService:
    def __init__(
        self,
        project_root: Path,
        run_command: Optional[RunCommand] = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.run_command = run_command or self._run_command

    async def ensure_runtime(
        self,
        user_id: str,
        project_id: int,
        host_root: Path,
    ) -> dict[str, str]:
        resolved_host_root = Path(host_root).resolve()
        self._validate_host_root(resolved_host_root)

        container_name = self._container_name(user_id=user_id, project_id=project_id)
        container_id = await self.run_command(
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-v",
            f"{resolved_host_root}:/workspace",
            "-w",
            "/workspace",
            "python:3.12-slim",
            "sleep",
            "infinity",
        )
        return {
            "container_name": container_name,
            "container_id": container_id,
            "status": "running",
        }

    async def exec(self, container_name: str, command: str) -> str:
        return await self.run_command(
            "docker",
            "exec",
            "-i",
            container_name,
            "/bin/bash",
            "-lc",
            command,
        )

    async def _run_command(self, *command: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode().strip() or stdout.decode().strip() or "command failed"
            raise RuntimeError(message)
        return stdout.decode().strip()

    def _validate_host_root(self, host_root: Path) -> None:
        try:
            host_root.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(f"host_root must stay within project_root: {host_root}") from exc

    @staticmethod
    def _container_name(user_id: str, project_id: int) -> str:
        return f"atoms-{user_id}-{project_id}"
