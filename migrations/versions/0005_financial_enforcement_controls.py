"""financial enforcement controls

Revision ID: 0005_financial_enforcement_controls
Revises: 0004_feature_suite_extensions
Create Date: 2026-04-07 22:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005_financial_enforcement_controls"
down_revision = "0004_feature_suite_extensions"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade():
    tables = _table_names()

    if "service_plans" in tables:
        columns = _column_names("service_plans")
        if "grace_days_override" not in columns:
            with op.batch_alter_table("service_plans") as batch_op:
                batch_op.add_column(sa.Column("grace_days_override", sa.Integer(), nullable=True))

    if "client_services" in tables:
        columns = _column_names("client_services")
        if "financial_enforcement_override" not in columns:
            with op.batch_alter_table("client_services") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "financial_enforcement_override",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )


def downgrade():
    tables = _table_names()

    if "client_services" in tables and "financial_enforcement_override" in _column_names("client_services"):
        with op.batch_alter_table("client_services") as batch_op:
            batch_op.drop_column("financial_enforcement_override")

    if "service_plans" in tables and "grace_days_override" in _column_names("service_plans"):
        with op.batch_alter_table("service_plans") as batch_op:
            batch_op.drop_column("grace_days_override")
