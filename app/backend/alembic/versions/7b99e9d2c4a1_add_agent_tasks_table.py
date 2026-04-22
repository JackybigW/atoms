"""add_agent_tasks_table

Revision ID: 7b99e9d2c4a1
Revises: 29ca809015cb
Create Date: 2026-04-22 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7b99e9d2c4a1"
down_revision: Union[str, Sequence[str], None] = "29ca809015cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("task_key", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("blocked_by", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_plan_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("owner", sa.String(length=64), nullable=False, server_default="engineer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_tasks_id"), "agent_tasks", ["id"], unique=False)
    op.create_index(op.f("ix_agent_tasks_project_id"), "agent_tasks", ["project_id"], unique=False)
    op.create_index(op.f("ix_agent_tasks_request_key"), "agent_tasks", ["request_key"], unique=False)
    op.create_index(op.f("ix_agent_tasks_task_key"), "agent_tasks", ["task_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_tasks_task_key"), table_name="agent_tasks")
    op.drop_index(op.f("ix_agent_tasks_request_key"), table_name="agent_tasks")
    op.drop_index(op.f("ix_agent_tasks_project_id"), table_name="agent_tasks")
    op.drop_index(op.f("ix_agent_tasks_id"), table_name="agent_tasks")
    op.drop_table("agent_tasks")
