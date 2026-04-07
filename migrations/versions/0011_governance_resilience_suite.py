"""governance resilience suite

Revision ID: 0011_governance_resilience_suite
Revises: 0010_client_ssh_key_management
Create Date: 2026-04-08 13:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_governance_resilience_suite"
down_revision = "0010_client_ssh_key_management"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if table_name in _table_names() and name not in _index_names(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade():
    tables = _table_names()

    if "tenant_retention_policies" not in tables:
        op.create_table(
            "tenant_retention_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("resource_type", sa.String(length=64), nullable=False),
            sa.Column("anonymize_after_days", sa.Integer(), nullable=True),
            sa.Column("delete_after_days", sa.Integer(), nullable=True),
            sa.Column("legal_hold_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("notes", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("client_id", "resource_type", name="uq_retention_policy_client_resource"),
        )

    if "data_legal_holds" not in tables:
        op.create_table(
            "data_legal_holds",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("resource_type", sa.String(length=64), nullable=False),
            sa.Column("resource_id", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("reason", sa.String(length=255), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("starts_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("released_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "retention_cleanup_runs" not in tables:
        op.create_table(
            "retention_cleanup_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_key", sa.String(length=120), nullable=True, unique=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("summary_json", sa.JSON(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("triggered_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "vault_secrets" not in tables:
        op.create_table(
            "vault_secrets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("secret_type", sa.String(length=64), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rotation_interval_days", sa.Integer(), nullable=True),
            sa.Column("last_rotated_at", sa.DateTime(), nullable=True),
            sa.Column("next_rotation_due_at", sa.DateTime(), nullable=True),
            sa.Column("last_revealed_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("client_id", "name", name="uq_vault_secret_client_name"),
        )

    if "vault_secret_versions" not in tables:
        op.create_table(
            "vault_secret_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("secret_id", sa.Integer(), sa.ForeignKey("vault_secrets.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("value_encrypted", sa.Text(), nullable=False),
            sa.Column("value_fingerprint", sa.String(length=64), nullable=True),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("rotated_reason", sa.String(length=255), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("secret_id", "version", name="uq_vault_secret_version"),
        )

    if "event_stream_entries" not in tables:
        op.create_table(
            "event_stream_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
            sa.Column("source", sa.String(length=64), nullable=True),
            sa.Column("message", sa.String(length=255), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("event_fingerprint", sa.String(length=64), nullable=True),
            sa.Column("event_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "compliance_runs" not in tables:
        op.create_table(
            "compliance_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("score", sa.Integer(), nullable=True),
            sa.Column("summary_json", sa.JSON(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("triggered_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "compliance_results" not in tables:
        op.create_table(
            "compliance_results",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("compliance_runs.id"), nullable=False),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("check_code", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="medium"),
            sa.Column("score", sa.Integer(), nullable=True),
            sa.Column("message", sa.String(length=255), nullable=False),
            sa.Column("details_json", sa.JSON(), nullable=True),
            sa.Column("evidence_ref", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "compliance_checklist_items" not in tables:
        op.create_table(
            "compliance_checklist_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("control_code", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="not_started"),
            sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("client_id", "control_code", name="uq_compliance_control_client_code"),
        )

    if "compliance_evidence_links" not in tables:
        op.create_table(
            "compliance_evidence_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("checklist_item_id", sa.Integer(), sa.ForeignKey("compliance_checklist_items.id"), nullable=False),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("evidence_type", sa.String(length=32), nullable=False),
            sa.Column("reference_id", sa.String(length=120), nullable=False),
            sa.Column("reference_label", sa.String(length=255), nullable=True),
            sa.Column("linked_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("checklist_item_id", "evidence_type", "reference_id", name="uq_compliance_evidence_link"),
        )

    if "policy_documents" not in tables:
        op.create_table(
            "policy_documents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("scope", sa.String(length=32), nullable=False, server_default="global"),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("version", sa.String(length=32), nullable=False, server_default="v1"),
            sa.Column("enforcement_mode", sa.String(length=16), nullable=False, server_default="advisory"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("definition_json", sa.JSON(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "policy_evaluations" not in tables:
        op.create_table(
            "policy_evaluations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("policy_id", sa.Integer(), sa.ForeignKey("policy_documents.id"), nullable=False),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("target_type", sa.String(length=64), nullable=True),
            sa.Column("target_id", sa.String(length=120), nullable=True),
            sa.Column("decision", sa.String(length=16), nullable=False),
            sa.Column("message", sa.String(length=255), nullable=True),
            sa.Column("input_json", sa.JSON(), nullable=True),
            sa.Column("evaluated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "client_onboarding_states" not in tables:
        op.create_table(
            "client_onboarding_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False, unique=True),
            sa.Column("completed_steps_json", sa.JSON(), nullable=False),
            sa.Column("skipped_steps_json", sa.JSON(), nullable=False),
            sa.Column("last_step", sa.String(length=64), nullable=True),
            sa.Column("completion_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "disaster_recovery_profiles" not in tables:
        op.create_table(
            "disaster_recovery_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False, unique=True),
            sa.Column("primary_region", sa.String(length=64), nullable=True),
            sa.Column("secondary_region", sa.String(length=64), nullable=True),
            sa.Column("rpo_target_minutes", sa.Integer(), nullable=False, server_default="1440"),
            sa.Column("rto_target_minutes", sa.Integer(), nullable=False, server_default="240"),
            sa.Column("failover_last_tested_at", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "disaster_recovery_check_runs" not in tables:
        op.create_table(
            "disaster_recovery_check_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("score", sa.Integer(), nullable=True),
            sa.Column("rpo_minutes", sa.Integer(), nullable=True),
            sa.Column("rto_minutes", sa.Integer(), nullable=True),
            sa.Column("message", sa.String(length=255), nullable=True),
            sa.Column("details_json", sa.JSON(), nullable=True),
            sa.Column("checked_at", sa.DateTime(), nullable=False),
            sa.Column("checked_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    _create_index_if_missing("ix_tenant_retention_policies_client_id", "tenant_retention_policies", ["client_id"])
    _create_index_if_missing("ix_tenant_retention_policies_resource_type", "tenant_retention_policies", ["resource_type"])
    _create_index_if_missing("ix_tenant_retention_policies_is_active", "tenant_retention_policies", ["is_active"])
    _create_index_if_missing("ix_retention_policy_client_resource", "tenant_retention_policies", ["client_id", "resource_type"])

    _create_index_if_missing("ix_data_legal_holds_client_id", "data_legal_holds", ["client_id"])
    _create_index_if_missing("ix_data_legal_holds_resource_type", "data_legal_holds", ["resource_type"])
    _create_index_if_missing("ix_data_legal_holds_resource_id", "data_legal_holds", ["resource_id"])
    _create_index_if_missing("ix_data_legal_holds_status", "data_legal_holds", ["status"])
    _create_index_if_missing("ix_data_legal_holds_created_by_user_id", "data_legal_holds", ["created_by_user_id"])
    _create_index_if_missing("ix_data_legal_holds_starts_at", "data_legal_holds", ["starts_at"])
    _create_index_if_missing("ix_data_legal_holds_expires_at", "data_legal_holds", ["expires_at"])
    _create_index_if_missing("ix_data_legal_holds_released_at", "data_legal_holds", ["released_at"])
    _create_index_if_missing("ix_legal_holds_scope_status", "data_legal_holds", ["client_id", "resource_type", "status"])

    _create_index_if_missing("ix_retention_cleanup_runs_run_key", "retention_cleanup_runs", ["run_key"], unique=True)
    _create_index_if_missing("ix_retention_cleanup_runs_status", "retention_cleanup_runs", ["status"])
    _create_index_if_missing("ix_retention_cleanup_runs_triggered_by_user_id", "retention_cleanup_runs", ["triggered_by_user_id"])
    _create_index_if_missing("ix_retention_runs_status_started", "retention_cleanup_runs", ["status", "started_at"])

    _create_index_if_missing("ix_vault_secrets_client_id", "vault_secrets", ["client_id"])
    _create_index_if_missing("ix_vault_secrets_name", "vault_secrets", ["name"])
    _create_index_if_missing("ix_vault_secrets_secret_type", "vault_secrets", ["secret_type"])
    _create_index_if_missing("ix_vault_secrets_status", "vault_secrets", ["status"])
    _create_index_if_missing("ix_vault_secrets_last_rotated_at", "vault_secrets", ["last_rotated_at"])
    _create_index_if_missing("ix_vault_secrets_next_rotation_due_at", "vault_secrets", ["next_rotation_due_at"])
    _create_index_if_missing("ix_vault_secrets_created_by_user_id", "vault_secrets", ["created_by_user_id"])
    _create_index_if_missing("ix_vault_secrets_updated_by_user_id", "vault_secrets", ["updated_by_user_id"])
    _create_index_if_missing("ix_vault_secret_rotation_due", "vault_secrets", ["next_rotation_due_at", "status"])

    _create_index_if_missing("ix_vault_secret_versions_secret_id", "vault_secret_versions", ["secret_id"])
    _create_index_if_missing("ix_vault_secret_versions_value_fingerprint", "vault_secret_versions", ["value_fingerprint"])
    _create_index_if_missing("ix_vault_secret_versions_is_current", "vault_secret_versions", ["is_current"])
    _create_index_if_missing("ix_vault_secret_versions_expires_at", "vault_secret_versions", ["expires_at"])
    _create_index_if_missing("ix_vault_secret_versions_created_by_user_id", "vault_secret_versions", ["created_by_user_id"])
    _create_index_if_missing("ix_vault_secret_version_current", "vault_secret_versions", ["secret_id", "is_current"])

    _create_index_if_missing("ix_event_stream_entries_client_id", "event_stream_entries", ["client_id"])
    _create_index_if_missing("ix_event_stream_entries_actor_user_id", "event_stream_entries", ["actor_user_id"])
    _create_index_if_missing("ix_event_stream_entries_event_type", "event_stream_entries", ["event_type"])
    _create_index_if_missing("ix_event_stream_entries_category", "event_stream_entries", ["category"])
    _create_index_if_missing("ix_event_stream_entries_severity", "event_stream_entries", ["severity"])
    _create_index_if_missing("ix_event_stream_entries_source", "event_stream_entries", ["source"])
    _create_index_if_missing("ix_event_stream_entries_event_fingerprint", "event_stream_entries", ["event_fingerprint"])
    _create_index_if_missing("ix_event_stream_entries_event_at", "event_stream_entries", ["event_at"])
    _create_index_if_missing("ix_event_stream_category_time", "event_stream_entries", ["category", "event_at"])
    _create_index_if_missing("ix_event_stream_client_time", "event_stream_entries", ["client_id", "event_at"])

    _create_index_if_missing("ix_compliance_runs_client_id", "compliance_runs", ["client_id"])
    _create_index_if_missing("ix_compliance_runs_status", "compliance_runs", ["status"])
    _create_index_if_missing("ix_compliance_runs_triggered_by_user_id", "compliance_runs", ["triggered_by_user_id"])

    _create_index_if_missing("ix_compliance_results_run_id", "compliance_results", ["run_id"])
    _create_index_if_missing("ix_compliance_results_client_id", "compliance_results", ["client_id"])
    _create_index_if_missing("ix_compliance_results_check_code", "compliance_results", ["check_code"])
    _create_index_if_missing("ix_compliance_results_status", "compliance_results", ["status"])
    _create_index_if_missing("ix_compliance_result_run_status", "compliance_results", ["run_id", "status"])

    _create_index_if_missing("ix_compliance_checklist_items_client_id", "compliance_checklist_items", ["client_id"])
    _create_index_if_missing("ix_compliance_checklist_items_control_code", "compliance_checklist_items", ["control_code"])
    _create_index_if_missing("ix_compliance_checklist_items_status", "compliance_checklist_items", ["status"])
    _create_index_if_missing("ix_compliance_checklist_items_owner_user_id", "compliance_checklist_items", ["owner_user_id"])
    _create_index_if_missing("ix_compliance_checklist_items_due_date", "compliance_checklist_items", ["due_date"])
    _create_index_if_missing("ix_compliance_checklist_items_created_by_user_id", "compliance_checklist_items", ["created_by_user_id"])
    _create_index_if_missing("ix_compliance_checklist_items_updated_by_user_id", "compliance_checklist_items", ["updated_by_user_id"])
    _create_index_if_missing("ix_compliance_control_client_status", "compliance_checklist_items", ["client_id", "status"])

    _create_index_if_missing("ix_compliance_evidence_links_checklist_item_id", "compliance_evidence_links", ["checklist_item_id"])
    _create_index_if_missing("ix_compliance_evidence_links_client_id", "compliance_evidence_links", ["client_id"])
    _create_index_if_missing("ix_compliance_evidence_links_evidence_type", "compliance_evidence_links", ["evidence_type"])
    _create_index_if_missing("ix_compliance_evidence_links_reference_id", "compliance_evidence_links", ["reference_id"])
    _create_index_if_missing("ix_compliance_evidence_links_linked_by_user_id", "compliance_evidence_links", ["linked_by_user_id"])
    _create_index_if_missing("ix_compliance_evidence_type_ref", "compliance_evidence_links", ["evidence_type", "reference_id"])

    _create_index_if_missing("ix_policy_documents_name", "policy_documents", ["name"])
    _create_index_if_missing("ix_policy_documents_scope", "policy_documents", ["scope"])
    _create_index_if_missing("ix_policy_documents_client_id", "policy_documents", ["client_id"])
    _create_index_if_missing("ix_policy_documents_enforcement_mode", "policy_documents", ["enforcement_mode"])
    _create_index_if_missing("ix_policy_documents_is_active", "policy_documents", ["is_active"])
    _create_index_if_missing("ix_policy_documents_created_by_user_id", "policy_documents", ["created_by_user_id"])
    _create_index_if_missing("ix_policy_documents_updated_by_user_id", "policy_documents", ["updated_by_user_id"])
    _create_index_if_missing("ix_policy_doc_scope_active", "policy_documents", ["scope", "is_active"])

    _create_index_if_missing("ix_policy_evaluations_policy_id", "policy_evaluations", ["policy_id"])
    _create_index_if_missing("ix_policy_evaluations_client_id", "policy_evaluations", ["client_id"])
    _create_index_if_missing("ix_policy_evaluations_event_type", "policy_evaluations", ["event_type"])
    _create_index_if_missing("ix_policy_evaluations_target_type", "policy_evaluations", ["target_type"])
    _create_index_if_missing("ix_policy_evaluations_target_id", "policy_evaluations", ["target_id"])
    _create_index_if_missing("ix_policy_evaluations_decision", "policy_evaluations", ["decision"])
    _create_index_if_missing("ix_policy_evaluations_evaluated_by_user_id", "policy_evaluations", ["evaluated_by_user_id"])
    _create_index_if_missing("ix_policy_eval_event_decision", "policy_evaluations", ["event_type", "decision"])

    _create_index_if_missing("ix_client_onboarding_states_client_id", "client_onboarding_states", ["client_id"], unique=True)
    _create_index_if_missing("ix_client_onboarding_states_completed_at", "client_onboarding_states", ["completed_at"])
    _create_index_if_missing("ix_client_onboarding_states_updated_by_user_id", "client_onboarding_states", ["updated_by_user_id"])
    _create_index_if_missing("ix_onboarding_client_percent", "client_onboarding_states", ["client_id", "completion_percent"])

    _create_index_if_missing("ix_disaster_recovery_profiles_client_id", "disaster_recovery_profiles", ["client_id"], unique=True)

    _create_index_if_missing("ix_disaster_recovery_check_runs_client_id", "disaster_recovery_check_runs", ["client_id"])
    _create_index_if_missing("ix_disaster_recovery_check_runs_status", "disaster_recovery_check_runs", ["status"])
    _create_index_if_missing("ix_disaster_recovery_check_runs_checked_at", "disaster_recovery_check_runs", ["checked_at"])
    _create_index_if_missing("ix_disaster_recovery_check_runs_checked_by_user_id", "disaster_recovery_check_runs", ["checked_by_user_id"])
    _create_index_if_missing("ix_dr_checks_client_time", "disaster_recovery_check_runs", ["client_id", "checked_at"])


def downgrade():
    for table_name, index_names in [
        (
            "disaster_recovery_check_runs",
            [
                "ix_dr_checks_client_time",
                "ix_disaster_recovery_check_runs_checked_by_user_id",
                "ix_disaster_recovery_check_runs_checked_at",
                "ix_disaster_recovery_check_runs_status",
                "ix_disaster_recovery_check_runs_client_id",
            ],
        ),
        ("disaster_recovery_profiles", ["ix_disaster_recovery_profiles_client_id"]),
        (
            "client_onboarding_states",
            [
                "ix_onboarding_client_percent",
                "ix_client_onboarding_states_updated_by_user_id",
                "ix_client_onboarding_states_completed_at",
                "ix_client_onboarding_states_client_id",
            ],
        ),
        (
            "policy_evaluations",
            [
                "ix_policy_eval_event_decision",
                "ix_policy_evaluations_evaluated_by_user_id",
                "ix_policy_evaluations_decision",
                "ix_policy_evaluations_target_id",
                "ix_policy_evaluations_target_type",
                "ix_policy_evaluations_event_type",
                "ix_policy_evaluations_client_id",
                "ix_policy_evaluations_policy_id",
            ],
        ),
        (
            "policy_documents",
            [
                "ix_policy_doc_scope_active",
                "ix_policy_documents_updated_by_user_id",
                "ix_policy_documents_created_by_user_id",
                "ix_policy_documents_is_active",
                "ix_policy_documents_enforcement_mode",
                "ix_policy_documents_client_id",
                "ix_policy_documents_scope",
                "ix_policy_documents_name",
            ],
        ),
        (
            "compliance_evidence_links",
            [
                "ix_compliance_evidence_type_ref",
                "ix_compliance_evidence_links_linked_by_user_id",
                "ix_compliance_evidence_links_reference_id",
                "ix_compliance_evidence_links_evidence_type",
                "ix_compliance_evidence_links_client_id",
                "ix_compliance_evidence_links_checklist_item_id",
            ],
        ),
        (
            "compliance_checklist_items",
            [
                "ix_compliance_control_client_status",
                "ix_compliance_checklist_items_updated_by_user_id",
                "ix_compliance_checklist_items_created_by_user_id",
                "ix_compliance_checklist_items_due_date",
                "ix_compliance_checklist_items_owner_user_id",
                "ix_compliance_checklist_items_status",
                "ix_compliance_checklist_items_control_code",
                "ix_compliance_checklist_items_client_id",
            ],
        ),
        (
            "compliance_results",
            [
                "ix_compliance_result_run_status",
                "ix_compliance_results_status",
                "ix_compliance_results_check_code",
                "ix_compliance_results_client_id",
                "ix_compliance_results_run_id",
            ],
        ),
        (
            "compliance_runs",
            [
                "ix_compliance_runs_triggered_by_user_id",
                "ix_compliance_runs_status",
                "ix_compliance_runs_client_id",
            ],
        ),
        (
            "event_stream_entries",
            [
                "ix_event_stream_client_time",
                "ix_event_stream_category_time",
                "ix_event_stream_entries_event_at",
                "ix_event_stream_entries_event_fingerprint",
                "ix_event_stream_entries_source",
                "ix_event_stream_entries_severity",
                "ix_event_stream_entries_category",
                "ix_event_stream_entries_event_type",
                "ix_event_stream_entries_actor_user_id",
                "ix_event_stream_entries_client_id",
            ],
        ),
        (
            "vault_secret_versions",
            [
                "ix_vault_secret_version_current",
                "ix_vault_secret_versions_created_by_user_id",
                "ix_vault_secret_versions_expires_at",
                "ix_vault_secret_versions_is_current",
                "ix_vault_secret_versions_value_fingerprint",
                "ix_vault_secret_versions_secret_id",
            ],
        ),
        (
            "vault_secrets",
            [
                "ix_vault_secret_rotation_due",
                "ix_vault_secrets_updated_by_user_id",
                "ix_vault_secrets_created_by_user_id",
                "ix_vault_secrets_next_rotation_due_at",
                "ix_vault_secrets_last_rotated_at",
                "ix_vault_secrets_status",
                "ix_vault_secrets_secret_type",
                "ix_vault_secrets_name",
                "ix_vault_secrets_client_id",
            ],
        ),
        (
            "retention_cleanup_runs",
            [
                "ix_retention_runs_status_started",
                "ix_retention_cleanup_runs_triggered_by_user_id",
                "ix_retention_cleanup_runs_status",
                "ix_retention_cleanup_runs_run_key",
            ],
        ),
        (
            "data_legal_holds",
            [
                "ix_legal_holds_scope_status",
                "ix_data_legal_holds_released_at",
                "ix_data_legal_holds_expires_at",
                "ix_data_legal_holds_starts_at",
                "ix_data_legal_holds_created_by_user_id",
                "ix_data_legal_holds_status",
                "ix_data_legal_holds_resource_id",
                "ix_data_legal_holds_resource_type",
                "ix_data_legal_holds_client_id",
            ],
        ),
        (
            "tenant_retention_policies",
            [
                "ix_retention_policy_client_resource",
                "ix_tenant_retention_policies_is_active",
                "ix_tenant_retention_policies_resource_type",
                "ix_tenant_retention_policies_client_id",
            ],
        ),
    ]:
        if table_name in _table_names():
            existing_indexes = _index_names(table_name)
            for index_name in index_names:
                if index_name in existing_indexes:
                    op.drop_index(index_name, table_name=table_name)

    for table_name in [
        "disaster_recovery_check_runs",
        "disaster_recovery_profiles",
        "client_onboarding_states",
        "policy_evaluations",
        "policy_documents",
        "compliance_evidence_links",
        "compliance_checklist_items",
        "compliance_results",
        "compliance_runs",
        "event_stream_entries",
        "vault_secret_versions",
        "vault_secrets",
        "retention_cleanup_runs",
        "data_legal_holds",
        "tenant_retention_policies",
    ]:
        if table_name in _table_names():
            op.drop_table(table_name)
