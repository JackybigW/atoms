"""add project_number for per-user project isolation

Revision ID: 8f3c2d1e4a5b
Revises: 29ca809015cb
Create Date: 2026-04-26 10:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f3c2d1e4a5b'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add nullable column
    op.add_column('projects', sa.Column('project_number', sa.Integer(), nullable=True))

    # 2. Backfill: assign per-user sequential numbers ordered by created_at
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, user_id FROM projects ORDER BY user_id, created_at ASC")
    ).fetchall()

    user_counters = {}
    for row in rows:
        uid = row[1]
        user_counters[uid] = user_counters.get(uid, 0) + 1
        conn.execute(
            sa.text("UPDATE projects SET project_number = :num WHERE id = :id"),
            {"num": user_counters[uid], "id": row[0]},
        )

    # 3. Make non-nullable
    op.alter_column('projects', 'project_number', nullable=False)

    # 4. Add unique constraint
    op.create_unique_constraint('uq_user_project_number', 'projects', ['user_id', 'project_number'])


def downgrade() -> None:
    op.drop_constraint('uq_user_project_number', 'projects', type_='unique')
    op.drop_column('projects', 'project_number')
