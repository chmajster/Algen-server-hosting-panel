"""feature suite extensions

Revision ID: 0004_feature_suite_extensions
Revises: 0003_operator_and_tickets
Create Date: 2026-04-07 18:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_feature_suite_extensions"
down_revision = "0003_operator_and_tickets"
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

    if "tickets" in tables:
        ticket_columns = _column_names("tickets")
        with op.batch_alter_table("tickets") as batch_op:
            if "first_response_at" not in ticket_columns:
                batch_op.add_column(sa.Column("first_response_at", sa.DateTime(), nullable=True))
            if "first_response_due_at" not in ticket_columns:
                batch_op.add_column(sa.Column("first_response_due_at", sa.DateTime(), nullable=True))
            if "escalated_at" not in ticket_columns:
                batch_op.add_column(sa.Column("escalated_at", sa.DateTime(), nullable=True))

    tables = _table_names()
    if "ticket_attachments" not in tables:
        op.create_table(
            "ticket_attachments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("ticket_message_id", sa.Integer(), sa.ForeignKey("ticket_messages.id"), nullable=True),
            sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("storage_path", sa.String(length=1024), nullable=False),
            sa.Column("mime_type", sa.String(length=120), nullable=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "client_resource_samples" not in tables:
        op.create_table(
            "client_resource_samples",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("cpu_percent", sa.Numeric(7, 2), nullable=True),
            sa.Column("memory_mb", sa.Numeric(12, 2), nullable=True),
            sa.Column("memory_limit_mb", sa.Numeric(12, 2), nullable=True),
            sa.Column("disk_mb", sa.Numeric(12, 2), nullable=True),
            sa.Column("inode_count", sa.BigInteger(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "backup_restore_jobs" not in tables:
        op.create_table(
            "backup_restore_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("backup_id", sa.Integer(), sa.ForeignKey("backups.id"), nullable=False),
            sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("restore_type", sa.String(length=32), nullable=False, server_default="files"),
            sa.Column("target_path", sa.String(length=1024), nullable=True),
            sa.Column("message", sa.String(length=500), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "api_tokens" not in tables:
        op.create_table(
            "api_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("token_prefix", sa.String(length=24), nullable=False),
            sa.Column("token_hash", sa.String(length=128), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "webhook_endpoints" not in tables:
        op.create_table(
            "webhook_endpoints",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("target_url", sa.String(length=500), nullable=False),
            sa.Column("secret", sa.String(length=255), nullable=True),
            sa.Column("event_types_json", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_error", sa.String(length=500), nullable=True),
            sa.Column("last_success_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "webhook_deliveries" not in tables:
        op.create_table(
            "webhook_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("webhook_endpoints.id"), nullable=False),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("status_code", sa.Integer(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("response_excerpt", sa.String(length=500), nullable=True),
            sa.Column("attempted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "tickets" in tables:
        indexes = _index_names("tickets")
        if "ix_tickets_first_response_due" not in indexes:
            op.create_index("ix_tickets_first_response_due", "tickets", ["status", "first_response_due_at"])

    if "ticket_attachments" in tables:
        indexes = _index_names("ticket_attachments")
        if "ix_ticket_attachments_ticket_created" not in indexes:
            op.create_index("ix_ticket_attachments_ticket_created", "ticket_attachments", ["ticket_id", "created_at"])

    if "client_resource_samples" in tables:
        indexes = _index_names("client_resource_samples")
        if "ix_resource_samples_client_created" not in indexes:
            op.create_index("ix_resource_samples_client_created", "client_resource_samples", ["client_id", "created_at"])

    if "backup_restore_jobs" in tables:
        indexes = _index_names("backup_restore_jobs")
        if "ix_restore_jobs_client_status" not in indexes:
            op.create_index("ix_restore_jobs_client_status", "backup_restore_jobs", ["client_id", "status"])

    if "api_tokens" in tables:
        indexes = _index_names("api_tokens")
        if "ix_api_tokens_user_revoked" not in indexes:
            op.create_index("ix_api_tokens_user_revoked", "api_tokens", ["user_id", "revoked_at"])

    if "webhook_endpoints" in tables:
        indexes = _index_names("webhook_endpoints")
        if "ix_webhooks_active_client" not in indexes:
            op.create_index("ix_webhooks_active_client", "webhook_endpoints", ["is_active", "client_id"])


def downgrade():
    tables = _table_names()

    if "webhook_deliveries" in tables:
        op.drop_table("webhook_deliveries")
    if "webhook_endpoints" in tables:
        op.drop_table("webhook_endpoints")
    if "api_tokens" in tables:
        op.drop_table("api_tokens")
    if "backup_restore_jobs" in tables:
        op.drop_table("backup_restore_jobs")
    if "client_resource_samples" in tables:
        op.drop_table("client_resource_samples")
    if "ticket_attachments" in tables:
        op.drop_table("ticket_attachments")

    if "tickets" in tables:
        ticket_columns = _column_names("tickets")
        with op.batch_alter_table("tickets") as batch_op:
            if "escalated_at" in ticket_columns:
                batch_op.drop_column("escalated_at")
            if "first_response_due_at" in ticket_columns:
                batch_op.drop_column("first_response_due_at")
            if "first_response_at" in ticket_columns:
                batch_op.drop_column("first_response_at")
