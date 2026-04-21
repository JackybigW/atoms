import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent_tasks import AgentTasks


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
    ) -> AgentTasks:
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
        return task

    async def list_tasks(self, project_id: int, request_key: Optional[str] = None) -> list[AgentTasks]:
        query = select(AgentTasks).where(AgentTasks.project_id == project_id)
        if request_key is not None:
            query = query.where(AgentTasks.request_key == request_key)
        query = query.order_by(AgentTasks.id.asc())

        result = await self.db.execute(query)
        return result.scalars().all()

    @staticmethod
    def _serialize_blocked_by(blocked_by: Optional[list[str]]) -> str:
        if blocked_by is None:
            return "[]"
        return json.dumps(blocked_by, ensure_ascii=False)
