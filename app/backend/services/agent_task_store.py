import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent_tasks import AgentTasks


@dataclass(frozen=True)
class AgentTaskRecord:
    id: int
    project_id: int
    request_key: str
    task_key: str
    subject: str
    description: str
    status: str
    blocked_by: list[str]
    source_plan_path: str
    owner: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class AgentTaskStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_task(
        self,
        project_id: int,
        request_key: str,
        subject: str,
        description: str,
        task_key: str = "",
        status: str = "pending",
        blocked_by: Optional[list[str]] = None,
        source_plan_path: str = "",
        owner: str = "engineer",
    ) -> AgentTaskRecord:
        task = AgentTasks(
            project_id=project_id,
            request_key=request_key,
            task_key=task_key,
            subject=subject,
            description=description,
            status=status,
            blocked_by=self._serialize_blocked_by(blocked_by),
            source_plan_path=source_plan_path,
            owner=owner,
        )
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return self._to_record(task)

    async def update_task(
        self,
        task_id: int,
        status: Optional[str] = None,
        blocked_by: Optional[list[str]] = None,
        source_plan_path: Optional[str] = None,
    ) -> Optional["AgentTaskRecord"]:
        result = await self.db.execute(select(AgentTasks).where(AgentTasks.id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            return None
        if status is not None:
            task.status = status
        if blocked_by is not None:
            task.blocked_by = self._serialize_blocked_by(blocked_by)
        if source_plan_path is not None:
            task.source_plan_path = source_plan_path
        await self.db.commit()
        await self.db.refresh(task)
        return self._to_record(task)

    async def list_tasks(self, project_id: int, request_key: Optional[str] = None) -> list[AgentTaskRecord]:
        query = select(AgentTasks).where(AgentTasks.project_id == project_id)
        if request_key is not None:
            query = query.where(AgentTasks.request_key == request_key)
        query = query.order_by(AgentTasks.id.asc())

        result = await self.db.execute(query)
        return [self._to_record(task) for task in result.scalars().all()]

    async def sync_request_tasks(
        self,
        project_id: int,
        request_key: str,
        source_plan_path: str,
        items: list[dict],
        owner: str = "engineer",
    ) -> list[AgentTaskRecord]:
        existing = await self.list_tasks(project_id=project_id, request_key=request_key)
        existing_by_key = {task.task_key: task for task in existing}
        seen_keys: list[str] = []

        for item in items:
            task_key = str(item.get("id") or "").strip()
            if not task_key:
                raise ValueError("todo items must include a non-empty id")
            seen_keys.append(task_key)
            status = str(item.get("status") or "pending")
            subject = str(item.get("text") or "")
            blocked_by = item.get("blocked_by")

            existing_task = existing_by_key.get(task_key)
            if existing_task is None:
                await self.create_task(
                    project_id=project_id,
                    request_key=request_key,
                    task_key=task_key,
                    subject=subject,
                    description="",
                    status=status,
                    blocked_by=blocked_by,
                    source_plan_path=source_plan_path,
                    owner=owner,
                )
                continue

            task_row = await self.db.get(AgentTasks, existing_task.id)
            if task_row is None:
                continue
            task_row.subject = subject
            task_row.status = status
            task_row.blocked_by = self._serialize_blocked_by(blocked_by)
            task_row.source_plan_path = source_plan_path
            task_row.owner = owner

        stale_keys = set(existing_by_key) - set(seen_keys)
        if stale_keys:
            await self.db.execute(
                delete(AgentTasks).where(
                    AgentTasks.project_id == project_id,
                    AgentTasks.request_key == request_key,
                    AgentTasks.task_key.in_(stale_keys),
                )
            )

        await self.db.commit()
        return await self.list_tasks(project_id=project_id, request_key=request_key)

    @staticmethod
    def _serialize_blocked_by(blocked_by: Optional[list[str]]) -> str:
        if blocked_by is None:
            return "[]"
        if not isinstance(blocked_by, list):
            raise TypeError(f"blocked_by must be a list, got {type(blocked_by).__name__}")
        if not all(isinstance(item, str) for item in blocked_by):
            raise ValueError("blocked_by entries must all be strings")
        return json.dumps(blocked_by, ensure_ascii=False)

    @staticmethod
    def _parse_blocked_by(blocked_by: str | list[str] | None) -> list[str]:
        if blocked_by is None:
            return []
        if isinstance(blocked_by, list):
            return blocked_by if all(isinstance(item, str) for item in blocked_by) else []
        if not blocked_by:
            return []
        try:
            parsed = json.loads(blocked_by)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed) else []

    @classmethod
    def _to_record(cls, task: AgentTasks) -> AgentTaskRecord:
        return AgentTaskRecord(
            id=task.id,
            project_id=task.project_id,
            request_key=task.request_key,
            task_key=task.task_key,
            subject=task.subject,
            description=task.description,
            status=task.status,
            blocked_by=cls._parse_blocked_by(task.blocked_by),
            source_plan_path=task.source_plan_path,
            owner=task.owner,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
