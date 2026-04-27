import json
from pathlib import Path

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
    assert rows[0]["duration_ms"] >= 0
    assert rows[0]["attrs"] == {"project_id": 42}

    assert rows[1]["seq"] == 2
    assert rows[1]["type"] == "event"
    assert rows[1]["name"] == "dependency_cache.hit"
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
