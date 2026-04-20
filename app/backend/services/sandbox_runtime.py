import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional


RunCommand = Callable[..., Awaitable[tuple[int, str, str]]]


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
    ) -> str:
        resolved_host_root = Path(host_root).resolve()
        self._validate_host_root(resolved_host_root)

        container_name = self._container_name(user_id=user_id, project_id=project_id)
        returncode, stdout, stderr = await self.run_command(
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-v",
            f"{resolved_host_root}:/workspace",
            "-w",
            "/workspace",
            "-p",
            "0:3000",
            "-p",
            "0:8000",
            "atoms-sandbox:latest",
            "sleep",
            "infinity",
        )
        if returncode != 0:
            message = stderr.strip() or stdout.strip() or "docker run failed"
            raise RuntimeError(message)
        return container_name

    async def exec(self, container_name: str, command: str) -> tuple[int, str, str]:
        return await self.run_command(
            "docker",
            "exec",
            "-i",
            container_name,
            "/bin/bash",
            "-lc",
            command,
        )

    async def get_runtime_ports(self, container_name: str) -> dict[str, int | None]:
        returncode, stdout, stderr = await self.run_command("docker", "port", container_name)
        if returncode != 0:
            message = stderr.strip() or stdout.strip() or f"docker port failed for {container_name}"
            raise RuntimeError(message)

        published_ports = self._parse_published_ports(stdout)
        frontend_port = published_ports.get("3000/tcp")
        backend_port = published_ports.get("8000/tcp")
        return {
            "frontend_port": frontend_port,
            "backend_port": backend_port,
            "preview_port": frontend_port or backend_port,
        }

    async def _run_command(self, *command: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return process.returncode or 0, stdout.decode(), stderr.decode()

    def _validate_host_root(self, host_root: Path) -> None:
        try:
            host_root.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(f"host_root must stay within project_root: {host_root}") from exc

    @staticmethod
    def _container_name(user_id: str, project_id: int) -> str:
        return f"atoms-{user_id}-{project_id}"

    @staticmethod
    def _parse_published_ports(output: str) -> dict[str, int]:
        published_ports: dict[str, int] = {}
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or "->" not in line:
                continue

            container_port, host_binding = [part.strip() for part in line.split("->", 1)]
            if container_port in published_ports or ":" not in host_binding:
                continue

            host_port = host_binding.rsplit(":", 1)[-1]
            if host_port.isdigit():
                published_ports[container_port] = int(host_port)

        return published_ports
