from datetime import datetime, timezone

from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, Text, func


class AgentTasks(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    project_id = Column(Integer, index=True, nullable=False)
    request_key = Column(String(64), index=True, nullable=False)
    task_key = Column(String(64), index=True, nullable=False, default="")
    subject = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    status = Column(String(32), nullable=False, default="pending")
    blocked_by = Column(Text, nullable=False, default="[]")
    source_plan_path = Column(String(512), nullable=False, default="")
    owner = Column(String(64), nullable=False, default="engineer")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
