import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional


RunCommand = Callable[..., Awaitable[tuple[int, str, str]]]
logger = logging.getLogger(__name__)
_SANDBOX_PROXY_ENV_MAP = {
    "ATOMS_SANDBOX_HTTP_PROXY": ("HTTP_PROXY", "http_proxy"),
    "ATOMS_SANDBOX_HTTPS_PROXY": ("HTTPS_PROXY", "https_proxy"),
    "ATOMS_SANDBOX_ALL_PROXY": ("ALL_PROXY", "all_proxy"),
    "ATOMS_SANDBOX_NO_PROXY": ("NO_PROXY", "no_proxy"),
}
_SANDBOX_PROXY_ENV_KEYS = {
    target_key
    for target_keys in _SANDBOX_PROXY_ENV_MAP.values()
    for target_key in target_keys
}


class SandboxRuntimeService:
    SANDBOX_IMAGE = "atoms-sandbox:latest"

    def __init__(
        self,
        project_root: Path,
        run_command: Optional[RunCommand] = None,
        command_timeout_seconds: float = 30.0,
        exec_timeout_seconds: float = 180.0,
        install_timeout_seconds: Optional[float] = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.run_command = run_command or self._run_command
        self.command_timeout_seconds = command_timeout_seconds
        self.exec_timeout_seconds = exec_timeout_seconds
        self.install_timeout_seconds = (
            install_timeout_seconds
            if install_timeout_seconds is not None
            else float(os.environ.get("ATOMS_SANDBOX_INSTALL_TIMEOUT_SECONDS", "600"))
        )

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
        returncode, stdout, stderr = await self._run_container(
            container_name=container_name,
            resolved_host_root=resolved_host_root,
            project_id=project_id,
        )
        if returncode == 0:
            return container_name

        message = stderr.strip() or stdout.strip() or "docker run failed"
        if "is already in use" in message:
            logger.info("sandbox container name conflict detected container=%s", container_name)
            runtime_identity = await self.inspect_runtime(container_name)
            if not await self._matches_expected_runtime(
                runtime_identity=runtime_identity,
                resolved_host_root=resolved_host_root,
                project_id=project_id,
            ):
                logger.warning("sandbox stale container detected; recreating container=%s", container_name)
                remove_returncode, remove_stdout, remove_stderr = await self._invoke(
                    "docker",
                    "rm",
                    "-f",
                    container_name,
                )
                if remove_returncode != 0:
                    remove_message = remove_stderr.strip() or remove_stdout.strip() or "docker rm failed"
                    raise RuntimeError(remove_message)

                recreate_returncode, recreate_stdout, recreate_stderr = await self._run_container(
                    container_name=container_name,
                    resolved_host_root=resolved_host_root,
                    project_id=project_id,
                )
                if recreate_returncode == 0:
                    logger.info("sandbox container recreated container=%s", container_name)
                    return container_name

                recreate_message = recreate_stderr.strip() or recreate_stdout.strip() or "docker run failed"
                raise RuntimeError(recreate_message)

            start_returncode, start_stdout, start_stderr = await self._invoke(
                "docker",
                "start",
                container_name,
            )
            if start_returncode == 0:
                logger.info("sandbox container started container=%s", container_name)
                return container_name

            start_message = start_stderr.strip() or start_stdout.strip() or "docker start failed"
            if "already running" in start_message:
                logger.info("sandbox container already running container=%s", container_name)
                return container_name
            raise RuntimeError(start_message)

        if returncode != 0:
            raise RuntimeError(message)
        return container_name

    async def _run_container(
        self,
        *,
        container_name: str,
        resolved_host_root: Path,
        project_id: int,
    ) -> tuple[int, str, str]:
        command = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-v",
            f"{resolved_host_root}:/workspace",
            "-w",
            "/workspace",
            "-e",
            f"ATOMS_PROJECT_ID={project_id}",
        ]
        command.extend(self._sandbox_env_args())
        command.extend(
            [
                "-p",
                "0:3000",
                "-p",
                "0:8000",
                self.SANDBOX_IMAGE,
                "sleep",
                "infinity",
            ]
        )
        return await self._invoke(*command)

    def _sandbox_env_args(self) -> list[str]:
        args: list[str] = []
        for assignment in self._sandbox_env_assignments():
            args.extend(["-e", assignment])
        return args

    def _sandbox_env_assignments(self) -> list[str]:
        assignments: list[str] = []
        for source_key, target_keys in _SANDBOX_PROXY_ENV_MAP.items():
            value = os.environ.get(source_key)
            if not value:
                continue
            for target_key in target_keys:
                assignments.append(f"{target_key}={value}")
        return assignments

    def _exec_timeout_for_command(self, command: str) -> float:
        install_patterns = (
            "uv pip install",
            "pip install",
            "python -m pip install",
            "python3 -m pip install",
            "pnpm install",
            "npm install",
            "yarn install",
        )
        if any(pattern in command for pattern in install_patterns):
            return self.install_timeout_seconds
        return self.exec_timeout_seconds

    async def _matches_expected_runtime(
        self,
        *,
        runtime_identity: dict[str, object],
        resolved_host_root: Path,
        project_id: int,
    ) -> bool:
        workspace_source = self._normalize_existing_path(runtime_identity.get("workspace_source"))
        image_id = runtime_identity.get("image_id")
        expected_image_id = await self.inspect_image_id(self.SANDBOX_IMAGE)
        has_required_ports = {"3000/tcp", "8000/tcp"}.issubset(runtime_identity.get("port_bindings", set()))
        runtime_env = set(runtime_identity.get("env", []))
        has_matching_project_env = f"ATOMS_PROJECT_ID={project_id}" in runtime_env
        expected_proxy_env = set(self._sandbox_env_assignments())
        has_matching_proxy_env = expected_proxy_env.issubset(runtime_env)
        return (
            workspace_source == str(resolved_host_root)
            and image_id == expected_image_id
            and runtime_identity.get("working_dir") == "/workspace"
            and runtime_identity.get("command") == ["sleep", "infinity"]
            and has_required_ports
            and has_matching_project_env
            and has_matching_proxy_env
        )

    async def exec(
        self,
        container_name: str,
        command: str,
        env: Optional[dict[str, str]] = None,
    ) -> tuple[int, str, str]:
        docker_command = ["docker", "exec", "-i"]
        for key, value in (env or {}).items():
            docker_command.extend(["-e", f"{key}={value}"])
        docker_command.extend([container_name, "/bin/bash", "-lc", command])
        timeout_seconds = self._exec_timeout_for_command(command)
        try:
            return await asyncio.wait_for(
                self.run_command(*docker_command),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"command timed out after {timeout_seconds}s: {self._format_command_for_error(docker_command)}"
            ) from exc

    async def start_preview_services(
        self,
        container_name: str,
        env: Optional[dict[str, str]] = None,
    ) -> tuple[int, str, str]:
        return await self.exec(container_name, "/usr/local/bin/start-preview", env=env)

    async def wait_for_service(
        self,
        container_name: str,
        port: int,
        path: str = "/",
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 1.0,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        normalized_path = path if path.startswith("/") else f"/{path}"
        logger.info("wait_for_service container=%s port=%s path=%s timeout=%.0fs", container_name, port, normalized_path, timeout_seconds)

        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                logger.warning("wait_for_service TIMEOUT container=%s port=%s path=%s", container_name, port, normalized_path)
                return False

            probe_timeout = min(2.0, remaining_seconds, self.command_timeout_seconds)
            probe_command = (
                f"curl -sf -o /dev/null --max-time {probe_timeout:g} "
                f"http://localhost:{port}{normalized_path}"
            )

            try:
                returncode, _, _ = await asyncio.wait_for(
                    self.run_command(
                        "docker",
                        "exec",
                        "-i",
                        container_name,
                        "/bin/bash",
                        "-lc",
                        probe_command,
                    ),
                    timeout=probe_timeout,
                )
            except TimeoutError:
                return False

            if returncode == 0:
                logger.info("wait_for_service OK container=%s port=%s path=%s", container_name, port, normalized_path)
                return True
            if time.monotonic() >= deadline:
                logger.warning("wait_for_service TIMEOUT container=%s port=%s path=%s", container_name, port, normalized_path)
                return False
            await asyncio.sleep(poll_interval_seconds)

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

    async def inspect_runtime(self, container_name: str) -> dict[str, object]:
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
        env = container.get("Config", {}).get("Env") or []

        return {
            "image": container.get("Config", {}).get("Image"),
            "image_id": container.get("Image"),
            "workspace_source": workspace_source,
            "working_dir": container.get("Config", {}).get("WorkingDir"),
            "command": container.get("Config", {}).get("Cmd") or [],
            "port_bindings": port_bindings,
            "env": env,
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
            raise RuntimeError(
                f"command timed out after {self.command_timeout_seconds}s: {self._format_command_for_error(command)}"
            ) from exc

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

    @staticmethod
    def _format_command_for_error(command: tuple[str, ...] | list[str]) -> str:
        return " ".join(SandboxRuntimeService._redact_command_part(part) for part in command)

    @staticmethod
    def _redact_command_part(part: str) -> str:
        key, separator, _value = part.partition("=")
        if separator and key in _SANDBOX_PROXY_ENV_KEYS:
            return f"{key}=<redacted>"
        return part
