from pathlib import Path

from services.project_workspace import ProjectWorkspaceService


def test_resolve_paths_nests_by_user_and_project(tmp_path):
    service = ProjectWorkspaceService(base_root=tmp_path)

    paths = service.resolve_paths(user_id="user-123", project_id=42)

    assert paths.host_root == tmp_path / "user-123" / "42"
    assert paths.container_root == Path("/workspace")


def test_snapshot_files_ignores_runtime_directories(tmp_path):
    service = ProjectWorkspaceService(base_root=tmp_path)
    host_root = tmp_path / "user-123" / "42"
    (host_root / "src").mkdir(parents=True)
    (host_root / ".venv").mkdir()
    (host_root / ".pnpm-store" / "v3" / "files").mkdir(parents=True)
    (host_root / "node_modules" / "pkg").mkdir(parents=True)
    (host_root / "dist").mkdir()
    (host_root / "build").mkdir()
    (host_root / "__pycache__").mkdir()
    (host_root / ".git").mkdir()
    (host_root / ".atoms" / "cache" / "deps").mkdir(parents=True)
    (host_root / ".atoms").mkdir(exist_ok=True)

    (host_root / "src" / "App.tsx").write_text("export default function App() {}", encoding="utf-8")
    (host_root / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin/python", encoding="utf-8")
    (host_root / ".pnpm-store" / "v3" / "files" / "hash").write_text("store", encoding="utf-8")
    (host_root / "node_modules" / "pkg" / "index.js").write_text("module.exports = {}", encoding="utf-8")
    (host_root / "dist" / "bundle.js").write_text("console.log('dist')", encoding="utf-8")
    (host_root / "build" / "bundle.js").write_text("console.log('build')", encoding="utf-8")
    (host_root / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"pyc")
    (host_root / ".git" / "config").write_text("[core]", encoding="utf-8")
    (host_root / ".atoms" / "cache" / "deps" / "frontend.json").write_text('{"hash":"abc"}', encoding="utf-8")
    (host_root / ".atoms" / "smoke.json").write_text('{"version":1,"checks":[]}', encoding="utf-8")

    snapshot = service.snapshot_files(host_root)

    assert snapshot == {
        ".atoms/smoke.json": {
            "content": '{"version":1,"checks":[]}',
            "is_directory": False,
        },
        "src/App.tsx": {
            "content": "export default function App() {}",
            "is_directory": False,
        }
    }


def test_materialize_files_writes_nested_paths(tmp_path):
    service = ProjectWorkspaceService(base_root=tmp_path)
    paths = service.resolve_paths(user_id="user-123", project_id=42)

    service.materialize_files(
        paths.host_root,
        [
            {"file_path": "docs/todo.md", "content": "# Todo"},
            {"file_path": "app/frontend/src/App.tsx", "content": "export const App = () => null;"},
            {"file_path": "app/backend/utils/App.tsx", "content": "export const App = () => null;"},
            {"file_path": "backend/main.py", "content": "print('ok')"},
        ],
    )

    assert (paths.host_root / "docs" / "todo.md").read_text(encoding="utf-8") == "# Todo"
    assert (paths.host_root / "app" / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8") == "export const App = () => null;"
    assert (paths.host_root / "app" / "backend" / "utils" / "App.tsx").read_text(encoding="utf-8") == "export const App = () => null;"
    assert (paths.host_root / "backend" / "main.py").read_text(encoding="utf-8") == "print('ok')"


def test_resolve_paths_rejects_unsafe_user_id(tmp_path):
    service = ProjectWorkspaceService(base_root=tmp_path)

    try:
        service.resolve_paths(user_id="../escape", project_id=42)
    except ValueError as exc:
        assert "escape" in str(exc)
    else:
        raise AssertionError("expected ValueError for unsafe user_id")


def test_materialize_files_rejects_relative_traversal(tmp_path):
    service = ProjectWorkspaceService(base_root=tmp_path)
    paths = service.resolve_paths(user_id="user-123", project_id=42)

    try:
        service.materialize_files(
            paths.host_root,
            [{"file_path": "../escape.txt", "content": "nope"}],
        )
    except ValueError as exc:
        assert "escape" in str(exc)
    else:
        raise AssertionError("expected ValueError for relative traversal")


def test_materialize_files_rejects_absolute_paths(tmp_path):
    service = ProjectWorkspaceService(base_root=tmp_path)
    paths = service.resolve_paths(user_id="user-123", project_id=42)

    try:
        service.materialize_files(
            paths.host_root,
            [{"file_path": "/absolute/escape.txt", "content": "nope"}],
        )
    except ValueError as exc:
        assert "escape" in str(exc)
    else:
        raise AssertionError("expected ValueError for absolute file path")


def test_external_host_root_is_rejected(tmp_path):
    service = ProjectWorkspaceService(base_root=tmp_path)
    outside_root = tmp_path.parent / "outside"

    try:
        service.materialize_files(
            outside_root,
            [{"file_path": "src/App.tsx", "content": "nope"}],
        )
    except ValueError as exc:
        assert "host_root" in str(exc)
    else:
        raise AssertionError("expected ValueError for external host_root")

    try:
        service.snapshot_files(outside_root)
    except ValueError as exc:
        assert "host_root" in str(exc)
    else:
        raise AssertionError("expected ValueError for external host_root snapshot")
