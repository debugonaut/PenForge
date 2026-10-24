"""Add durable account working state and Writing Desk documents.

Mutable draft state only. Approved geometry, snapshots and exports stay in their
existing immutable tables; these rows reference them, never replace them.
"""

from alembic import op

revision = "0013_account_workspace"
down_revision = "0012_devanagari"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE project_workspaces (
      project_id uuid PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
      owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      workspace jsonb NOT NULL,
      row_version integer NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_project_workspaces_version CHECK (row_version > 0),
      CONSTRAINT ck_project_workspaces_size CHECK (pg_column_size(workspace) <= 1048576)
    );
    CREATE INDEX project_workspaces_owner_idx ON project_workspaces(owner_user_id);

    CREATE TABLE writing_documents (
      id uuid PRIMARY KEY,
      owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title text NOT NULL,
      format text NOT NULL,
      document jsonb NOT NULL,
      row_version integer NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_writing_documents_title CHECK (length(title) BETWEEN 1 AND 120),
      CONSTRAINT ck_writing_documents_format CHECK (format IN ('letter','note','card')),
      CONSTRAINT ck_writing_documents_version CHECK (row_version > 0),
      CONSTRAINT ck_writing_documents_size CHECK (pg_column_size(document) <= 262144)
    );
    CREATE INDEX writing_documents_owner_cursor_idx
      ON writing_documents(owner_user_id, updated_at DESC, id DESC);
    """)


def downgrade():
    # Draft rows are mutable working state, not lineage. Refuse only if an export
    # reference would be lost silently; otherwise remove the additive tables.
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (
        SELECT 1 FROM project_workspaces
        WHERE workspace->'exports' IS NOT NULL
          AND jsonb_array_length(COALESCE(workspace->'exports', '[]'::jsonb)) > 0
      ) THEN
        RAISE EXCEPTION 'workspace_export_references_require_forward_repair';
      END IF;
    END $$;
    DROP TABLE writing_documents;
    DROP TABLE project_workspaces;
    """)
