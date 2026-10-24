"""Add the stable owner project keyset-pagination index.

Revision ID: 0003_project_cursor
Revises: 0002_identity
"""

from alembic import op

revision = "0003_project_cursor"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "projects_owner_cursor_idx",
        "projects",
        ["owner_user_id", "updated_at", "id"],
        unique=False,
        postgresql_ops={"updated_at": "DESC", "id": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("projects_owner_cursor_idx", table_name="projects")
