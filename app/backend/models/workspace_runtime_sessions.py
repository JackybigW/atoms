from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint


class WorkspaceRuntimeSessions(Base):
    __tablename__ = "workspace_runtime_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_workspace_runtime_sessions_user_project"),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    project_id = Column(Integer, nullable=False)
    container_name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    preview_port = Column(Integer, nullable=True)
    frontend_port = Column(Integer, nullable=True)
    backend_port = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
