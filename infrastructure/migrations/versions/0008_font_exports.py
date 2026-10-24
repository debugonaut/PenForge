"""Frozen deterministic builds and commit-time export publication fences."""

from alembic import op

revision = "0008_font_exports"
down_revision = "0007_analysis_fence"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE font_exports ADD COLUMN build_manifest jsonb,
      ADD COLUMN build_id bytea CHECK(build_id IS NULL OR octet_length(build_id)=32),
      ADD COLUMN validation_report jsonb,
      ADD COLUMN finalized_attempt integer;
    CREATE FUNCTION penforge_export_build_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.build_manifest IS NOT NULL AND (NEW.build_manifest IS DISTINCT FROM OLD.build_manifest
        OR NEW.build_id IS DISTINCT FROM OLD.build_id OR NEW.snapshot_id<>OLD.snapshot_id
        OR NEW.project_id<>OLD.project_id OR NEW.format<>OLD.format
        OR NEW.family_name<>OLD.family_name OR NEW.style_name<>OLD.style_name
        OR (NEW.job_id IS DISTINCT FROM OLD.job_id AND NOT (NEW.job_id IS NULL AND NOT EXISTS(SELECT 1 FROM jobs WHERE id=OLD.job_id)))) THEN
        RAISE EXCEPTION 'export_build_immutable' USING ERRCODE='23514'; END IF;
      IF NEW.status='ready' AND OLD.status='pending' AND NEW.build_manifest IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM jobs j JOIN projects p ON p.id=j.project_id JOIN users u ON u.id=j.owner_user_id
        WHERE j.id=NEW.job_id AND j.job_type='compile_export' AND j.state='running'
          AND j.attempts=NEW.finalized_attempt AND j.lease_holder IS NOT NULL
          AND j.lease_expires_at>clock_timestamp() AND NOT j.cancel_requested
          AND p.id=NEW.project_id AND p.owner_user_id=j.owner_user_id AND p.status<>'deleting'
          AND u.account_state='active' AND u.merged_into_user_id IS NULL
          AND NEW.validation_report->>'status' IN ('passed','passed_with_warnings')) THEN
        RAISE EXCEPTION 'export_publication_fence_lost' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER exports_build_guard BEFORE UPDATE ON font_exports
      FOR EACH ROW EXECUTE FUNCTION penforge_export_build_guard();
    CREATE FUNCTION penforge_export_commit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE j record;
    BEGIN
      IF NEW.job_type='compile_export' AND OLD.state='running' AND NEW.state='succeeded' THEN
        SELECT * INTO j FROM jobs WHERE id=NEW.id;
        IF OLD.lease_holder IS NULL OR OLD.lease_expires_at<=clock_timestamp()
          OR j.attempts<>OLD.attempts OR j.owner_user_id IS DISTINCT FROM OLD.owner_user_id
          OR j.state<>'succeeded' OR j.cancel_requested
          OR NOT EXISTS(SELECT 1 FROM projects p JOIN users u ON u.id=p.owner_user_id
            WHERE p.id=j.project_id AND p.owner_user_id=j.owner_user_id AND p.status<>'deleting'
            AND u.account_state='active' AND u.merged_into_user_id IS NULL)
          OR EXISTS(SELECT 1 FROM font_exports WHERE job_id=j.id AND (status='pending' OR finalized_attempt<>j.attempts))
          OR NOT EXISTS(SELECT 1 FROM font_exports WHERE job_id=j.id)
        THEN RAISE EXCEPTION 'export_publication_fence_lost' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER jobs_export_commit AFTER UPDATE ON jobs
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_export_commit_guard();
    """)


def downgrade():
    import sqlalchemy as sa

    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM font_exports WHERE build_manifest IS NOT NULL)")
    ):
        raise RuntimeError(
            "phase05_export_lineage_retained: keep this additive migration while exports reference frozen builds"
        )
    op.execute(
        "DROP TRIGGER jobs_export_commit ON jobs; DROP FUNCTION penforge_export_commit_guard(); DROP TRIGGER exports_build_guard ON font_exports; DROP FUNCTION penforge_export_build_guard(); ALTER TABLE font_exports DROP COLUMN finalized_attempt, DROP COLUMN validation_report, DROP COLUMN build_id, DROP COLUMN build_manifest"
    )
