import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal


TelemetryCategory = Literal[
    "runtime",
    "agent",
    "bash",
    "dependency",
    "verification",
    "preview",
    "smoke",
    "workspace",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, set):
        return [_safe_json_value(item) for item in sorted(value, key=str)]
    return str(value)


@dataclass(slots=True)
class RuntimeTelemetryRecorder:
    run_id: str
    sink_path: Path
    sequence: int = 0
    _records: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.sink_path = Path(self.sink_path)
        self.sink_path.parent.mkdir(parents=True, exist_ok=True)
        self.sink_path.write_text("", encoding="utf-8")

    @contextmanager
    def span(
        self,
        name: str,
        *,
        category: TelemetryCategory,
        attrs: dict[str, Any] | None = None,
    ) -> Iterator[None]:
        started_at = _utc_now_iso()
        started = time.perf_counter()
        status = "ok"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            duration_ms = max(0.0, (time.perf_counter() - started) * 1000)
            self._append(
                {
                    "type": "span",
                    "name": name,
                    "category": category,
                    "status": status,
                    "started_at": started_at,
                    "ended_at": _utc_now_iso(),
                    "duration_ms": duration_ms,
                    "attrs": _safe_json_value(attrs or {}),
                }
            )

    def event(
        self,
        name: str,
        *,
        category: TelemetryCategory,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self._append(
            {
                "type": "event",
                "name": name,
                "category": category,
                "created_at": _utc_now_iso(),
                "attrs": _safe_json_value(attrs or {}),
            }
        )

    def summary(self) -> dict[str, Any]:
        durations_ms: dict[str, float] = {}
        events: dict[str, int] = {}
        span_count = 0
        event_count = 0

        for record in self._records:
            if record["type"] == "span":
                span_count += 1
                category = str(record["category"])
                durations_ms[category] = durations_ms.get(category, 0.0) + float(record["duration_ms"])
            elif record["type"] == "event":
                event_count += 1
                name = str(record["name"])
                events[name] = events.get(name, 0) + 1

        return {
            "run_id": self.run_id,
            "span_count": span_count,
            "event_count": event_count,
            "durations_ms": durations_ms,
            "events": events,
        }

    def _append(self, payload: dict[str, Any]) -> None:
        self.sequence += 1
        record = {
            "run_id": self.run_id,
            "seq": self.sequence,
            **payload,
        }
        self._records.append(record)
        with self.sink_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_safe_json_value(record), ensure_ascii=False) + "\n")
