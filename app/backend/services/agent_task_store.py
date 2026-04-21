import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent_tasks import AgentTasks


@dataclass(frozen=True)
class AgentTaskRecord:
    id: int
    project_id: int
    request_key: str
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
        status: str = "pending",
        blocked_by: Optional[list[str]] = None,
        source_plan_path: str = "",
        owner: str = "engineer",
    ) -> AgentTaskRecord:
        task = AgentTasks(
            project_id=project_id,
            request_key=request_key,
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

    async def list_tasks(self, project_id: int, request_key: Optional[str] = None) -> list[AgentTaskRecord]:
        query = select(AgentTasks).where(AgentTasks.project_id == project_id)
        if request_key is not None:
            query = query.where(AgentTasks.request_key == request_key)
        query = query.order_by(AgentTasks.id.asc())

        result = await self.db.execute(query)
        return [self._to_record(task) for task in result.scalars().all()]

    @staticmethod
    def _serialize_blocked_by(blocked_by: Optional[list[str]]) -> str:
        if blocked_by is None:
            return "[]"
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
            subject=task.subject,
            description=task.description,
            status=task.status,
            blocked_by=cls._parse_blocked_by(task.blocked_by),
            source_plan_path=task.source_plan_path,
            owner=task.owner,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
