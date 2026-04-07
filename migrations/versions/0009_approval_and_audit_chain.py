"""approval workflow and immutable audit chain

Revision ID: 0009_approval_and_audit_chain
Revises: 0008_growth_risk_billing_suite
Create Date: 2026-04-08 10:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_approval_and_audit_chain"
down_revision = "0008_growth_risk_billing_suite"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade():
    tables = _table_names()

    if "approval_requests" not in tables:
        op.create_table(
            "approval_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("action_key", sa.String(length=64), nullable=False),
            sa.Column("target_type", sa.String(length=64), nullable=False),
            sa.Column("target_id", sa.String(length=120), nullable=False),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("executed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("min_approver_role", sa.String(length=32), nullable=False, server_default="operator"),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("rejected_at", sa.DateTime(), nullable=True),
            sa.Column("executed_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.Column("execution_error", sa.String(length=500), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "approval_decisions" not in tables:
        op.create_table(
            "approval_decisions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("approval_request_id", sa.Integer(), sa.ForeignKey("approval_requests.id"), nullable=False),
            sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("decision", sa.String(length=16), nullable=False),
            sa.Column("note", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "approval_request_id",
                "decided_by_user_id",
                name="uq_approval_decision_request_user",
            ),
        )

    tables = _table_names()
    if "activity_logs" in tables:
        columns = _column_names("activity_logs")
        if "chain_sequence" not in columns:
            op.add_column("activity_logs", sa.Column("chain_sequence", sa.Integer(), nullable=True))
        if "chain_prev_hash" not in columns:
            op.add_column("activity_logs", sa.Column("chain_prev_hash", sa.String(length=64), nullable=True))
        if "chain_hash" not in columns:
            op.add_column("activity_logs", sa.Column("chain_hash", sa.String(length=64), nullable=True))
        if "chain_version" not in columns:
            op.add_column("activity_logs", sa.Column("chain_version", sa.String(length=16), nullable=True))
        if "chain_legacy" not in columns:
            op.add_column(
                "activity_logs",
                sa.Column("chain_legacy", sa.Boolean(), nullable=False, server_default=sa.false()),
            )

    if "approval_requests" in _table_names():
        indexes = _index_names("approval_requests")
        if "ix_approval_requests_action_key" not in indexes:
            op.create_index("ix_approval_requests_action_key", "approval_requests", ["action_key"], unique=False)
        if "ix_approval_requests_target_type" not in indexes:
            op.create_index("ix_approval_requests_target_type", "approval_requests", ["target_type"], unique=False)
        if "ix_approval_requests_target_id" not in indexes:
            op.create_index("ix_approval_requests_target_id", "approval_requests", ["target_id"], unique=False)
        if "ix_approval_requests_client_id" not in indexes:
            op.create_index("ix_approval_requests_client_id", "approval_requests", ["client_id"], unique=False)
        if "ix_approval_requests_requested_by_user_id" not in indexes:
            op.create_index(
                "ix_approval_requests_requested_by_user_id",
                "approval_requests",
                ["requested_by_user_id"],
                unique=False,
            )
        if "ix_approval_requests_executed_by_user_id" not in indexes:
            op.create_index(
                "ix_approval_requests_executed_by_user_id",
                "approval_requests",
                ["executed_by_user_id"],
                unique=False,
            )
        if "ix_approval_requests_status" not in indexes:
            op.create_index("ix_approval_requests_status", "approval_requests", ["status"], unique=False)
        if "ix_approval_requests_expires_at" not in indexes:
            op.create_index("ix_approval_requests_expires_at", "approval_requests", ["expires_at"], unique=False)
        if "ix_approval_requests_approved_at" not in indexes:
            op.create_index("ix_approval_requests_approved_at", "approval_requests", ["approved_at"], unique=False)
        if "ix_approval_requests_rejected_at" not in indexes:
            op.create_index("ix_approval_requests_rejected_at", "approval_requests", ["rejected_at"], unique=False)
        if "ix_approval_requests_executed_at" not in indexes:
            op.create_index("ix_approval_requests_executed_at", "approval_requests", ["executed_at"], unique=False)
        if "ix_approval_requests_cancelled_at" not in indexes:
            op.create_index("ix_approval_requests_cancelled_at", "approval_requests", ["cancelled_at"], unique=False)
        if "ix_approval_requests_action_status" not in indexes:
            op.create_index(
                "ix_approval_requests_action_status",
                "approval_requests",
                ["action_key", "status"],
                unique=False,
            )
        if "ix_approval_requests_target_status" not in indexes:
            op.create_index(
                "ix_approval_requests_target_status",
                "approval_requests",
                ["target_type", "target_id", "status"],
                unique=False,
            )

    if "approval_decisions" in _table_names():
        indexes = _index_names("approval_decisions")
        if "ix_approval_decisions_approval_request_id" not in indexes:
            op.create_index(
                "ix_approval_decisions_approval_request_id",
                "approval_decisions",
                ["approval_request_id"],
                unique=False,
            )
        if "ix_approval_decisions_decided_by_user_id" not in indexes:
            op.create_index(
                "ix_approval_decisions_decided_by_user_id",
                "approval_decisions",
                ["decided_by_user_id"],
                unique=False,
            )
        if "ix_approval_decisions_decision" not in indexes:
            op.create_index("ix_approval_decisions_decision", "approval_decisions", ["decision"], unique=False)
        if "ix_approval_decisions_request_decision" not in indexes:
            op.create_index(
                "ix_approval_decisions_request_decision",
                "approval_decisions",
                ["approval_request_id", "decision"],
                unique=False,
            )

    if "activity_logs" in _table_names():
        indexes = _index_names("activity_logs")
        if "ix_activity_logs_chain_hash" not in indexes:
            op.create_index("ix_activity_logs_chain_hash", "activity_logs", ["chain_hash"], unique=False)
        if "ix_activity_logs_chain_legacy" not in indexes:
            op.create_index("ix_activity_logs_chain_legacy", "activity_logs", ["chain_legacy"], unique=False)
        if "ix_activity_logs_chain_integrity" not in indexes:
            op.create_index(
                "ix_activity_logs_chain_integrity",
                "activity_logs",
                ["chain_sequence", "chain_hash"],
                unique=False,
            )
        op.execute(sa.text("UPDATE activity_logs SET chain_legacy = 1 WHERE chain_hash IS NULL"))


def downgrade():
    tables = _table_names()

    if "activity_logs" in tables:
        indexes = _index_names("activity_logs")
        for index_name in [
            "ix_activity_logs_chain_integrity",
            "ix_activity_logs_chain_legacy",
            "ix_activity_logs_chain_hash",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="activity_logs")

        columns = _column_names("activity_logs")
        for column_name in ["chain_legacy", "chain_version", "chain_hash", "chain_prev_hash", "chain_sequence"]:
            if column_name in columns:
                op.drop_column("activity_logs", column_name)

    tables = _table_names()
    if "approval_decisions" in tables:
        indexes = _index_names("approval_decisions")
        for index_name in [
            "ix_approval_decisions_request_decision",
            "ix_approval_decisions_decision",
            "ix_approval_decisions_decided_by_user_id",
            "ix_approval_decisions_approval_request_id",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="approval_decisions")
        op.drop_table("approval_decisions")

    tables = _table_names()
    if "approval_requests" in tables:
        indexes = _index_names("approval_requests")
        for index_name in [
            "ix_approval_requests_target_status",
            "ix_approval_requests_action_status",
            "ix_approval_requests_cancelled_at",
            "ix_approval_requests_executed_at",
            "ix_approval_requests_rejected_at",
            "ix_approval_requests_approved_at",
            "ix_approval_requests_expires_at",
            "ix_approval_requests_status",
            "ix_approval_requests_executed_by_user_id",
            "ix_approval_requests_requested_by_user_id",
            "ix_approval_requests_client_id",
            "ix_approval_requests_target_id",
            "ix_approval_requests_target_type",
            "ix_approval_requests_action_key",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="approval_requests")
        op.drop_table("approval_requests")
