"""ticket macros, bulk operations and export history

Revision ID: 0007_ticket_bulk_export_suite
Revises: 0006_operations_and_security_suite
Create Date: 2026-04-07 23:58:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_ticket_bulk_export_suite"
down_revision = "0006_operations_and_security_suite"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    tables = _table_names()

    if "ticket_macros" not in tables:
        op.create_table(
            "ticket_macros",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("visibility_scope", sa.String(length=32), nullable=False, server_default="all_staff"),
            sa.Column("subject_template", sa.String(length=200), nullable=True),
            sa.Column("body_template", sa.Text(), nullable=False),
            sa.Column("placeholders_json", sa.JSON(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "ticket_macro_usages" not in tables:
        op.create_table(
            "ticket_macro_usages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("ticket_message_id", sa.Integer(), sa.ForeignKey("ticket_messages.id"), nullable=True),
            sa.Column("macro_id", sa.Integer(), sa.ForeignKey("ticket_macros.id"), nullable=False),
            sa.Column("used_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("rendered_body", sa.Text(), nullable=True),
            sa.Column("render_error", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "bulk_operations" not in tables:
        op.create_table(
            "bulk_operations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("operation_type", sa.String(length=64), nullable=False),
            sa.Column("target_type", sa.String(length=32), nullable=False),
            sa.Column("initiated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
            sa.Column("requested_filters_json", sa.JSON(), nullable=True),
            sa.Column("result_summary_json", sa.JSON(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "bulk_operation_items" not in tables:
        op.create_table(
            "bulk_operation_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("bulk_operation_id", sa.Integer(), sa.ForeignKey("bulk_operations.id"), nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("entity_id", sa.String(length=120), nullable=False),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("message", sa.String(length=500), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    tables = _table_names()
    if "export_jobs" not in tables:
        op.create_table(
            "export_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("dataset", sa.String(length=32), nullable=False),
            sa.Column("format", sa.String(length=16), nullable=False),
            sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("filters_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
            sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.String(length=500), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "ticket_macros" in _table_names():
        indexes = _index_names("ticket_macros")
        if "ix_ticket_macros_name" not in indexes:
            op.create_index("ix_ticket_macros_name", "ticket_macros", ["name"], unique=False)
        if "ix_ticket_macros_category" not in indexes:
            op.create_index("ix_ticket_macros_category", "ticket_macros", ["category"], unique=False)
        if "ix_ticket_macros_visibility_scope" not in indexes:
            op.create_index("ix_ticket_macros_visibility_scope", "ticket_macros", ["visibility_scope"], unique=False)
        if "ix_ticket_macros_is_active" not in indexes:
            op.create_index("ix_ticket_macros_is_active", "ticket_macros", ["is_active"], unique=False)
        if "ix_ticket_macros_created_by_user_id" not in indexes:
            op.create_index("ix_ticket_macros_created_by_user_id", "ticket_macros", ["created_by_user_id"], unique=False)
        if "ix_ticket_macros_updated_by_user_id" not in indexes:
            op.create_index("ix_ticket_macros_updated_by_user_id", "ticket_macros", ["updated_by_user_id"], unique=False)
        if "ix_ticket_macros_category_active" not in indexes:
            op.create_index("ix_ticket_macros_category_active", "ticket_macros", ["category", "is_active"], unique=False)

    if "ticket_macro_usages" in _table_names():
        indexes = _index_names("ticket_macro_usages")
        if "ix_ticket_macro_usages_ticket_id" not in indexes:
            op.create_index("ix_ticket_macro_usages_ticket_id", "ticket_macro_usages", ["ticket_id"], unique=False)
        if "ix_ticket_macro_usages_ticket_message_id" not in indexes:
            op.create_index("ix_ticket_macro_usages_ticket_message_id", "ticket_macro_usages", ["ticket_message_id"], unique=False)
        if "ix_ticket_macro_usages_macro_id" not in indexes:
            op.create_index("ix_ticket_macro_usages_macro_id", "ticket_macro_usages", ["macro_id"], unique=False)
        if "ix_ticket_macro_usages_used_by_user_id" not in indexes:
            op.create_index("ix_ticket_macro_usages_used_by_user_id", "ticket_macro_usages", ["used_by_user_id"], unique=False)
        if "ix_ticket_macro_usages_ticket_created" not in indexes:
            op.create_index("ix_ticket_macro_usages_ticket_created", "ticket_macro_usages", ["ticket_id", "created_at"], unique=False)

    if "bulk_operations" in _table_names():
        indexes = _index_names("bulk_operations")
        if "ix_bulk_operations_operation_type" not in indexes:
            op.create_index("ix_bulk_operations_operation_type", "bulk_operations", ["operation_type"], unique=False)
        if "ix_bulk_operations_target_type" not in indexes:
            op.create_index("ix_bulk_operations_target_type", "bulk_operations", ["target_type"], unique=False)
        if "ix_bulk_operations_initiated_by_user_id" not in indexes:
            op.create_index("ix_bulk_operations_initiated_by_user_id", "bulk_operations", ["initiated_by_user_id"], unique=False)
        if "ix_bulk_operations_status" not in indexes:
            op.create_index("ix_bulk_operations_status", "bulk_operations", ["status"], unique=False)
        if "ix_bulk_operations_type_status" not in indexes:
            op.create_index("ix_bulk_operations_type_status", "bulk_operations", ["operation_type", "status"], unique=False)

    if "bulk_operation_items" in _table_names():
        indexes = _index_names("bulk_operation_items")
        if "ix_bulk_operation_items_bulk_operation_id" not in indexes:
            op.create_index("ix_bulk_operation_items_bulk_operation_id", "bulk_operation_items", ["bulk_operation_id"], unique=False)
        if "ix_bulk_operation_items_success" not in indexes:
            op.create_index("ix_bulk_operation_items_success", "bulk_operation_items", ["success"], unique=False)

    if "export_jobs" in _table_names():
        indexes = _index_names("export_jobs")
        if "ix_export_jobs_dataset" not in indexes:
            op.create_index("ix_export_jobs_dataset", "export_jobs", ["dataset"], unique=False)
        if "ix_export_jobs_format" not in indexes:
            op.create_index("ix_export_jobs_format", "export_jobs", ["format"], unique=False)
        if "ix_export_jobs_requested_by_user_id" not in indexes:
            op.create_index("ix_export_jobs_requested_by_user_id", "export_jobs", ["requested_by_user_id"], unique=False)
        if "ix_export_jobs_status" not in indexes:
            op.create_index("ix_export_jobs_status", "export_jobs", ["status"], unique=False)
        if "ix_export_jobs_dataset_created" not in indexes:
            op.create_index("ix_export_jobs_dataset_created", "export_jobs", ["dataset", "created_at"], unique=False)


def downgrade():
    tables = _table_names()

    if "export_jobs" in tables:
        indexes = _index_names("export_jobs")
        for index_name in [
            "ix_export_jobs_dataset_created",
            "ix_export_jobs_status",
            "ix_export_jobs_requested_by_user_id",
            "ix_export_jobs_format",
            "ix_export_jobs_dataset",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="export_jobs")
        op.drop_table("export_jobs")

    tables = _table_names()
    if "bulk_operation_items" in tables:
        indexes = _index_names("bulk_operation_items")
        for index_name in [
            "ix_bulk_operation_items_success",
            "ix_bulk_operation_items_bulk_operation_id",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="bulk_operation_items")
        op.drop_table("bulk_operation_items")

    tables = _table_names()
    if "bulk_operations" in tables:
        indexes = _index_names("bulk_operations")
        for index_name in [
            "ix_bulk_operations_type_status",
            "ix_bulk_operations_status",
            "ix_bulk_operations_initiated_by_user_id",
            "ix_bulk_operations_target_type",
            "ix_bulk_operations_operation_type",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="bulk_operations")
        op.drop_table("bulk_operations")

    tables = _table_names()
    if "ticket_macro_usages" in tables:
        indexes = _index_names("ticket_macro_usages")
        for index_name in [
            "ix_ticket_macro_usages_ticket_created",
            "ix_ticket_macro_usages_used_by_user_id",
            "ix_ticket_macro_usages_macro_id",
            "ix_ticket_macro_usages_ticket_message_id",
            "ix_ticket_macro_usages_ticket_id",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="ticket_macro_usages")
        op.drop_table("ticket_macro_usages")

    tables = _table_names()
    if "ticket_macros" in tables:
        indexes = _index_names("ticket_macros")
        for index_name in [
            "ix_ticket_macros_category_active",
            "ix_ticket_macros_updated_by_user_id",
            "ix_ticket_macros_created_by_user_id",
            "ix_ticket_macros_is_active",
            "ix_ticket_macros_visibility_scope",
            "ix_ticket_macros_category",
            "ix_ticket_macros_name",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="ticket_macros")
        op.drop_table("ticket_macros")
