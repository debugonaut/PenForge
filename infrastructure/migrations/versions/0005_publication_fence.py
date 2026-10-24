"""Require a live verification lease at transaction commit.

Revision ID: 0005_publication_fence
Revises: 0004_capture_uploads

Additive trigger only. Existing completed jobs are untouched; in-flight workers
must satisfy the same current owner/attempt/holder policy when they publish.
"""

from alembic import op

revision = "0005_publication_fence"
down_revision = "0004_capture_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION penforge_publication_fence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE current_job record;
    BEGIN
      IF OLD.state='running' AND NEW.state='succeeded' AND NEW.job_type='asset_verify' THEN
        SELECT * INTO current_job FROM jobs WHERE id=NEW.id;
        IF OLD.lease_holder IS NULL OR OLD.lease_expires_at IS NULL
          OR OLD.lease_expires_at <= clock_timestamp()
          OR NEW.attempts IS DISTINCT FROM OLD.attempts
          OR current_job.attempts IS DISTINCT FROM OLD.attempts
          OR current_job.state <> 'succeeded'
          OR current_job.owner_user_id IS DISTINCT FROM OLD.owner_user_id
          OR NOT EXISTS (SELECT 1 FROM users WHERE id=current_job.owner_user_id
              AND account_state='active' AND merged_into_user_id IS NULL)
          OR NOT EXISTS (SELECT 1 FROM projects WHERE id=current_job.project_id
              AND owner_user_id=current_job.owner_user_id AND status<>'deleting')
          OR NOT EXISTS (SELECT 1 FROM assets WHERE verify_job_id=current_job.id
              AND owner_user_id=current_job.owner_user_id AND project_id=current_job.project_id
              AND (state='ready' OR (state='rejected' AND reject_reason='duplicate')))
        THEN RAISE EXCEPTION 'asset_publication_fence_lost' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER jobs_publication_fence AFTER UPDATE ON jobs
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION penforge_publication_fence_guard();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER jobs_publication_fence ON jobs")
    op.execute("DROP FUNCTION penforge_publication_fence_guard()")
