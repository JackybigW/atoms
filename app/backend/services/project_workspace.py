from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


IGNORED_PARTS = {
    ".venv",
    ".pnpm-store",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".git",
}


def _should_ignore_snapshot_path(relative_path: Path) -> bool:
    parts = relative_path.parts
    if any(part in IGNORED_PARTS for part in parts):
        return True
    return len(parts) >= 2 and parts[0] == ".atoms" and parts[1] == "cache"


@dataclass(frozen=True)
class WorkspacePaths:
    host_root: Path
    container_root: Path


class ProjectWorkspaceService:
    def __init__(self, base_root: Path):
        self.base_root = Path(base_root)

    def _ensure_within_root(self, root: Path, candidate: Path, error_label: str) -> Path:
        root = Path(root).resolve(strict=False)
        resolved_candidate = Path(candidate).resolve(strict=False)

        try:
            resolved_candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{error_label} escapes workspace root") from exc

        return resolved_candidate

    def resolve_paths(self, user_id: str, project_id: int) -> WorkspacePaths:
        host_root = self.base_root / user_id / str(project_id)
        host_root = self._ensure_within_root(self.base_root, host_root, "host_root")
        host_root.mkdir(parents=True, exist_ok=True)
        return WorkspacePaths(host_root=host_root, container_root=Path("/workspace"))

    def materialize_files(self, host_root: Path, project_files: Iterable[dict]) -> None:
        host_root = self._ensure_within_root(self.base_root, host_root, "host_root")
        host_root.mkdir(parents=True, exist_ok=True)

        for file_record in project_files:
            relative_path = Path(file_record["file_path"])
            target_path = host_root / relative_path
            target_path = self._ensure_within_root(host_root, target_path, "file_path")
            if file_record.get("is_directory"):
                target_path.mkdir(parents=True, exist_ok=True)
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(file_record.get("content") or "", encoding="utf-8")

    def snapshot_files(self, host_root: Path) -> dict[str, dict]:
        host_root = self._ensure_within_root(self.base_root, host_root, "host_root")
        snapshot: dict[str, dict] = {}

        for path in host_root.rglob("*"):
            if _should_ignore_snapshot_path(path.relative_to(host_root)):
                continue
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, ValueError):
                    continue
                snapshot[str(path.relative_to(host_root))] = {
                    "content": content,
                    "is_directory": False,
                }

        return snapshot
