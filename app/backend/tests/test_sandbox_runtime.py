import asyncio
import subprocess
import sys

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from models.workspace_runtime_sessions import WorkspaceRuntimeSessions
from services.sandbox_runtime import SandboxRuntimeService
from services.workspace_runtime_sessions import WorkspaceRuntimeSessionsService


@pytest.mark.asyncio
async def test_workspace_runtime_sessions_service_get_by_project():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            db.add_all(
                [
                    WorkspaceRuntimeSessions(
                        user_id="user-123",
                        project_id=42,
                        container_name="atoms-user-123-42",
                        status="running",
                        preview_port=3000,
                        frontend_port=5173,
                        backend_port=8000,
                    ),
                    WorkspaceRuntimeSessions(
                        user_id="user-999",
                        project_id=42,
                        container_name="atoms-user-999-42",
                        status="running",
                    ),
                ]
            )
            await db.commit()

            service = WorkspaceRuntimeSessionsService(db)
            runtime = await service.get_by_project(user_id="user-123", project_id=42)

        assert runtime is not None
        assert runtime.container_name == "atoms-user-123-42"
        assert runtime.status == "running"
        assert runtime.preview_port == 3000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_runtime_sessions_user_project_is_unique():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            db.add(
                WorkspaceRuntimeSessions(
                    user_id="user-123",
                    project_id=42,
                    container_name="atoms-user-123-42",
                    status="running",
                )
            )
            await db.commit()

            db.add(
                WorkspaceRuntimeSessions(
                    user_id="user-123",
                    project_id=42,
                    container_name="atoms-user-123-42-replacement",
                    status="stopped",
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_runtime_sessions_service_create_upserts_same_project():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            service = WorkspaceRuntimeSessionsService(db)
            created = await service.create(
                {
                    "user_id": "user-123",
                    "project_id": 42,
                    "container_name": "atoms-user-123-42",
                    "status": "running",
                    "preview_port": 3000,
                }
            )
            updated = await service.create(
                {
                    "user_id": "user-123",
                    "project_id": 42,
                    "container_name": "atoms-user-123-42-restarted",
                    "status": "stopped",
                    "preview_port": 3001,
                }
            )

        assert updated.id == created.id
        assert updated.container_name == "atoms-user-123-42-restarted"
        assert updated.status == "stopped"
        assert updated.preview_port == 3001
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_runtime_sessions_service_rolls_back_failed_upsert_commit():
    class FakeSession:
        def __init__(self):
            self.commit_calls = 0
            self.rollback_calls = 0
            self.refresh_calls = 0
            self.added = []

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise IntegrityError("insert failed", params=None, orig=Exception("duplicate"))
            raise RuntimeError("update commit failed")

        async def rollback(self):
            self.rollback_calls += 1

        async def refresh(self, obj):
            self.refresh_calls += 1

        async def execute(self, query):
            class Result:
                def scalar_one_or_none(self_inner):
                    existing = WorkspaceRuntimeSessions(
                        user_id="user-123",
                        project_id=42,
                        container_name="atoms-user-123-42",
                        status="running",
                    )
                    existing.id = 7
                    return existing

            return Result()

    service = WorkspaceRuntimeSessionsService(FakeSession())

    with pytest.raises(RuntimeError, match="update commit failed"):
        await service.create(
            {
                "user_id": "user-123",
                "project_id": 42,
                "container_name": "atoms-user-123-42-restarted",
                "status": "stopped",
            }
        )

    assert service.db.rollback_calls == 2


@pytest.mark.asyncio
async def test_workspace_runtime_sessions_service_rejects_partial_upsert_payload():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            service = WorkspaceRuntimeSessionsService(db)
            await service.create(
                {
                    "user_id": "user-123",
                    "project_id": 42,
                    "container_name": "atoms-user-123-42",
                    "status": "running",
                }
            )

            with pytest.raises(ValueError, match="workspace runtime session requires fields: status"):
                await service.create(
                    {
                        "user_id": "user-123",
                        "project_id": 42,
                        "container_name": "atoms-user-123-42-restarted",
                    }
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_runtime_sessions_service_rejects_null_required_values():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            service = WorkspaceRuntimeSessionsService(db)

            with pytest.raises(ValueError, match="requires non-empty values for: status"):
                await service.create(
                    {
                        "user_id": "user-123",
                        "project_id": 42,
                        "container_name": "atoms-user-123-42",
                        "status": None,
                    }
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_runtime_sessions_service_rejects_unknown_fields():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            service = WorkspaceRuntimeSessionsService(db)

            with pytest.raises(ValueError, match="does not allow fields: id"):
                await service.create(
                    {
                        "id": 999,
                        "user_id": "user-123",
                        "project_id": 42,
                        "container_name": "atoms-user-123-42",
                        "status": "running",
                    }
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_runtime_builds_docker_run_command(tmp_path):
    commands = []
    workspace_root = tmp_path / "user-123" / "42"
    workspace_root.mkdir(parents=True)

    async def fake_run(*args):
        commands.append(args)
        return 0, "container-id-123\n", ""

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    container_name = await service.ensure_runtime(
        user_id="user-123",
        project_id=42,
        host_root=workspace_root,
    )

    assert container_name == "atoms-user-123-42"

    joined = " ".join(commands[0])
    assert "docker run -d" in joined
    assert "-v" in joined
    assert str(tmp_path / "user-123" / "42") in joined
    assert "/workspace" in joined
    assert "-p 0:3000" in joined
    assert "-p 0:8000" in joined
    assert "atoms-sandbox:latest" in joined


@pytest.mark.asyncio
async def test_ensure_runtime_raises_when_docker_run_fails(tmp_path):
    workspace_root = tmp_path / "user-123" / "42"
    workspace_root.mkdir(parents=True)

    async def fake_run(*args):
        return 125, "", "docker run failed"

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    with pytest.raises(RuntimeError, match="docker run failed"):
        await service.ensure_runtime(
            user_id="user-123",
            project_id=42,
            host_root=workspace_root,
        )


@pytest.mark.asyncio
async def test_ensure_runtime_reuses_existing_container_name(tmp_path):
    commands = []
    workspace_root = tmp_path / "user-123" / "42"
    workspace_root.mkdir(parents=True)

    async def fake_run(*args):
        commands.append(args)
        if args[:4] == ("docker", "run", "-d", "--name"):
            return 125, "", 'Conflict. The container name "/atoms-user-123-42" is already in use.'
        if args == ("docker", "inspect", "atoms-user-123-42"):
            return (
                0,
                '[{"Image":"sha256:expected","Config":{"Image":"atoms-sandbox:latest","WorkingDir":"/workspace","Cmd":["sleep","infinity"]},"HostConfig":{"PortBindings":{"3000/tcp":[{"HostPort":"49153"}],"8000/tcp":[{"HostPort":"49154"}]}},"Mounts":[{"Destination":"/workspace","Source":"'
                + str(workspace_root)
                + '"}]}]',
                "",
            )
        if args == ("docker", "image", "inspect", "atoms-sandbox:latest"):
            return 0, '[{"Id":"sha256:expected"}]', ""
        if args == ("docker", "start", "atoms-user-123-42"):
            return 0, "atoms-user-123-42\n", ""
        raise AssertionError(f"unexpected command: {args}")

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    container_name = await service.ensure_runtime(
        user_id="user-123",
        project_id=42,
        host_root=workspace_root,
    )

    assert container_name == "atoms-user-123-42"
    assert commands[-1] == ("docker", "start", "atoms-user-123-42")


@pytest.mark.asyncio
async def test_ensure_runtime_reuses_existing_container_with_var_path_alias(tmp_path):
    commands = []
    workspace_root = tmp_path / "user-123" / "42"
    workspace_root.mkdir(parents=True)
    docker_reported_root = str(workspace_root)
    if docker_reported_root.startswith("/private/var/"):
        docker_reported_root = docker_reported_root.removeprefix("/private")

    async def fake_run(*args):
        commands.append(args)
        if args[:4] == ("docker", "run", "-d", "--name"):
            return 125, "", 'Conflict. The container name "/atoms-user-123-42" is already in use.'
        if args == ("docker", "inspect", "atoms-user-123-42"):
            return (
                0,
                '[{"Image":"sha256:expected","Config":{"Image":"atoms-sandbox:latest","WorkingDir":"/workspace","Cmd":["sleep","infinity"]},"HostConfig":{"PortBindings":{"3000/tcp":[{"HostPort":"49153"}],"8000/tcp":[{"HostPort":"49154"}]}},"Mounts":[{"Destination":"/workspace","Source":"'
                + docker_reported_root
                + '"}]}]',
                "",
            )
        if args == ("docker", "image", "inspect", "atoms-sandbox:latest"):
            return 0, '[{"Id":"sha256:expected"}]', ""
        if args == ("docker", "start", "atoms-user-123-42"):
            return 0, "atoms-user-123-42\n", ""
        raise AssertionError(f"unexpected command: {args}")

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    container_name = await service.ensure_runtime(
        user_id="user-123",
        project_id=42,
        host_root=workspace_root,
    )

    assert container_name == "atoms-user-123-42"
    assert commands[-1] == ("docker", "start", "atoms-user-123-42")


@pytest.mark.asyncio
async def test_ensure_runtime_rejects_existing_container_with_wrong_runtime_shape(tmp_path):
    workspace_root = tmp_path / "user-123" / "42"
    workspace_root.mkdir(parents=True)

    async def fake_run(*args):
        if args[:4] == ("docker", "run", "-d", "--name"):
            return 125, "", 'Conflict. The container name "/atoms-user-123-42" is already in use.'
        if args == ("docker", "inspect", "atoms-user-123-42"):
            return (
                0,
                '[{"Image":"sha256:expected","Config":{"Image":"atoms-sandbox:latest","WorkingDir":"/workspace","Cmd":["python","server.py"]},"HostConfig":{"PortBindings":{"3000/tcp":[{"HostPort":"49153"}]}},"Mounts":[{"Destination":"/workspace","Source":"'
                + str(workspace_root)
                + '"}]}]',
                "",
            )
        if args == ("docker", "image", "inspect", "atoms-sandbox:latest"):
            return 0, '[{"Id":"sha256:expected"}]', ""
        raise AssertionError(f"unexpected command: {args}")

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    with pytest.raises(RuntimeError, match="does not match requested workspace or image"):
        await service.ensure_runtime(
            user_id="user-123",
            project_id=42,
            host_root=workspace_root,
        )


@pytest.mark.asyncio
async def test_ensure_runtime_rejects_conflicting_existing_container(tmp_path):
    workspace_root = tmp_path / "user-123" / "42"
    workspace_root.mkdir(parents=True)

    async def fake_run(*args):
        if args[:4] == ("docker", "run", "-d", "--name"):
            return 125, "", 'Conflict. The container name "/atoms-user-123-42" is already in use.'
        if args == ("docker", "inspect", "atoms-user-123-42"):
            return (
                0,
                '[{"Image":"sha256:stale","Config":{"Image":"old-image:latest","WorkingDir":"/workspace","Cmd":["sleep","infinity"]},"HostConfig":{"PortBindings":{"3000/tcp":[{"HostPort":"49153"}],"8000/tcp":[{"HostPort":"49154"}]}},"Mounts":[{"Destination":"/workspace","Source":"'
                + str(workspace_root)
                + '"}]}]',
                "",
            )
        if args == ("docker", "image", "inspect", "atoms-sandbox:latest"):
            return 0, '[{"Id":"sha256:expected"}]', ""
        raise AssertionError(f"unexpected command: {args}")

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    with pytest.raises(RuntimeError, match="does not match requested workspace or image"):
        await service.ensure_runtime(
            user_id="user-123",
            project_id=42,
            host_root=workspace_root,
        )


@pytest.mark.asyncio
async def test_ensure_runtime_rejects_missing_workspace_directory(tmp_path):
    service = SandboxRuntimeService(project_root=tmp_path)

    with pytest.raises(ValueError, match="host_root must exist as a directory"):
        await service.ensure_runtime(
            user_id="user-123",
            project_id=42,
            host_root=tmp_path / "user-123" / "42",
        )


@pytest.mark.asyncio
async def test_ensure_runtime_times_out_stuck_docker_call(tmp_path):
    workspace_root = tmp_path / "user-123" / "42"
    workspace_root.mkdir(parents=True)

    async def fake_run(*args):
        await asyncio.sleep(0.05)
        return 0, "container-id-123\n", ""

    service = SandboxRuntimeService(
        project_root=tmp_path,
        run_command=fake_run,
        command_timeout_seconds=0.01,
    )

    with pytest.raises(RuntimeError, match="command timed out"):
        await service.ensure_runtime(
            user_id="user-123",
            project_id=42,
            host_root=workspace_root,
        )


@pytest.mark.asyncio
async def test_run_command_kills_process_after_timeout(tmp_path):
    marker = tmp_path / "timeout-marker.txt"
    service = SandboxRuntimeService(
        project_root=tmp_path,
        command_timeout_seconds=0.05,
    )

    with pytest.raises(RuntimeError, match="command timed out"):
        await service._invoke(
            "python",
            "-c",
            (
                "import pathlib, time; "
                "time.sleep(0.2); "
                f"pathlib.Path({str(marker)!r}).write_text('done')"
            ),
        )

    await asyncio.sleep(0.25)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_exec_uses_bash_lc_shape(tmp_path):
    commands = []

    async def fake_run(*args):
        commands.append(args)
        return 0, "ok", ""

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    result = await service.exec("atoms-user-123-42", "npm test")

    assert result == (0, "ok", "")
    assert commands == [
        (
            "docker",
            "exec",
            "-i",
            "atoms-user-123-42",
            "/bin/bash",
            "-lc",
            "npm test",
        )
    ]


@pytest.mark.asyncio
async def test_get_runtime_ports_parses_docker_port_output(tmp_path):
    commands = []

    async def fake_run(*args):
        commands.append(args)
        return (
            0,
            "3000/tcp -> 0.0.0.0:49153\n"
            "3000/tcp -> [::]:49153\n"
            "8000/tcp -> 0.0.0.0:49154\n",
            "",
        )

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    ports = await service.get_runtime_ports("atoms-user-123-42")

    assert commands == [("docker", "port", "atoms-user-123-42")]
    assert ports == {
        "frontend_port": 49153,
        "backend_port": 49154,
        "preview_port": 49153,
    }


def test_container_name_sanitizes_oidc_subjects():
    container_name = SandboxRuntimeService._container_name(
        "Auth0|Jacky/With:Odd Chars",
        42,
    )

    assert container_name.startswith("atoms-auth0-jacky-with-odd-chars-")
    assert container_name.endswith("-42")


def test_container_name_distinguishes_ids_with_same_sanitized_slug():
    first_name = SandboxRuntimeService._container_name("auth0|alice", 42)
    second_name = SandboxRuntimeService._container_name("auth0/alice", 42)

    assert first_name != second_name


def test_container_name_distinguishes_case_only_user_ids():
    first_name = SandboxRuntimeService._container_name("Alice", 42)
    second_name = SandboxRuntimeService._container_name("alice", 42)

    assert first_name != second_name


def test_container_name_preserves_unique_suffix_when_truncated():
    long_user_id = "user-" + ("x" * 200)
    first_name = SandboxRuntimeService._container_name(long_user_id, 42)
    second_name = SandboxRuntimeService._container_name(long_user_id, 43)

    assert len(first_name) <= 128
    assert first_name.endswith("-42")
    assert second_name.endswith("-43")
    assert first_name != second_name


def test_database_service_import_registers_workspace_runtime_model():
    script = (
        "import asyncio\n"
        "from core.database import Base, DatabaseManager\n"
        "\n"
        "class FakeConn:\n"
        "    async def __aenter__(self):\n"
        "        return self\n"
        "    async def __aexit__(self, exc_type, exc, tb):\n"
        "        return False\n"
        "    async def run_sync(self, fn):\n"
        "        print('workspace_runtime_sessions' in Base.metadata.tables)\n"
        "        print('users' in Base.metadata.tables)\n"
        "        print('oidc_states' in Base.metadata.tables)\n"
        "\n"
        "class FakeEngine:\n"
        "    def begin(self):\n"
        "        return FakeConn()\n"
        "\n"
        "async def main():\n"
        "    manager = DatabaseManager()\n"
        "    manager.engine = FakeEngine()\n"
        "    await manager.create_tables()\n"
        "\n"
        "asyncio.run(main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd="/Users/jackywang/Documents/atoms/app/backend",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["True", "True", "True"]


@pytest.mark.asyncio
async def test_database_manager_loads_models_before_repair():
    from core.database import Base, DatabaseManager

    class FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def run_sync(self, fn):
            return None

        async def execute(self, query):
            return None

    class FakeEngine:
        dialect = type("Dialect", (), {"name": "sqlite"})()

        def begin(self):
            return FakeConn()

    manager = DatabaseManager()
    manager.engine = FakeEngine()

    async def fake_repair():
        assert "workspace_runtime_sessions" in Base.metadata.tables

    async def fake_ensure_uniqueness():
        return None

    manager.check_and_repair_existing_tables = fake_repair
    manager._ensure_workspace_runtime_session_uniqueness = fake_ensure_uniqueness

    await manager.create_tables()
    assert manager._initialized is True


@pytest.mark.asyncio
async def test_database_manager_repairs_workspace_runtime_uniqueness():
    from sqlalchemy import text

    from core.database import DatabaseManager

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE workspace_runtime_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id VARCHAR NOT NULL,
                        project_id INTEGER NOT NULL,
                        container_name VARCHAR NOT NULL,
                        status VARCHAR NOT NULL,
                        preview_port INTEGER NULL,
                        frontend_port INTEGER NULL,
                        backend_port INTEGER NULL,
                        created_at DATETIME NULL,
                        updated_at DATETIME NULL
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO workspace_runtime_sessions
                        (user_id, project_id, container_name, status)
                    VALUES
                        ('user-123', 42, 'old-runtime', 'stopped'),
                        ('user-123', 42, 'new-runtime', 'running')
                    """
                )
            )

        manager = DatabaseManager()
        manager.engine = engine
        await manager._ensure_workspace_runtime_session_uniqueness()

        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT COUNT(*), MAX(container_name)
                    FROM workspace_runtime_sessions
                    WHERE user_id = 'user-123' AND project_id = 42
                    """
                )
            )
            count, container_name = result.one()
            assert count == 1
            assert container_name == "new-runtime"

            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        """
                        INSERT INTO workspace_runtime_sessions
                            (user_id, project_id, container_name, status)
                        VALUES
                            ('user-123', 42, 'duplicate-runtime', 'running')
                        """
                    )
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_manager_create_tables_keeps_uninitialized_when_context_exit_fails():
    from core.database import DatabaseManager

    class FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            raise RuntimeError("connection close failed")

        async def run_sync(self, fn):
            return None

    class FakeEngine:
        def begin(self):
            return FakeConn()

    manager = DatabaseManager()
    manager.engine = FakeEngine()

    with pytest.raises(RuntimeError, match="connection close failed"):
        await manager.create_tables()

    assert manager._initialized is False


@pytest.mark.asyncio
async def test_run_command_collects_stdout(tmp_path):
    service = SandboxRuntimeService(project_root=tmp_path)

    result = await service._run_command(
        "python",
        "-c",
        "print('sandbox-ok')",
    )

    assert result == (0, "sandbox-ok\n", "")
