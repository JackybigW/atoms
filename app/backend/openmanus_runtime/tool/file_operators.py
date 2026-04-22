"""File operation interfaces and implementations for local and sandbox environments."""

import asyncio
from inspect import isawaitable
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional, Protocol, Tuple, Union, runtime_checkable

from openmanus_runtime.config import SandboxSettings
from openmanus_runtime.exceptions import ToolError
from openmanus_runtime.sandbox.client import SANDBOX_CLIENT


PathLike = Union[str, Path]
WORKSPACE_ROOT = PurePosixPath("/workspace")
ALLOWED_WORKSPACE_WRITE_ROOTS = (
    PurePosixPath("/workspace/app/frontend"),
    PurePosixPath("/workspace/app/backend"),
    PurePosixPath("/workspace/docs"),
    PurePosixPath("/workspace/.atoms"),
)
PROTECTED_PATHS = (
    PurePosixPath("/workspace/app/backend/core"),
    PurePosixPath("/workspace/app/backend/models"),
    PurePosixPath("/workspace/app/backend/main.py"),
    PurePosixPath("/workspace/app/backend/lambda_handler.py"),
)


def _as_posix_path(path: PathLike) -> PurePosixPath:
    return PurePosixPath(str(path))


def _normalize_posix_path(path: PathLike) -> PurePosixPath:
    candidate = _as_posix_path(path)
    if not candidate.is_absolute():
        return candidate

    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {"/", "."}:
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)

    return PurePosixPath("/", *normalized_parts)


def normalize_workspace_path(path: PathLike) -> PurePosixPath:
    return _normalize_posix_path(path)


def _is_under(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def _is_protected_workspace_path(path: PathLike) -> bool:
    candidate = _normalize_posix_path(path)
    return any(_is_under(candidate, protected) for protected in PROTECTED_PATHS)


def _is_allowed_workspace_write_path(path: PathLike) -> bool:
    candidate = _normalize_posix_path(path)
    return any(
        _is_under(candidate, allowed_root) for allowed_root in ALLOWED_WORKSPACE_WRITE_ROOTS
    )


def validate_workspace_path(path: PathLike) -> None:
    candidate = _normalize_posix_path(path)
    if not candidate.is_absolute():
        raise ToolError(f"The path {path} is not an absolute path")
    if not _is_under(candidate, WORKSPACE_ROOT):
        raise ToolError(f"Path must live under {WORKSPACE_ROOT}, got: {candidate}")
    if _is_protected_workspace_path(candidate):
        raise ToolError(f"Path {candidate} is protected and cannot be modified")
    if not _is_allowed_workspace_write_path(candidate):
        allowed = ", ".join(str(path) for path in ALLOWED_WORKSPACE_WRITE_ROOTS)
        raise ToolError(
            f"Writes are only allowed under: {allowed}. Got: {candidate}"
        )


def validate_workspace_write_path(path: PathLike) -> None:
    """Validate that a workspace path is eligible for mutation."""
    validate_workspace_path(path)


@runtime_checkable
class FileOperator(Protocol):
    """Interface for file operations in different environments."""

    async def read_file(self, path: PathLike) -> str:
        """Read content from a file."""
        ...

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a file."""
        ...

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory."""
        ...

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists."""
        ...

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a shell command and return (return_code, stdout, stderr)."""
        ...


class LocalFileOperator(FileOperator):
    """File operations implementation for local filesystem."""

    encoding: str = "utf-8"

    async def read_file(self, path: PathLike) -> str:
        """Read content from a local file."""
        try:
            return Path(path).read_text(encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"Failed to read {path}: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a local file."""
        try:
            Path(path).write_text(content, encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"Failed to write to {path}: {str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory."""
        return Path(path).is_dir()

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists."""
        return Path(path).exists()

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a shell command locally."""
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            return (
                process.returncode or 0,
                stdout.decode(),
                stderr.decode(),
            )
        except asyncio.TimeoutError as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise TimeoutError(
                f"Command '{cmd}' timed out after {timeout} seconds"
            ) from exc


class ProjectFileOperator(LocalFileOperator):
    """File operator that maps container-relative paths (e.g. /workspace/…) to a host directory."""

    def __init__(
        self,
        host_root: Path,
        container_root: Path,
        event_sink: Callable[[dict[str, object]], Any] | None = None,
        approval_gate: Any | None = None,
    ):
        self.host_root = Path(host_root)
        self.container_root = Path(container_root)
        self._event_sink = event_sink
        self._approval_gate = approval_gate

    def _to_host_path(self, path: PathLike) -> Path:
        raw = Path(path)
        if not raw.is_absolute() or not str(raw).startswith(str(self.container_root)):
            raise ToolError(f"Path must live under {self.container_root}, got: {raw}")
        try:
            relative = raw.relative_to(self.container_root)
        except ValueError:
            raise ToolError(f"Path must live under {self.container_root}, got: {raw}")
        host_path = (self.host_root / relative).resolve()
        resolved_root = self.host_root.resolve()
        if host_path != resolved_root and resolved_root not in host_path.parents:
            raise ToolError("Path escapes project workspace")
        return host_path

    def _to_host_write_path(self, path: PathLike) -> Path:
        normalized = normalize_workspace_path(path)
        if self._approval_gate is not None:
            _DOCS_ROOT = PurePosixPath("/workspace/docs")
            _PLANS_ROOT = PurePosixPath("/workspace/docs/plans")
            self._approval_gate.check_write(normalized)
            if normalized.is_relative_to(_PLANS_ROOT) and normalized.suffix == ".md":
                self._approval_gate.record_plan_written(str(normalized))
        validate_workspace_write_path(normalized)
        return self._to_host_path(str(normalized))

    async def read_file(self, path: PathLike) -> str:
        return await super().read_file(self._to_host_path(path))

    async def write_file(self, path: PathLike, content: str) -> None:
        host_path = self._to_host_write_path(path)
        host_path.parent.mkdir(parents=True, exist_ok=True)
        await super().write_file(host_path, content)
        if self._event_sink is not None:
            result = self._event_sink(
                {
                    "type": "file.snapshot",
                    "path": host_path.relative_to(self.host_root).as_posix(),
                    "content": content,
                }
            )
            if isawaitable(result):
                await result

    async def is_directory(self, path: PathLike) -> bool:
        return await super().is_directory(self._to_host_path(path))

    async def exists(self, path: PathLike) -> bool:
        return await super().exists(self._to_host_path(path))

    async def run_command(self, cmd: str, timeout: Optional[float] = 120.0) -> Tuple[int, str, str]:
        return await super().run_command(self._map_workspace_paths_in_command(cmd), timeout)

    def _map_workspace_paths_in_command(self, cmd: str) -> str:
        parts = shlex.split(cmd)
        mapped_parts: list[str] = []
        for part in parts:
            if part.startswith(str(self.container_root)):
                mapped_parts.append(str(self._to_host_path(part)))
            else:
                mapped_parts.append(part)
        return shlex.join(mapped_parts)


class SandboxFileOperator(FileOperator):
    """File operations implementation for sandbox environment."""

    def __init__(self):
        self.sandbox_client = SANDBOX_CLIENT

    async def _ensure_sandbox_initialized(self):
        """Ensure sandbox is initialized."""
        if not self.sandbox_client.sandbox:
            await self.sandbox_client.create(config=SandboxSettings())

    async def read_file(self, path: PathLike) -> str:
        """Read content from a file in sandbox."""
        await self._ensure_sandbox_initialized()
        try:
            return await self.sandbox_client.read_file(str(path))
        except Exception as e:
            raise ToolError(f"Failed to read {path} in sandbox: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a file in sandbox."""
        await self._ensure_sandbox_initialized()
        try:
            await self.sandbox_client.write_file(str(path), content)
        except Exception as e:
            raise ToolError(f"Failed to write to {path} in sandbox: {str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory in sandbox."""
        await self._ensure_sandbox_initialized()
        result = await self.sandbox_client.run_command(
            f"test -d {path} && echo 'true' || echo 'false'"
        )
        return result.strip() == "true"

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists in sandbox."""
        await self._ensure_sandbox_initialized()
        result = await self.sandbox_client.run_command(
            f"test -e {path} && echo 'true' || echo 'false'"
        )
        return result.strip() == "true"

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a command in sandbox environment."""
        await self._ensure_sandbox_initialized()
        try:
            stdout = await self.sandbox_client.run_command(
                cmd, timeout=int(timeout) if timeout else None
            )
            return (
                0,  # Always return 0 since we don't have explicit return code from sandbox
                stdout,
                "",  # No stderr capture in the current sandbox implementation
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Command '{cmd}' timed out after {timeout} seconds in sandbox"
            ) from exc
        except Exception as exc:
            return 1, "", f"Error executing command in sandbox: {str(exc)}"
