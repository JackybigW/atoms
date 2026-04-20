import pytest
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
async def test_ensure_runtime_builds_docker_run_command(tmp_path):
    commands = []

    async def fake_run(*args):
        commands.append(args)
        return "container-id-123"

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    runtime = await service.ensure_runtime(
        user_id="user-123",
        project_id=42,
        host_root=tmp_path / "user-123" / "42",
    )

    assert runtime["container_name"] == "atoms-user-123-42"
    assert runtime["container_id"] == "container-id-123"
    assert runtime["status"] == "running"

    joined = " ".join(commands[0])
    assert "docker run -d" in joined
    assert "-v" in joined
    assert str(tmp_path / "user-123" / "42") in joined
    assert "/workspace" in joined


@pytest.mark.asyncio
async def test_exec_uses_bash_lc_shape(tmp_path):
    commands = []

    async def fake_run(*args):
        commands.append(args)
        return "ok"

    service = SandboxRuntimeService(project_root=tmp_path, run_command=fake_run)

    result = await service.exec("atoms-user-123-42", "npm test")

    assert result == "ok"
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
async def test_run_command_collects_stdout(tmp_path):
    service = SandboxRuntimeService(project_root=tmp_path)

    result = await service._run_command(
        "python",
        "-c",
        "print('sandbox-ok')",
    )

    assert result == "sandbox-ok"
