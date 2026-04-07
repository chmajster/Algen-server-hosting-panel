"""operator role and tickets

Revision ID: 0003_operator_and_tickets
Revises: 0002_optional_2fa_and_online_payments
Create Date: 2026-04-07 16:30:00
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision = "0003_operator_and_tickets"
down_revision = "0002_optional_2fa_and_online_payments"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()

    if "roles" in _table_names():
        operator_exists = bind.execute(sa.text("SELECT 1 FROM roles WHERE name = :name"), {"name": "operator"}).scalar()
        if not operator_exists:
            now = datetime.utcnow()
            bind.execute(
                sa.text(
                    """
                    INSERT INTO roles (name, description, created_at, updated_at)
                    VALUES (:name, :description, :created_at, :updated_at)
                    """
                ),
                {
                    "name": "operator",
                    "description": "Operator",
                    "created_at": now,
                    "updated_at": now,
                },
            )

    if "tickets" not in _table_names():
        op.create_table(
            "tickets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("assigned_to_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("subject", sa.String(length=200), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=True),
            sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("last_message_at", sa.DateTime(), nullable=True),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "ticket_messages" not in _table_names():
        op.create_table(
            "ticket_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "tickets" in _table_names():
        ticket_indexes = _index_names("tickets")
        if "ix_tickets_client_status" not in ticket_indexes:
            op.create_index("ix_tickets_client_status", "tickets", ["client_id", "status"])
        if "ix_tickets_status_priority" not in ticket_indexes:
            op.create_index("ix_tickets_status_priority", "tickets", ["status", "priority"])

    if "ticket_messages" in _table_names():
        ticket_message_indexes = _index_names("ticket_messages")
        if "ix_ticket_messages_ticket_created" not in ticket_message_indexes:
            op.create_index("ix_ticket_messages_ticket_created", "ticket_messages", ["ticket_id", "created_at"])


def downgrade():
    if "ticket_messages" in _table_names():
        op.drop_table("ticket_messages")
    if "tickets" in _table_names():
        op.drop_table("tickets")

    if "roles" in _table_names():
        bind = op.get_bind()
        bind.execute(sa.text("DELETE FROM roles WHERE name = :name"), {"name": "operator"})
