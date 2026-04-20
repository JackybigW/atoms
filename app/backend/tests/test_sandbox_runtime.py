import pytest
from sqlalchemy import text
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
async def test_workspace_runtime_sessions_service_handles_legacy_duplicates():
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
                        (user_id, project_id, container_name, status, preview_port, frontend_port, backend_port)
                    VALUES
                        ('user-123', 42, 'atoms-user-123-42-old', 'stopped', 3000, 5173, 8000),
                        ('user-123', 42, 'atoms-user-123-42-new', 'running', 3001, 5174, 8001)
                    """
                )
            )

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            service = WorkspaceRuntimeSessionsService(db)
            runtime = await service.get_by_project(user_id="user-123", project_id=42)

        assert runtime is not None
        assert runtime.container_name == "atoms-user-123-42-new"
        assert runtime.status == "running"
        assert runtime.preview_port == 3001
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_runtime_builds_docker_run_command(tmp_path):
    commands = []

    async def fake_run(*args):
        commands.append(args)
        return 0, "container-id-123\n", ""

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    container_name = await service.ensure_runtime(
        user_id="user-123",
        project_id=42,
        host_root=tmp_path / "user-123" / "42",
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
    async def fake_run(*args):
        return 125, "", "docker run failed"

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    with pytest.raises(RuntimeError, match="docker run failed"):
        await service.ensure_runtime(
            user_id="user-123",
            project_id=42,
            host_root=tmp_path / "user-123" / "42",
        )


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


@pytest.mark.asyncio
async def test_run_command_collects_stdout(tmp_path):
    service = SandboxRuntimeService(project_root=tmp_path)

    result = await service._run_command(
        "python",
        "-c",
        "print('sandbox-ok')",
    )

    assert result == (0, "sandbox-ok\n", "")
