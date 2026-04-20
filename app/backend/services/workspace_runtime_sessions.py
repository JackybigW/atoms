import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.workspace_runtime_sessions import WorkspaceRuntimeSessions

logger = logging.getLogger(__name__)


class WorkspaceRuntimeSessionsService:
    """Service layer for workspace runtime session operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict[str, Any]) -> WorkspaceRuntimeSessions:
        try:
            obj = WorkspaceRuntimeSessions(**data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            return obj
        except Exception:
            await self.db.rollback()
            logger.exception("Error creating workspace_runtime_sessions record")
            raise

    async def get_by_project(
        self,
        user_id: str,
        project_id: int,
    ) -> Optional[WorkspaceRuntimeSessions]:
        try:
            result = await self.db.execute(
                select(WorkspaceRuntimeSessions)
                .where(WorkspaceRuntimeSessions.user_id == user_id)
                .where(WorkspaceRuntimeSessions.project_id == project_id)
                .order_by(WorkspaceRuntimeSessions.id.desc())
            )
            return result.scalars().first()
        except Exception:
            logger.exception(
                "Error fetching workspace_runtime_sessions for user_id=%s project_id=%s",
                user_id,
                project_id,
            )
            raise
