import asyncio
import contextlib
import hashlib
import json
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional


RunCommand = Callable[..., Awaitable[tuple[int, str, str]]]


class SandboxRuntimeService:
    SANDBOX_IMAGE = "atoms-sandbox:latest"

    def __init__(
        self,
        project_root: Path,
        run_command: Optional[RunCommand] = None,
        command_timeout_seconds: float = 30.0,
    ):
        self.project_root = Path(project_root).resolve()
        self.run_command = run_command or self._run_command
        self.command_timeout_seconds = command_timeout_seconds

    async def ensure_runtime(
        self,
        user_id: str,
        project_id: int,
        host_root: Path,
    ) -> str:
        resolved_host_root = Path(host_root).resolve()
        self._validate_host_root(resolved_host_root)
        if not resolved_host_root.exists() or not resolved_host_root.is_dir():
            raise ValueError(f"host_root must exist as a directory: {resolved_host_root}")

        container_name = self._container_name(user_id=user_id, project_id=project_id)
        returncode, stdout, stderr = await self._invoke(
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
            self.SANDBOX_IMAGE,
            "sleep",
            "infinity",
        )
        if returncode == 0:
            return container_name

        message = stderr.strip() or stdout.strip() or "docker run failed"
        if "is already in use" in message:
            runtime_identity = await self.inspect_runtime(container_name)
            workspace_source = self._normalize_existing_path(runtime_identity.get("workspace_source"))
            image_id = runtime_identity.get("image_id")
            expected_image_id = await self.inspect_image_id(self.SANDBOX_IMAGE)
            has_required_ports = {"3000/tcp", "8000/tcp"}.issubset(runtime_identity.get("port_bindings", set()))
            if (
                workspace_source != str(resolved_host_root)
                or image_id != expected_image_id
                or runtime_identity.get("working_dir") != "/workspace"
                or runtime_identity.get("command") != ["sleep", "infinity"]
                or not has_required_ports
            ):
                raise RuntimeError(
                    f"existing container {container_name} does not match requested workspace or image"
                )

            start_returncode, start_stdout, start_stderr = await self._invoke(
                "docker",
                "start",
                container_name,
            )
            if start_returncode == 0:
                return container_name

            start_message = start_stderr.strip() or start_stdout.strip() or "docker start failed"
            if "already running" in start_message:
                return container_name
            raise RuntimeError(start_message)

        if returncode != 0:
            raise RuntimeError(message)
        return container_name

    async def exec(self, container_name: str, command: str) -> tuple[int, str, str]:
        return await self._invoke(
            "docker",
            "exec",
            "-i",
            container_name,
            "/bin/bash",
            "-lc",
            command,
        )

    async def get_runtime_ports(self, container_name: str) -> dict[str, int | None]:
        returncode, stdout, stderr = await self._invoke("docker", "port", container_name)
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

    async def inspect_runtime(self, container_name: str) -> dict[str, str | None]:
        returncode, stdout, stderr = await self._invoke("docker", "inspect", container_name)
        if returncode != 0:
            message = stderr.strip() or stdout.strip() or f"docker inspect failed for {container_name}"
            raise RuntimeError(message)

        payload = json.loads(stdout or "[]")
        container = payload[0] if payload else {}
        mounts = container.get("Mounts") or []
        workspace_source = None
        for mount in mounts:
            if mount.get("Destination") == "/workspace":
                workspace_source = mount.get("Source")
                break
        port_bindings = set((container.get("HostConfig", {}).get("PortBindings") or {}).keys())

        return {
            "image": container.get("Config", {}).get("Image"),
            "image_id": container.get("Image"),
            "workspace_source": workspace_source,
            "working_dir": container.get("Config", {}).get("WorkingDir"),
            "command": container.get("Config", {}).get("Cmd") or [],
            "port_bindings": port_bindings,
        }

    async def inspect_image_id(self, image_ref: str) -> str | None:
        returncode, stdout, stderr = await self._invoke("docker", "image", "inspect", image_ref)
        if returncode != 0:
            message = stderr.strip() or stdout.strip() or f"docker image inspect failed for {image_ref}"
            raise RuntimeError(message)

        payload = json.loads(stdout or "[]")
        image = payload[0] if payload else {}
        return image.get("Id")

    async def _invoke(self, *command: str) -> tuple[int, str, str]:
        try:
            return await asyncio.wait_for(
                self.run_command(*command),
                timeout=self.command_timeout_seconds,
            )
        except TimeoutError as exc:
            raise RuntimeError(f"command timed out after {self.command_timeout_seconds}s: {' '.join(command)}") from exc

    async def _run_command(self, *command: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
            raise
        return process.returncode or 0, stdout.decode(), stderr.decode()

    def _validate_host_root(self, host_root: Path) -> None:
        try:
            host_root.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(f"host_root must stay within project_root: {host_root}") from exc

    @staticmethod
    def _container_name(user_id: str, project_id: int) -> str:
        lowered_user_id = user_id.lower()
        sanitized_user_id = re.sub(r"[^a-z0-9_.-]+", "-", lowered_user_id).strip("-.")
        if not sanitized_user_id:
            sanitized_user_id = "user"

        suffix = f"-{project_id}"
        base_prefix = "atoms-"
        max_name_length = 128
        simple_name = f"{base_prefix}{sanitized_user_id}{suffix}"
        if len(simple_name) <= max_name_length and sanitized_user_id == user_id:
            return simple_name

        digest = hashlib.sha1(user_id.encode("utf-8")).hexdigest()[:12]
        hashed_suffix = f"-{digest}{suffix}"
        max_user_length = max_name_length - len(base_prefix) - len(hashed_suffix)
        truncated_user_id = sanitized_user_id[:max_user_length].rstrip("-.") or "user"
        return f"{base_prefix}{truncated_user_id}{hashed_suffix}"

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

    @staticmethod
    def _normalize_existing_path(path_value: str | None) -> str | None:
        if not path_value:
            return None

        try:
            return str(Path(path_value).resolve())
        except Exception:
            return path_value
