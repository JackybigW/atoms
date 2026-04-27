import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


RunLogKind = Literal["system", "progress", "terminal", "error"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class AgentRunRecorder:
    store: "AgentRunLogStore"
    user_id: str
    project_id: int
    run_id: str
    sequence: int = 0
    metric_sequence: int = 0

    def _append(self, *, kind: RunLogKind, content: str) -> None:
        self.sequence += 1
        self.store.append_entry(
            user_id=self.user_id,
            project_id=self.project_id,
            run_id=self.run_id,
            sequence=self.sequence,
            kind=kind,
            content=content,
        )

    def system(self, content: str) -> None:
        self._append(kind="system", content=f"$ [system] {content}")

    def progress(self, content: str) -> None:
        self._append(kind="progress", content=f"> {content}")

    def terminal(self, content: str) -> None:
        self._append(kind="terminal", content=content)

    def error(self, content: str) -> None:
        self._append(kind="error", content=f"! {content}")

    def metric_event(self, name: str, *, category: str, attrs: dict[str, object] | None = None) -> None:
        self.metric_sequence += 1
        self.store.append_metric(
            user_id=self.user_id,
            project_id=self.project_id,
            run_id=self.run_id,
            sequence=self.metric_sequence,
            payload={
                "type": "event",
                "name": name,
                "category": category,
                "attrs": attrs or {},
            },
        )

    def metric_summary(self, summary: dict[str, object]) -> None:
        self.store.write_manifest(
            user_id=self.user_id,
            project_id=self.project_id,
            payload={"metrics_summary": summary},
            merge=True,
        )

    def set_status(self, status: str) -> None:
        now = _utc_now_iso()
        self.store.write_manifest(
            user_id=self.user_id,
            project_id=self.project_id,
            payload={
                "run_id": self.run_id,
                "status": status,
                "updated_at": now,
                "ended_at": now,
            },
            merge=True,
        )


class AgentRunLogStore:
    def __init__(self, base_root: Path):
        self.base_root = Path(base_root)

    def _run_dir(self, *, user_id: str, project_id: int) -> Path:
        return self.base_root / ".agent_runs" / user_id / str(project_id)

    def _manifest_path(self, *, user_id: str, project_id: int) -> Path:
        return self._run_dir(user_id=user_id, project_id=project_id) / "latest.json"

    def _entries_path(self, *, user_id: str, project_id: int) -> Path:
        return self._run_dir(user_id=user_id, project_id=project_id) / "latest.jsonl"

    def _metrics_path(self, *, user_id: str, project_id: int) -> Path:
        return self._run_dir(user_id=user_id, project_id=project_id) / "latest_metrics.jsonl"

    def start_run(self, *, user_id: str, project_id: int) -> AgentRunRecorder:
        run_dir = self._run_dir(user_id=user_id, project_id=project_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        self.write_manifest(
            user_id=user_id,
            project_id=project_id,
            payload={
                "run_id": run_id,
                "status": "running",
                "started_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
            },
            merge=False,
        )
        self._entries_path(user_id=user_id, project_id=project_id).write_text("", encoding="utf-8")
        self._metrics_path(user_id=user_id, project_id=project_id).write_text("", encoding="utf-8")
        return AgentRunRecorder(
            store=self,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
        )

    def write_manifest(self, *, user_id: str, project_id: int, payload: dict, merge: bool) -> None:
        manifest_path = self._manifest_path(user_id=user_id, project_id=project_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, object] = {}
        if merge and manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
        manifest.update(payload)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_entry(
        self,
        *,
        user_id: str,
        project_id: int,
        run_id: str,
        sequence: int,
        kind: RunLogKind,
        content: str,
    ) -> None:
        entry = {
            "run_id": run_id,
            "seq": sequence,
            "kind": kind,
            "content": content,
            "created_at": _utc_now_iso(),
        }
        entries_path = self._entries_path(user_id=user_id, project_id=project_id)
        entries_path.parent.mkdir(parents=True, exist_ok=True)
        with entries_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def append_metric(
        self,
        *,
        user_id: str,
        project_id: int,
        run_id: str,
        sequence: int,
        payload: dict[str, object],
    ) -> None:
        metric = {
            "run_id": run_id,
            "seq": sequence,
            **payload,
        }
        metrics_path = self._metrics_path(user_id=user_id, project_id=project_id)
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with metrics_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(metric, ensure_ascii=False) + "\n")

    def read_latest_run(self, *, user_id: str, project_id: int) -> dict[str, object] | None:
        manifest_path = self._manifest_path(user_id=user_id, project_id=project_id)
        entries_path = self._entries_path(user_id=user_id, project_id=project_id)
        metrics_path = self._metrics_path(user_id=user_id, project_id=project_id)
        if not manifest_path.exists():
            return None

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

        entries: list[dict[str, object]] = []
        if entries_path.exists():
            for raw_line in entries_path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                try:
                    entries.append(json.loads(raw_line))
                except json.JSONDecodeError:
                    continue

        metrics: list[dict[str, object]] = []
        if metrics_path.exists():
            for raw_line in metrics_path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                try:
                    metrics.append(json.loads(raw_line))
                except json.JSONDecodeError:
                    continue

        return {
            "run_id": manifest.get("run_id"),
            "status": manifest.get("status", "unknown"),
            "started_at": manifest.get("started_at"),
            "updated_at": manifest.get("updated_at"),
            "entries": entries,
            "metrics": metrics,
            "metrics_summary": manifest.get("metrics_summary"),
        }
