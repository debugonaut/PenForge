"""Fence analysis publication at commit; candidates are never activations."""

from alembic import op

revision = "0007_analysis_fence"
down_revision = "0006_extraction_review"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION penforge_result_live_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM jobs j JOIN projects p ON p.id=j.project_id JOIN users u ON u.id=j.owner_user_id
          WHERE j.id=NEW.job_id AND j.project_id=NEW.project_id AND j.attempts=NEW.attempt
          AND j.job_type='analyze_samples' AND j.state='running' AND NOT j.cancel_requested
          AND j.lease_holder IS NOT NULL AND j.lease_expires_at>clock_timestamp()
          AND p.owner_user_id=j.owner_user_id AND p.status<>'deleting'
          AND u.account_state='active' AND u.merged_into_user_id IS NULL) THEN
        RAISE EXCEPTION 'analysis_publication_fence_lost' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER results_live_attempt BEFORE INSERT ON ml_result_artifacts
      FOR EACH ROW EXECUTE FUNCTION penforge_result_live_attempt_guard();
    CREATE FUNCTION penforge_analysis_fence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE j record;
    BEGIN
      IF OLD.state='running' AND NEW.state='succeeded' AND NEW.job_type='analyze_samples' THEN
        SELECT * INTO j FROM jobs WHERE id=NEW.id;
        IF OLD.lease_holder IS NULL OR OLD.lease_expires_at IS NULL
          OR OLD.lease_expires_at <= clock_timestamp()
          OR j.attempts IS DISTINCT FROM OLD.attempts OR j.state<>'succeeded'
          OR j.owner_user_id IS DISTINCT FROM OLD.owner_user_id
          OR NOT EXISTS(SELECT 1 FROM users WHERE id=j.owner_user_id AND account_state='active' AND merged_into_user_id IS NULL)
          OR NOT EXISTS(SELECT 1 FROM projects WHERE id=j.project_id AND owner_user_id=j.owner_user_id AND status<>'deleting')
          OR NOT EXISTS(SELECT 1 FROM ml_result_artifacts WHERE job_id=j.id AND attempt=j.attempts AND project_id=j.project_id)
        THEN RAISE EXCEPTION 'analysis_publication_fence_lost' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER jobs_analysis_publication_fence AFTER UPDATE ON jobs
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_analysis_fence_guard();
    """)


def downgrade():
    op.execute("DROP TRIGGER results_live_attempt ON ml_result_artifacts")
    op.execute("DROP FUNCTION penforge_result_live_attempt_guard()")
    op.execute("DROP TRIGGER jobs_analysis_publication_fence ON jobs")
    op.execute("DROP FUNCTION penforge_analysis_fence_guard()")
