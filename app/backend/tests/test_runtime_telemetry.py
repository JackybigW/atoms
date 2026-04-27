import json
from datetime import datetime
from pathlib import Path

import pytest

from services.runtime_telemetry import RuntimeTelemetryRecorder


def test_runtime_telemetry_writes_span_and_event(tmp_path):
    metrics_path = tmp_path / "latest_metrics.jsonl"
    recorder = RuntimeTelemetryRecorder(run_id="run-1", sink_path=metrics_path)

    with recorder.span("sandbox.ensure_runtime", category="runtime", attrs={"project_id": 42}):
        pass
    recorder.event("dependency_cache.hit", category="dependency", attrs={"scope": "backend"})

    rows = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["seq"] == 1
    assert rows[0]["type"] == "span"
    assert rows[0]["name"] == "sandbox.ensure_runtime"
    assert rows[0]["category"] == "runtime"
    assert rows[0]["status"] == "ok"
    assert datetime.fromisoformat(rows[0]["started_at"])
    assert datetime.fromisoformat(rows[0]["ended_at"])
    assert rows[0]["duration_ms"] >= 0
    assert rows[0]["attrs"] == {"project_id": 42}

    assert rows[1]["run_id"] == "run-1"
    assert rows[1]["seq"] == 2
    assert rows[1]["type"] == "event"
    assert rows[1]["name"] == "dependency_cache.hit"
    assert rows[1]["category"] == "dependency"
    assert datetime.fromisoformat(rows[1]["created_at"])
    assert rows[1]["attrs"] == {"scope": "backend"}


def test_runtime_telemetry_summary_groups_duration_by_category(tmp_path):
    metrics_path = tmp_path / "latest_metrics.jsonl"
    recorder = RuntimeTelemetryRecorder(run_id="run-1", sink_path=metrics_path)

    with recorder.span("agent.round", category="agent"):
        pass
    with recorder.span("preview.wait.frontend", category="preview"):
        pass
    recorder.event("dependency_cache.hit", category="dependency", attrs={"scope": "frontend"})

    summary = recorder.summary()

    assert summary["run_id"] == "run-1"
    assert summary["span_count"] == 2
    assert summary["event_count"] == 1
    assert summary["durations_ms"]["agent"] >= 0
    assert summary["durations_ms"]["preview"] >= 0
    assert summary["events"]["dependency_cache.hit"] == 1


def test_runtime_telemetry_span_records_error_and_reraises(tmp_path):
    metrics_path = tmp_path / "latest_metrics.jsonl"
    recorder = RuntimeTelemetryRecorder(run_id="run-1", sink_path=metrics_path)

    with pytest.raises(RuntimeError, match="boom"):
        with recorder.span("agent.round", category="agent"):
            raise RuntimeError("boom")

    rows = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["type"] == "span"
    assert rows[0]["status"] == "error"
    assert rows[0]["duration_ms"] >= 0


def test_runtime_telemetry_safely_serializes_attrs(tmp_path):
    class CustomValue:
        def __str__(self):
            return "custom-value"

    metrics_path = tmp_path / "latest_metrics.jsonl"
    recorder = RuntimeTelemetryRecorder(run_id="run-1", sink_path=metrics_path)
    source_path = tmp_path / "workspace"

    recorder.event(
        "dependency_cache.hit",
        category="dependency",
        attrs={
            "path": source_path,
            "nested": {"path": source_path / "nested"},
            "list": [source_path / "list", 1],
            "tuple": (source_path / "tuple", "value"),
            "set": {"beta", "alpha"},
            "fallback": CustomValue(),
        },
    )

    row = json.loads(metrics_path.read_text(encoding="utf-8"))

    assert row["attrs"] == {
        "path": str(source_path),
        "nested": {"path": str(source_path / "nested")},
        "list": [str(source_path / "list"), 1],
        "tuple": [str(source_path / "tuple"), "value"],
        "set": ["alpha", "beta"],
        "fallback": "custom-value",
    }


def test_runtime_telemetry_init_creates_parent_dirs_and_truncates_sink(tmp_path):
    existing_path = tmp_path / "existing" / "latest_metrics.jsonl"
    existing_path.parent.mkdir()
    existing_path.write_text("stale\n", encoding="utf-8")

    RuntimeTelemetryRecorder(run_id="run-1", sink_path=existing_path)

    assert existing_path.read_text(encoding="utf-8") == ""

    nested_path = tmp_path / "new" / "nested" / "latest_metrics.jsonl"

    RuntimeTelemetryRecorder(run_id="run-2", sink_path=nested_path)

    assert nested_path.exists()
    assert nested_path.read_text(encoding="utf-8") == ""
