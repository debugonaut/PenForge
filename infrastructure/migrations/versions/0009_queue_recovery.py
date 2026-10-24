"""Additive recovery ledger and live worker write guards; preserves v1 jobs."""

from alembic import op

revision = "0009_queue_recovery"
down_revision = "0008_font_exports"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE job_checkpoints (
      id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      owner_user_id uuid NOT NULL REFERENCES users(id), project_id uuid NOT NULL REFERENCES projects(id),
      stage text NOT NULL, cache_key text NOT NULL, manifest_asset_id uuid NOT NULL REFERENCES assets(id),
      manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
      dependencies jsonb NOT NULL, attempt integer NOT NULL, lease_holder text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
      CONSTRAINT uq_job_checkpoints_job_id UNIQUE(job_id, stage, cache_key)
    );
    CREATE INDEX job_checkpoints_expiry_idx ON job_checkpoints(expires_at);
    CREATE TABLE job_streams (
      id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      owner_user_id uuid NOT NULL REFERENCES users(id), expires_at timestamptz NOT NULL
    );
    CREATE INDEX job_streams_expiry_idx ON job_streams(expires_at);
    CREATE FUNCTION penforge_checkpoint_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'checkpoint_immutable' USING ERRCODE='23514'; END IF;
      IF NOT EXISTS(SELECT 1 FROM jobs j JOIN users u ON u.id=j.owner_user_id
        JOIN projects p ON p.id=j.project_id JOIN assets a ON a.id=NEW.manifest_asset_id
        WHERE j.id=NEW.job_id AND j.owner_user_id=NEW.owner_user_id AND j.project_id=NEW.project_id
          AND j.state='running' AND NOT j.cancel_requested AND j.attempts=NEW.attempt
          AND j.lease_holder=NEW.lease_holder AND j.lease_expires_at>clock_timestamp()
          AND u.account_state='active' AND u.merged_into_user_id IS NULL
          AND p.owner_user_id=j.owner_user_id AND p.status<>'deleting'
          AND a.owner_user_id=j.owner_user_id AND a.project_id=j.project_id AND a.kind='checkpoint'
          AND a.sha256=NEW.manifest_sha256 AND a.state='ready') THEN
        RAISE EXCEPTION 'checkpoint_fence_lost' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER job_checkpoints_guard BEFORE INSERT OR UPDATE ON job_checkpoints
      FOR EACH ROW EXECUTE FUNCTION penforge_checkpoint_guard();
    CREATE FUNCTION penforge_queue_write_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.state='running' AND NEW.state='running' AND (
          NEW.last_heartbeat_at IS DISTINCT FROM OLD.last_heartbeat_at OR
          NEW.checkpoint IS DISTINCT FROM OLD.checkpoint OR NEW.progress IS DISTINCT FROM OLD.progress) THEN
        IF OLD.lease_expires_at<=clock_timestamp() OR OLD.lease_holder IS NULL
          OR NEW.attempts<>OLD.attempts OR NEW.lease_holder IS DISTINCT FROM OLD.lease_holder
          OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id OR NEW.cancel_requested
          OR NOT EXISTS(SELECT 1 FROM users u JOIN projects p ON p.owner_user_id=u.id
            WHERE u.id=NEW.owner_user_id AND u.account_state='active' AND u.merged_into_user_id IS NULL
              AND p.id=NEW.project_id AND p.status<>'deleting') THEN
          RAISE EXCEPTION 'queue_write_fence_lost' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER jobs_live_write BEFORE UPDATE ON jobs
      FOR EACH ROW EXECUTE FUNCTION penforge_queue_write_guard();
    CREATE FUNCTION penforge_checkpoint_commit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM jobs j JOIN projects p ON p.id=j.project_id JOIN users u ON u.id=j.owner_user_id
        WHERE j.id=NEW.job_id AND j.owner_user_id=NEW.owner_user_id AND j.attempts=NEW.attempt
        AND (j.state='succeeded' OR (j.state='running' AND j.lease_holder=NEW.lease_holder))
        AND j.lease_expires_at>clock_timestamp() AND NOT j.cancel_requested
        AND p.owner_user_id=j.owner_user_id AND p.status<>'deleting' AND u.account_state='active'
        AND u.merged_into_user_id IS NULL) THEN
        RAISE EXCEPTION 'checkpoint_fence_lost' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER checkpoint_commit AFTER INSERT ON job_checkpoints
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_checkpoint_commit_guard();
    """)

    op.execute(
        "    ALTER TABLE job_events ADD COLUMN state_snapshot jsonb;\n    CREATE FUNCTION penforge_job_view(j jobs) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$\n    DECLARE total integer; completed integer; basis text; stage_value text; err text;\n    BEGIN\n      total := COALESCE((j.checkpoint->>'total_units')::integer,\n        CASE WHEN j.job_type='asset_verify' THEN 4\n        WHEN j.job_type='analyze_samples' THEN jsonb_array_length(j.payload->'sample_ids') ELSE NULL END);\n      completed := COALESCE((j.checkpoint->>'completed_units')::integer,0);\n      IF total IS NOT NULL AND (total<1 OR completed>total) THEN total:=NULL; END IF;\n      basis:= CASE WHEN j.job_type='asset_verify' THEN 'verified_checks'\n        WHEN j.job_type='analyze_samples' THEN 'assessed_samples'\n        WHEN j.stage='validating' THEN 'validation_layers' ELSE 'compiled_formats' END;\n      stage_value:=CASE WHEN j.stage ~ '^[a-z][a-z0-9_-]{0,80}$' THEN j.stage ELSE NULL END;\n      err:=CASE WHEN j.error_code ~ '^[a-z][a-z0-9_]{0,80}$' THEN j.error_code ELSE NULL END;\n      RETURN jsonb_build_object('job_id',j.id,'job_type',j.job_type,'state',j.state,'stage',stage_value,'attempts',j.attempts,\n        'progress',jsonb_build_object('completed_units',completed,'total_units',total,'basis',basis,\n          'indeterminate',total IS NULL,'stage_status',CASE WHEN j.state IN ('succeeded','failed','canceled') THEN 'finished'\n            WHEN j.state='queued' THEN 'waiting' ELSE 'active' END,\n          'denominator_change_reason',j.checkpoint->>'denominator_change_reason','plan_revision',COALESCE((j.checkpoint->>'plan_revision')::integer,1)),\n        'eta_seconds',NULL,'eta_reason','insufficient_data','quality_policy','accuracy_first',\n        'queue_wait',jsonb_build_object('entered_at',j.created_at,'eligible_at',j.run_after,'started_at',j.started_at),\n        'active_processing',jsonb_build_object('measured_seconds',j.usage->'wall_seconds','unmeasured_attempts',j.usage->'unmeasured_attempts'),\n        'user_review_wait',j.state='succeeded' AND j.job_type='analyze_samples',\n        'requires_user_action',j.state='succeeded' AND j.job_type='analyze_samples','error_code',err,\n        'retrying',j.state='queued' AND j.attempts>0,\n        'asset_id',j.payload->>'asset_id','result_asset_id',CASE WHEN j.state='succeeded' THEN j.payload->>'asset_id' ELSE NULL END);\n    END $$;\n    CREATE FUNCTION penforge_job_event_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$\n    DECLARE event_number bigint; snapshot jsonb;\n    BEGIN\n      IF TG_OP='UPDATE' AND NEW.state IS NOT DISTINCT FROM OLD.state AND NEW.stage IS NOT DISTINCT FROM OLD.stage\n        AND NEW.checkpoint IS NOT DISTINCT FROM OLD.checkpoint AND NEW.error_code IS NOT DISTINCT FROM OLD.error_code\n        AND NEW.usage IS NOT DISTINCT FROM OLD.usage THEN RETURN NULL; END IF;\n      event_number := nextval(pg_get_serial_sequence('job_events','id'));\n      snapshot := penforge_job_view(NEW) || jsonb_build_object('event_id',event_number);\n      INSERT INTO job_events(id,job_id,kind,to_state,stage,progress,state_snapshot)\n        VALUES(event_number,NEW.id,'progress',NEW.state,NEW.stage,NEW.progress,snapshot);\n      RETURN NULL;\n    END $$;\n    CREATE TRIGGER jobs_durable_events AFTER INSERT OR UPDATE ON jobs\n      FOR EACH ROW EXECUTE FUNCTION penforge_job_event_snapshot();\n    INSERT INTO job_events(job_id,kind,to_state,state_snapshot)\n      SELECT id,'state',state,penforge_job_view(jobs) FROM jobs;\n    UPDATE job_events SET state_snapshot=state_snapshot||jsonb_build_object('event_id',id)\n      WHERE state_snapshot IS NOT NULL;\n"
    )

    op.execute(
        "    ALTER TABLE assets ADD COLUMN worker_job_id uuid;\n    ALTER TABLE assets ADD CONSTRAINT fk_assets_worker_job_id_jobs FOREIGN KEY(worker_job_id) REFERENCES jobs(id) ON DELETE SET NULL;\n    CREATE FUNCTION penforge_worker_context_valid(job_uuid uuid, owner_uuid uuid, project_uuid uuid, attempt_number integer, commit_check boolean DEFAULT false)\n      RETURNS boolean LANGUAGE plpgsql AS $$\n    DECLARE ctx jsonb;\n    BEGIN\n      ctx:=NULLIF(current_setting('penforge.worker_fence',true),'')::jsonb;\n      IF ctx IS NULL THEN RETURN false; END IF;\n      RETURN ctx->>'job_id'=job_uuid::text AND (ctx->>'attempt')::integer=attempt_number\n        AND (ctx->>'expires_at')::timestamptz>clock_timestamp() AND EXISTS(\n          SELECT 1 FROM jobs j JOIN users u ON u.id=j.owner_user_id JOIN projects p ON p.id=j.project_id\n          WHERE j.id=job_uuid AND j.owner_user_id=owner_uuid AND j.project_id=project_uuid AND j.attempts=attempt_number\n            AND NOT j.cancel_requested AND ((j.state='running' AND j.lease_holder=ctx->>'holder' AND j.lease_expires_at>clock_timestamp())\n              OR (commit_check AND j.state='succeeded'))\n            AND u.account_state='active' AND u.merged_into_user_id IS NULL\n            AND p.owner_user_id=j.owner_user_id AND p.status<>'deleting');\n    EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN RETURN false;\n    END $$;\n    CREATE FUNCTION penforge_worker_asset_guard() RETURNS trigger LANGUAGE plpgsql AS $$\n    DECLARE job_uuid uuid; attempt_number integer;\n    BEGIN\n      IF NEW.state<>'ready' OR (TG_OP='UPDATE' AND OLD.state='ready') THEN RETURN NEW; END IF;\n      job_uuid:=COALESCE(NEW.worker_job_id,NEW.verify_job_id);\n      IF job_uuid IS NULL THEN RETURN NEW; END IF;\n      SELECT attempts INTO attempt_number FROM jobs WHERE id=job_uuid;\n      IF NOT penforge_worker_context_valid(job_uuid,NEW.owner_user_id,NEW.project_id,attempt_number,TG_WHEN='AFTER') THEN\n        RAISE EXCEPTION 'worker_asset_fence_lost' USING ERRCODE='23514'; END IF;\n      RETURN NEW;\n    END $$;\n    CREATE TRIGGER assets_worker_live BEFORE INSERT OR UPDATE ON assets FOR EACH ROW EXECUTE FUNCTION penforge_worker_asset_guard();\n    CREATE CONSTRAINT TRIGGER assets_worker_commit AFTER INSERT OR UPDATE ON assets DEFERRABLE INITIALLY DEFERRED\n      FOR EACH ROW EXECUTE FUNCTION penforge_worker_asset_guard();\n    CREATE FUNCTION penforge_worker_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$\n    DECLARE j record; result_attempt integer;\n    BEGIN\n      IF TG_TABLE_NAME='glyph_candidates' THEN\n        SELECT job_id,attempt INTO j FROM ml_result_artifacts WHERE id=NEW.result_artifact_id;\n        result_attempt:=j.attempt;\n        SELECT * INTO j FROM jobs WHERE id=j.job_id;\n      ELSE\n        result_attempt:=NEW.attempt;\n        SELECT * INTO j FROM jobs WHERE id=NEW.job_id;\n      END IF;\n      IF j.id IS NULL OR NOT penforge_worker_context_valid(j.id,j.owner_user_id,NEW.project_id,result_attempt,TG_WHEN='AFTER') THEN\n        RAISE EXCEPTION 'worker_result_fence_lost' USING ERRCODE='23514'; END IF;\n      RETURN NEW;\n    END $$;\n    CREATE TRIGGER results_worker_live BEFORE INSERT ON ml_result_artifacts FOR EACH ROW EXECUTE FUNCTION penforge_worker_result_guard();\n    CREATE TRIGGER candidates_worker_live BEFORE INSERT ON glyph_candidates FOR EACH ROW EXECUTE FUNCTION penforge_worker_result_guard();\n    CREATE CONSTRAINT TRIGGER candidates_worker_commit AFTER INSERT ON glyph_candidates DEFERRABLE INITIALLY DEFERRED\n      FOR EACH ROW EXECUTE FUNCTION penforge_worker_result_guard();\n    CREATE FUNCTION penforge_worker_export_guard() RETURNS trigger LANGUAGE plpgsql AS $$\n    DECLARE j record;\n    BEGIN\n      IF NEW.status='ready' AND OLD.status='pending' AND NEW.build_manifest IS NOT NULL THEN\n        SELECT * INTO j FROM jobs WHERE id=NEW.job_id;\n        IF NOT penforge_worker_context_valid(j.id,j.owner_user_id,NEW.project_id,NEW.finalized_attempt) THEN\n          RAISE EXCEPTION 'worker_export_fence_lost' USING ERRCODE='23514'; END IF;\n      END IF;\n      RETURN NEW;\n    END $$;\n    CREATE TRIGGER exports_worker_live BEFORE UPDATE ON font_exports FOR EACH ROW EXECUTE FUNCTION penforge_worker_export_guard();\n"
    )


def downgrade():
    import sqlalchemy as sa

    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM job_checkpoints)")):
        raise RuntimeError(
            "phase06_checkpoints_retained: drain with compatible workers; do not truncate"
        )
    op.execute("""
    DROP TRIGGER exports_worker_live ON font_exports;
    DROP FUNCTION penforge_worker_export_guard();
    DROP TRIGGER candidates_worker_commit ON glyph_candidates;
    DROP TRIGGER candidates_worker_live ON glyph_candidates;
    DROP TRIGGER results_worker_live ON ml_result_artifacts;
    DROP FUNCTION penforge_worker_result_guard();
    DROP TRIGGER assets_worker_commit ON assets;
    DROP TRIGGER assets_worker_live ON assets;
    DROP FUNCTION penforge_worker_asset_guard();
    DROP FUNCTION penforge_worker_context_valid(uuid,uuid,uuid,integer,boolean);
    ALTER TABLE assets DROP COLUMN worker_job_id;
    DROP TRIGGER jobs_durable_events ON jobs;
    DROP FUNCTION penforge_job_event_snapshot();
    DROP FUNCTION penforge_job_view(jobs);
    ALTER TABLE job_events DROP COLUMN state_snapshot;
    DROP TABLE job_streams;
    DROP TABLE job_checkpoints;
    DROP FUNCTION penforge_checkpoint_commit_guard();
    DROP FUNCTION penforge_checkpoint_guard();
    DROP TRIGGER jobs_live_write ON jobs;
    DROP FUNCTION penforge_queue_write_guard();
    """)
