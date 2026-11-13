"""fase 2: cost_is_estimated, rq_job_id, requeue_count

Revision ID: b3f1a2c9d4e5
Revises: 57091477d086
Create Date: 2026-11-13 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3f1a2c9d4e5'
down_revision = '57091477d086'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        'research_runs', 'cost_estimated', new_column_name='cost_is_estimated'
    )
    op.add_column('research_runs', sa.Column('rq_job_id', sa.Text(), nullable=True))
    op.add_column(
        'research_runs',
        sa.Column('requeue_count', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('research_runs', 'requeue_count')
    op.drop_column('research_runs', 'rq_job_id')
    op.alter_column(
        'research_runs', 'cost_is_estimated', new_column_name='cost_estimated'
    )
