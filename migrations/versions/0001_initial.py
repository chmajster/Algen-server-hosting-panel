"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-06 14:30:00
"""

from alembic import op


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from panel.extensions import db
    import panel.models  # noqa: F401

    bind = op.get_bind()
    db.metadata.create_all(bind=bind)


def downgrade():
    from panel.extensions import db
    import panel.models  # noqa: F401

    bind = op.get_bind()
    db.metadata.drop_all(bind=bind)
