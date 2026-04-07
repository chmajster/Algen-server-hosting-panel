"""anti-fraud, registrar integration and overdue reminders

Revision ID: 0008_growth_risk_billing_suite
Revises: 0007_ticket_bulk_export_suite
Create Date: 2026-04-08 01:45:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008_growth_risk_billing_suite"
down_revision = "0007_ticket_bulk_export_suite"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    tables = _table_names()

    if "registration_fraud_checks" not in tables:
        op.create_table(
            "registration_fraud_checks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("username", sa.String(length=80), nullable=False),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=500), nullable=True),
            sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="low"),
            sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reasons_json", sa.JSON(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("review_note", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "domain_registrations" not in tables:
        op.create_table(
            "domain_registrations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("domain_id", sa.Integer(), sa.ForeignKey("domains.id"), nullable=False),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("registrar", sa.String(length=64), nullable=False, server_default="mock"),
            sa.Column("external_registration_id", sa.String(length=191), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("registered_on", sa.Date(), nullable=True),
            sa.Column("expires_on", sa.Date(), nullable=True),
            sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("name_servers_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(), nullable=True),
            sa.Column("last_sync_status", sa.String(length=32), nullable=True),
            sa.Column("last_sync_message", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("domain_id", name="uq_domain_registrations_domain_id"),
            sa.UniqueConstraint("external_registration_id", name="uq_domain_registrations_external_id"),
        )

    tables = _table_names()
    if "overdue_reminders" not in tables:
        op.create_table(
            "overdue_reminders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("client_service_id", sa.Integer(), sa.ForeignKey("client_services.id"), nullable=False),
            sa.Column("billing_cycle_id", sa.Integer(), sa.ForeignKey("billing_cycles.id"), nullable=False),
            sa.Column("reminder_type", sa.String(length=32), nullable=False, server_default="email"),
            sa.Column("day_offset", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="sent"),
            sa.Column("recipient", sa.String(length=255), nullable=True),
            sa.Column("subject", sa.String(length=255), nullable=True),
            sa.Column("message", sa.String(length=500), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "billing_cycle_id",
                "reminder_type",
                "day_offset",
                name="uq_overdue_reminder_cycle_type_day",
            ),
        )

    if "registration_fraud_checks" in _table_names():
        indexes = _index_names("registration_fraud_checks")
        if "ix_registration_fraud_checks_user_id" not in indexes:
            op.create_index("ix_registration_fraud_checks_user_id", "registration_fraud_checks", ["user_id"], unique=False)
        if "ix_registration_fraud_checks_reviewed_by_user_id" not in indexes:
            op.create_index(
                "ix_registration_fraud_checks_reviewed_by_user_id",
                "registration_fraud_checks",
                ["reviewed_by_user_id"],
                unique=False,
            )
        if "ix_registration_fraud_checks_email" not in indexes:
            op.create_index("ix_registration_fraud_checks_email", "registration_fraud_checks", ["email"], unique=False)
        if "ix_registration_fraud_checks_username" not in indexes:
            op.create_index("ix_registration_fraud_checks_username", "registration_fraud_checks", ["username"], unique=False)
        if "ix_registration_fraud_checks_ip_address" not in indexes:
            op.create_index("ix_registration_fraud_checks_ip_address", "registration_fraud_checks", ["ip_address"], unique=False)
        if "ix_registration_fraud_checks_score" not in indexes:
            op.create_index("ix_registration_fraud_checks_score", "registration_fraud_checks", ["score"], unique=False)
        if "ix_registration_fraud_checks_risk_level" not in indexes:
            op.create_index("ix_registration_fraud_checks_risk_level", "registration_fraud_checks", ["risk_level"], unique=False)
        if "ix_registration_fraud_checks_blocked" not in indexes:
            op.create_index("ix_registration_fraud_checks_blocked", "registration_fraud_checks", ["blocked"], unique=False)
        if "ix_registration_fraud_checks_reviewed_at" not in indexes:
            op.create_index("ix_registration_fraud_checks_reviewed_at", "registration_fraud_checks", ["reviewed_at"], unique=False)
        if "ix_fraud_checks_level_created" not in indexes:
            op.create_index(
                "ix_fraud_checks_level_created",
                "registration_fraud_checks",
                ["risk_level", "created_at"],
                unique=False,
            )

    if "domain_registrations" in _table_names():
        indexes = _index_names("domain_registrations")
        if "ix_domain_registrations_client_id" not in indexes:
            op.create_index("ix_domain_registrations_client_id", "domain_registrations", ["client_id"], unique=False)
        if "ix_domain_registrations_registrar" not in indexes:
            op.create_index("ix_domain_registrations_registrar", "domain_registrations", ["registrar"], unique=False)
        if "ix_domain_registrations_status" not in indexes:
            op.create_index("ix_domain_registrations_status", "domain_registrations", ["status"], unique=False)
        if "ix_domain_registrations_expires_on" not in indexes:
            op.create_index("ix_domain_registrations_expires_on", "domain_registrations", ["expires_on"], unique=False)
        if "ix_domain_registrations_last_synced_at" not in indexes:
            op.create_index(
                "ix_domain_registrations_last_synced_at",
                "domain_registrations",
                ["last_synced_at"],
                unique=False,
            )
        if "ix_domain_registrations_last_sync_status" not in indexes:
            op.create_index(
                "ix_domain_registrations_last_sync_status",
                "domain_registrations",
                ["last_sync_status"],
                unique=False,
            )
        if "ix_domain_registrations_provider_expiry" not in indexes:
            op.create_index(
                "ix_domain_registrations_provider_expiry",
                "domain_registrations",
                ["registrar", "expires_on"],
                unique=False,
            )

    if "overdue_reminders" in _table_names():
        indexes = _index_names("overdue_reminders")
        if "ix_overdue_reminders_client_id" not in indexes:
            op.create_index("ix_overdue_reminders_client_id", "overdue_reminders", ["client_id"], unique=False)
        if "ix_overdue_reminders_client_service_id" not in indexes:
            op.create_index(
                "ix_overdue_reminders_client_service_id",
                "overdue_reminders",
                ["client_service_id"],
                unique=False,
            )
        if "ix_overdue_reminders_billing_cycle_id" not in indexes:
            op.create_index(
                "ix_overdue_reminders_billing_cycle_id",
                "overdue_reminders",
                ["billing_cycle_id"],
                unique=False,
            )
        if "ix_overdue_reminders_reminder_type" not in indexes:
            op.create_index("ix_overdue_reminders_reminder_type", "overdue_reminders", ["reminder_type"], unique=False)
        if "ix_overdue_reminders_status" not in indexes:
            op.create_index("ix_overdue_reminders_status", "overdue_reminders", ["status"], unique=False)
        if "ix_overdue_reminders_sent_at" not in indexes:
            op.create_index("ix_overdue_reminders_sent_at", "overdue_reminders", ["sent_at"], unique=False)
        if "ix_overdue_reminders_client_sent" not in indexes:
            op.create_index(
                "ix_overdue_reminders_client_sent",
                "overdue_reminders",
                ["client_id", "sent_at"],
                unique=False,
            )


def downgrade():
    tables = _table_names()

    if "overdue_reminders" in tables:
        indexes = _index_names("overdue_reminders")
        for index_name in [
            "ix_overdue_reminders_client_sent",
            "ix_overdue_reminders_sent_at",
            "ix_overdue_reminders_status",
            "ix_overdue_reminders_reminder_type",
            "ix_overdue_reminders_billing_cycle_id",
            "ix_overdue_reminders_client_service_id",
            "ix_overdue_reminders_client_id",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="overdue_reminders")
        op.drop_table("overdue_reminders")

    tables = _table_names()
    if "domain_registrations" in tables:
        indexes = _index_names("domain_registrations")
        for index_name in [
            "ix_domain_registrations_provider_expiry",
            "ix_domain_registrations_last_sync_status",
            "ix_domain_registrations_last_synced_at",
            "ix_domain_registrations_expires_on",
            "ix_domain_registrations_status",
            "ix_domain_registrations_registrar",
            "ix_domain_registrations_client_id",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="domain_registrations")
        op.drop_table("domain_registrations")

    tables = _table_names()
    if "registration_fraud_checks" in tables:
        indexes = _index_names("registration_fraud_checks")
        for index_name in [
            "ix_fraud_checks_level_created",
            "ix_registration_fraud_checks_reviewed_at",
            "ix_registration_fraud_checks_blocked",
            "ix_registration_fraud_checks_risk_level",
            "ix_registration_fraud_checks_score",
            "ix_registration_fraud_checks_ip_address",
            "ix_registration_fraud_checks_username",
            "ix_registration_fraud_checks_email",
            "ix_registration_fraud_checks_reviewed_by_user_id",
            "ix_registration_fraud_checks_user_id",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="registration_fraud_checks")
        op.drop_table("registration_fraud_checks")
