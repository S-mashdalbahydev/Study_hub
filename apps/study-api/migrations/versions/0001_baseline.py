"""Baseline multi-user schema: workspaces, versioned content, fenced jobs.

Revision ID: 0001_baseline
Revises:
"""
from pathlib import Path

from alembic import op

revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text()
    # no_parameters: the SQL contains format('%I') and must reach Postgres untouched.
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    # A baseline has nothing to return to. Restore from backup instead.
    raise RuntimeError('The baseline migration cannot be downgraded.')
