"""operations and security suite

Revision ID: 0006_operations_and_security_suite
Revises: 0005_financial_enforcement_controls
Create Date: 2026-04-07 23:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_operations_and_security_suite"
down_revision = "0005_financial_enforcement_controls"
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

    if "external_backup_targets" not in tables:
        op.create_table(
            "external_backup_targets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("endpoint_url", sa.String(length=500), nullable=True),
            sa.Column("bucket_name", sa.String(length=255), nullable=False),
            sa.Column("region", sa.String(length=64), nullable=True),
            sa.Column("access_key_env", sa.String(length=120), nullable=False),
            sa.Column("secret_key_env", sa.String(length=120), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_checked_at", sa.DateTime(), nullable=True),
            sa.Column("last_check_status", sa.String(length=32), nullable=True),
            sa.Column("last_check_message", sa.String(length=500), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name", name="uq_external_backup_target_name"),
        )

    tables = _table_names()
    if "resource_limit_alerts" not in tables:
        op.create_table(
            "resource_limit_alerts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("resource_key", sa.String(length=32), nullable=False),
            sa.Column("threshold_label", sa.String(length=32), nullable=False),
            sa.Column("threshold_percent", sa.Integer(), nullable=True),
            sa.Column("usage_value", sa.Numeric(14, 2), nullable=True),
            sa.Column("limit_value", sa.Numeric(14, 2), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("message", sa.String(length=255), nullable=False),
            sa.Column("triggered_at", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("last_measured_at", sa.DateTime(), nullable=True),
            sa.Column("notification_channels_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "backup_verification_runs" not in tables:
        op.create_table(
            "backup_verification_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("backup_id", sa.Integer(), sa.ForeignKey("backups.id"), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("schedule_type", sa.String(length=16), nullable=False, server_default="daily"),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.Column("restore_duration_ms", sa.Integer(), nullable=True),
            sa.Column("validation_message", sa.String(length=500), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "api_idempotency_keys" not in tables:
        op.create_table(
            "api_idempotency_keys",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("api_token_id", sa.Integer(), sa.ForeignKey("api_tokens.id"), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("method", sa.String(length=16), nullable=False),
            sa.Column("path", sa.String(length=255), nullable=False),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("response_status", sa.Integer(), nullable=False),
            sa.Column("response_body_json", sa.JSON(), nullable=True),
            sa.Column("processed_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "api_token_id",
                "idempotency_key",
                "method",
                "path",
                name="uq_api_idempotency_token_key",
            ),
        )

    tables = _table_names()
    if "user_sessions" not in tables:
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("session_token_hash", sa.String(length=128), nullable=False),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=500), nullable=True),
            sa.Column("last_activity_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("session_token_hash", name="uq_user_session_token_hash"),
        )

    tables = _table_names()
    if "two_factor_backup_codes" not in tables:
        op.create_table(
            "two_factor_backup_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("code_hash", sa.String(length=128), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "operator_permissions" not in tables:
        op.create_table(
            "operator_permissions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("domain", sa.String(length=32), nullable=False),
            sa.Column("can_read", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("can_write", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "domain", name="uq_operator_permission_user_domain"),
        )

    tables = _table_names()
    if "status_events" not in tables:
        op.create_table(
            "status_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_type", sa.String(length=16), nullable=False),
            sa.Column("state", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("public_message", sa.Text(), nullable=False),
            sa.Column("internal_note", sa.Text(), nullable=True),
            sa.Column("affected_components_json", sa.JSON(), nullable=True),
            sa.Column("starts_at", sa.DateTime(), nullable=False),
            sa.Column("ends_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "migration_jobs" not in tables:
        op.create_table(
            "migration_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("source_provider", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
            sa.Column("current_step", sa.String(length=32), nullable=False, server_default="preflight"),
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payload_encrypted", sa.Text(), nullable=True),
            sa.Column("masked_summary", sa.String(length=255), nullable=True),
            sa.Column("last_error", sa.String(length=500), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "automation_rules" not in tables:
        op.create_table(
            "automation_rules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("trigger_event", sa.String(length=120), nullable=False),
            sa.Column("conditions_json", sa.JSON(), nullable=True),
            sa.Column("actions_json", sa.JSON(), nullable=False),
            sa.Column("stop_on_match", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name", name="uq_automation_rule_name"),
        )

    tables = _table_names()
    if "automation_executions" not in tables:
        op.create_table(
            "automation_executions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("rule_id", sa.Integer(), sa.ForeignKey("automation_rules.id"), nullable=False),
            sa.Column("trigger_event", sa.String(length=120), nullable=False),
            sa.Column("event_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("message", sa.String(length=500), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("executed_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("rule_id", "event_fingerprint", name="uq_automation_rule_fingerprint"),
        )

    tables = _table_names()
    if "client_resource_samples" in tables:
        columns = _column_names("client_resource_samples")
        with op.batch_alter_table("client_resource_samples") as batch_op:
            if "database_count" not in columns:
                batch_op.add_column(sa.Column("database_count", sa.Integer(), nullable=True))
            if "mailbox_count" not in columns:
                batch_op.add_column(sa.Column("mailbox_count", sa.Integer(), nullable=True))

    if "service_plans" in tables:
        columns = _column_names("service_plans")
        with op.batch_alter_table("service_plans") as batch_op:
            if "backup_frequency" not in columns:
                batch_op.add_column(sa.Column("backup_frequency", sa.String(length=16), nullable=False, server_default="daily"))
            if "backup_restore_points" not in columns:
                batch_op.add_column(sa.Column("backup_restore_points", sa.Integer(), nullable=False, server_default="7"))
            if "backup_retention_days" not in columns:
                batch_op.add_column(sa.Column("backup_retention_days", sa.Integer(), nullable=False, server_default="30"))
            if "backup_storage_target_id" not in columns:
                batch_op.add_column(sa.Column("backup_storage_target_id", sa.Integer(), sa.ForeignKey("external_backup_targets.id"), nullable=True))

    if "backups" in tables:
        columns = _column_names("backups")
        with op.batch_alter_table("backups") as batch_op:
            if "storage_target_id" not in columns:
                batch_op.add_column(sa.Column("storage_target_id", sa.Integer(), sa.ForeignKey("external_backup_targets.id"), nullable=True))
            if "external_location" not in columns:
                batch_op.add_column(sa.Column("external_location", sa.String(length=1024), nullable=True))
            if "retention_until" not in columns:
                batch_op.add_column(sa.Column("retention_until", sa.DateTime(), nullable=True))
            if "last_verified_at" not in columns:
                batch_op.add_column(sa.Column("last_verified_at", sa.DateTime(), nullable=True))
            if "last_verification_status" not in columns:
                batch_op.add_column(sa.Column("last_verification_status", sa.String(length=32), nullable=True))
            if "last_verification_message" not in columns:
                batch_op.add_column(sa.Column("last_verification_message", sa.String(length=500), nullable=True))

    if "api_tokens" in tables:
        columns = _column_names("api_tokens")
        with op.batch_alter_table("api_tokens") as batch_op:
            if "scopes_json" not in columns:
                batch_op.add_column(sa.Column("scopes_json", sa.JSON(), nullable=False, server_default="[]"))

    if "webhook_deliveries" in tables:
        columns = _column_names("webhook_deliveries")
        with op.batch_alter_table("webhook_deliveries") as batch_op:
            if "attempt_count" not in columns:
                batch_op.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
            if "max_attempts" not in columns:
                batch_op.add_column(sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"))
            if "next_retry_at" not in columns:
                batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(), nullable=True))
            if "dead_lettered" not in columns:
                batch_op.add_column(sa.Column("dead_lettered", sa.Boolean(), nullable=False, server_default=sa.false()))
            if "dead_lettered_at" not in columns:
                batch_op.add_column(sa.Column("dead_lettered_at", sa.DateTime(), nullable=True))
            if "dead_letter_reason" not in columns:
                batch_op.add_column(sa.Column("dead_letter_reason", sa.String(length=500), nullable=True))
            if "idempotency_key" not in columns:
                batch_op.add_column(sa.Column("idempotency_key", sa.String(length=191), nullable=True))
            if "destination_url" not in columns:
                batch_op.add_column(sa.Column("destination_url", sa.String(length=500), nullable=True))
            if "request_headers_json" not in columns:
                batch_op.add_column(sa.Column("request_headers_json", sa.JSON(), nullable=True))
            if "request_body_sha256" not in columns:
                batch_op.add_column(sa.Column("request_body_sha256", sa.String(length=64), nullable=True))

    tables = _table_names()
    if "resource_limit_alerts" in tables:
        indexes = _index_names("resource_limit_alerts")
        if "ix_resource_alert_client_state" not in indexes:
            op.create_index("ix_resource_alert_client_state", "resource_limit_alerts", ["client_id", "status"])

    if "webhook_deliveries" in tables:
        indexes = _index_names("webhook_deliveries")
        if "ix_webhook_delivery_retry" not in indexes:
            op.create_index("ix_webhook_delivery_retry", "webhook_deliveries", ["next_retry_at", "dead_lettered"])

    if "status_events" in tables:
        indexes = _index_names("status_events")
        if "ix_status_events_public_state" not in indexes:
            op.create_index("ix_status_events_public_state", "status_events", ["is_public", "state"])

    if "migration_jobs" in tables:
        indexes = _index_names("migration_jobs")
        if "ix_migration_jobs_client_status" not in indexes:
            op.create_index("ix_migration_jobs_client_status", "migration_jobs", ["client_id", "status"])

    if "automation_executions" in tables:
        indexes = _index_names("automation_executions")
        if "ix_automation_exec_rule_status" not in indexes:
            op.create_index("ix_automation_exec_rule_status", "automation_executions", ["rule_id", "status"])


def downgrade():
    tables = _table_names()

    if "webhook_deliveries" in tables:
        columns = _column_names("webhook_deliveries")
        with op.batch_alter_table("webhook_deliveries") as batch_op:
            if "request_body_sha256" in columns:
                batch_op.drop_column("request_body_sha256")
            if "request_headers_json" in columns:
                batch_op.drop_column("request_headers_json")
            if "destination_url" in columns:
                batch_op.drop_column("destination_url")
            if "idempotency_key" in columns:
                batch_op.drop_column("idempotency_key")
            if "dead_letter_reason" in columns:
                batch_op.drop_column("dead_letter_reason")
            if "dead_lettered_at" in columns:
                batch_op.drop_column("dead_lettered_at")
            if "dead_lettered" in columns:
                batch_op.drop_column("dead_lettered")
            if "next_retry_at" in columns:
                batch_op.drop_column("next_retry_at")
            if "max_attempts" in columns:
                batch_op.drop_column("max_attempts")
            if "attempt_count" in columns:
                batch_op.drop_column("attempt_count")

    if "api_tokens" in tables and "scopes_json" in _column_names("api_tokens"):
        with op.batch_alter_table("api_tokens") as batch_op:
            batch_op.drop_column("scopes_json")

    if "backups" in tables:
        columns = _column_names("backups")
        with op.batch_alter_table("backups") as batch_op:
            if "last_verification_message" in columns:
                batch_op.drop_column("last_verification_message")
            if "last_verification_status" in columns:
                batch_op.drop_column("last_verification_status")
            if "last_verified_at" in columns:
                batch_op.drop_column("last_verified_at")
            if "retention_until" in columns:
                batch_op.drop_column("retention_until")
            if "external_location" in columns:
                batch_op.drop_column("external_location")
            if "storage_target_id" in columns:
                batch_op.drop_column("storage_target_id")

    if "service_plans" in tables:
        columns = _column_names("service_plans")
        with op.batch_alter_table("service_plans") as batch_op:
            if "backup_storage_target_id" in columns:
                batch_op.drop_column("backup_storage_target_id")
            if "backup_retention_days" in columns:
                batch_op.drop_column("backup_retention_days")
            if "backup_restore_points" in columns:
                batch_op.drop_column("backup_restore_points")
            if "backup_frequency" in columns:
                batch_op.drop_column("backup_frequency")

    if "client_resource_samples" in tables:
        columns = _column_names("client_resource_samples")
        with op.batch_alter_table("client_resource_samples") as batch_op:
            if "mailbox_count" in columns:
                batch_op.drop_column("mailbox_count")
            if "database_count" in columns:
                batch_op.drop_column("database_count")

    tables = _table_names()
    if "automation_executions" in tables:
        op.drop_table("automation_executions")
    if "automation_rules" in tables:
        op.drop_table("automation_rules")
    if "migration_jobs" in tables:
        op.drop_table("migration_jobs")
    if "status_events" in tables:
        op.drop_table("status_events")
    if "operator_permissions" in tables:
        op.drop_table("operator_permissions")
    if "two_factor_backup_codes" in tables:
        op.drop_table("two_factor_backup_codes")
    if "user_sessions" in tables:
        op.drop_table("user_sessions")
    if "api_idempotency_keys" in tables:
        op.drop_table("api_idempotency_keys")
    if "backup_verification_runs" in tables:
        op.drop_table("backup_verification_runs")
    if "resource_limit_alerts" in tables:
        op.drop_table("resource_limit_alerts")
    if "external_backup_targets" in tables:
        op.drop_table("external_backup_targets")
