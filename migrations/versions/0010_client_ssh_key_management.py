"""client ssh key management

Revision ID: 0010_client_ssh_key_management
Revises: 0009_approval_and_audit_chain
Create Date: 2026-04-08 11:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0010_client_ssh_key_management"
down_revision = "0009_approval_and_audit_chain"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    if "client_ssh_keys" not in _table_names():
        op.create_table(
            "client_ssh_keys",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column("key_type", sa.String(length=32), nullable=False),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("fingerprint_sha256", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("last_installed_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("client_id", "fingerprint_sha256", name="uq_client_ssh_key_client_fingerprint"),
        )

    if "client_ssh_keys" in _table_names():
        indexes = _index_names("client_ssh_keys")
        if "ix_client_ssh_keys_client_id" not in indexes:
            op.create_index("ix_client_ssh_keys_client_id", "client_ssh_keys", ["client_id"], unique=False)
        if "ix_client_ssh_keys_created_by_user_id" not in indexes:
            op.create_index(
                "ix_client_ssh_keys_created_by_user_id",
                "client_ssh_keys",
                ["created_by_user_id"],
                unique=False,
            )
        if "ix_client_ssh_keys_key_type" not in indexes:
            op.create_index("ix_client_ssh_keys_key_type", "client_ssh_keys", ["key_type"], unique=False)
        if "ix_client_ssh_keys_fingerprint_sha256" not in indexes:
            op.create_index(
                "ix_client_ssh_keys_fingerprint_sha256",
                "client_ssh_keys",
                ["fingerprint_sha256"],
                unique=False,
            )
        if "ix_client_ssh_keys_status" not in indexes:
            op.create_index("ix_client_ssh_keys_status", "client_ssh_keys", ["status"], unique=False)
        if "ix_client_ssh_keys_client_status" not in indexes:
            op.create_index(
                "ix_client_ssh_keys_client_status",
                "client_ssh_keys",
                ["client_id", "status"],
                unique=False,
            )


def downgrade():
    if "client_ssh_keys" in _table_names():
        indexes = _index_names("client_ssh_keys")
        for index_name in [
            "ix_client_ssh_keys_client_status",
            "ix_client_ssh_keys_status",
            "ix_client_ssh_keys_fingerprint_sha256",
            "ix_client_ssh_keys_key_type",
            "ix_client_ssh_keys_created_by_user_id",
            "ix_client_ssh_keys_client_id",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="client_ssh_keys")
        op.drop_table("client_ssh_keys")
