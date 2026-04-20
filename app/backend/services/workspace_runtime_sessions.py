import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.workspace_runtime_sessions import WorkspaceRuntimeSessions

logger = logging.getLogger(__name__)


class WorkspaceRuntimeSessionsService:
    """Service layer for workspace runtime session operations."""

    ALLOWED_RUNTIME_FIELDS = {
        "user_id",
        "project_id",
        "container_name",
        "status",
        "preview_port",
        "frontend_port",
        "backend_port",
    }
    REQUIRED_RUNTIME_FIELDS = {"user_id", "project_id", "container_name", "status"}

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict[str, Any]) -> WorkspaceRuntimeSessions:
        try:
            unknown_fields = set(data).difference(self.ALLOWED_RUNTIME_FIELDS)
            if unknown_fields:
                unknown_display = ", ".join(sorted(unknown_fields))
                raise ValueError(f"workspace runtime session does not allow fields: {unknown_display}")

            sanitized_data = {key: value for key, value in data.items() if key in self.ALLOWED_RUNTIME_FIELDS}
            missing_fields = self.REQUIRED_RUNTIME_FIELDS.difference(sanitized_data)
            if missing_fields:
                missing_display = ", ".join(sorted(missing_fields))
                raise ValueError(f"workspace runtime session requires fields: {missing_display}")

            invalid_fields = [
                field
                for field in self.REQUIRED_RUNTIME_FIELDS
                if sanitized_data[field] is None
                or (isinstance(sanitized_data[field], str) and not sanitized_data[field].strip())
            ]
            if invalid_fields:
                invalid_display = ", ".join(sorted(invalid_fields))
                raise ValueError(f"workspace runtime session requires non-empty values for: {invalid_display}")

            obj = WorkspaceRuntimeSessions(**sanitized_data)
            self.db.add(obj)
            await self.db.commit()
            await self.db.refresh(obj)
            return obj
        except IntegrityError:
            await self.db.rollback()
            existing = await self.get_by_project(
                user_id=str(sanitized_data["user_id"]),
                project_id=int(sanitized_data["project_id"]),
            )
            if existing is None:
                raise

            for key, value in sanitized_data.items():
                setattr(existing, key, value)
            try:
                await self.db.commit()
                await self.db.refresh(existing)
                return existing
            except Exception:
                await self.db.rollback()
                logger.exception("Error updating workspace_runtime_sessions record after IntegrityError")
                raise
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
            )
            return result.scalar_one_or_none()
        except Exception:
            logger.exception(
                "Error fetching workspace_runtime_sessions for user_id=%s project_id=%s",
                user_id,
                project_id,
            )
            raise
