"""optional 2fa and online payments

Revision ID: 0002_optional_2fa_and_online_payments
Revises: 0001_initial
Create Date: 2026-04-07 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002_optional_2fa_and_online_payments"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    tables = _table_names()
    if "users" in tables:
        user_columns = _column_names("users")
        with op.batch_alter_table("users") as batch_op:
            if "two_factor_enabled" not in user_columns:
                batch_op.add_column(sa.Column("two_factor_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
            if "two_factor_method" not in user_columns:
                batch_op.add_column(sa.Column("two_factor_method", sa.String(length=16), nullable=False, server_default="totp"))
            if "two_factor_secret" not in user_columns:
                batch_op.add_column(sa.Column("two_factor_secret", sa.String(length=128), nullable=True))

    tables = _table_names()
    if "online_payments" not in tables:
        op.create_table(
            "online_payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="PLN"),
            sa.Column("provider", sa.String(length=32), nullable=False, server_default="stripe"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("description", sa.String(length=255), nullable=False),
            sa.Column("external_id", sa.String(length=191), nullable=True, unique=True),
            sa.Column("provider_event_id", sa.String(length=191), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "online_payments" in _table_names():
        indexes = _index_names("online_payments")
        if "ix_online_payments_client_status" not in indexes:
            op.create_index("ix_online_payments_client_status", "online_payments", ["client_id", "status"])


def downgrade():
    if "online_payments" in _table_names():
        op.drop_table("online_payments")

    if "users" in _table_names():
        user_columns = _column_names("users")
        with op.batch_alter_table("users") as batch_op:
            if "two_factor_secret" in user_columns:
                batch_op.drop_column("two_factor_secret")
            if "two_factor_method" in user_columns:
                batch_op.drop_column("two_factor_method")
            if "two_factor_enabled" in user_columns:
                batch_op.drop_column("two_factor_enabled")
