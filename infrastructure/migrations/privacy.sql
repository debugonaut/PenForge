SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE assets ADD COLUMN last_used_at timestamptz;
UPDATE assets SET last_used_at=created_at;
ALTER TABLE assets ALTER COLUMN last_used_at SET NOT NULL, ALTER COLUMN last_used_at SET DEFAULT now();
ALTER TABLE deletion_requests ADD COLUMN idempotency_hash bytea,
 ADD COLUMN request_hash bytea, ADD COLUMN project_ref bytea,
 ADD COLUMN identity_uids jsonb NOT NULL DEFAULT '[]',
 ADD COLUMN target_users jsonb NOT NULL DEFAULT '[]',
 ADD COLUMN consent_ids jsonb NOT NULL DEFAULT '[]',
 ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX deletion_requests_subject_idx ON deletion_requests(subject_ref,id);
CREATE UNIQUE INDEX deletion_requests_idem_idx ON deletion_requests(subject_ref,idempotency_hash)
 WHERE idempotency_hash IS NOT NULL;
ALTER TABLE dataset_contributions ADD COLUMN storage_key text, ADD COLUMN content_sha256 bytea,
 ADD COLUMN source_asset_id uuid REFERENCES assets(id) ON DELETE SET NULL,
 ADD COLUMN transformation text NOT NULL DEFAULT 'legacy_unavailable',
 ADD COLUMN source_lineage jsonb NOT NULL DEFAULT '{}', ADD COLUMN copy_completed_at timestamptz;
CREATE INDEX dataset_contributions_consent_idx ON dataset_contributions(consent_id);
CREATE TABLE dataset_versions (
 version text PRIMARY KEY, manifest jsonb NOT NULL, manifest_sha256 bytea NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_dataset_versions_manifest_sha256_length CHECK(octet_length(manifest_sha256)=32));
CREATE FUNCTION penforge_consent_history_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (to_jsonb(NEW)-'withdrawn_at') IS DISTINCT FROM (to_jsonb(OLD)-'withdrawn_at')
   OR (OLD.withdrawn_at IS NOT NULL AND NEW.withdrawn_at IS DISTINCT FROM OLD.withdrawn_at) THEN
  RAISE EXCEPTION 'consent_history_immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER training_consent_history BEFORE UPDATE ON training_consents
 FOR EACH ROW EXECUTE FUNCTION penforge_consent_history_guard();
CREATE FUNCTION penforge_dataset_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'dataset_version_immutable' USING ERRCODE='23514'; END $$;
CREATE TRIGGER dataset_version_immutable BEFORE UPDATE OR DELETE ON dataset_versions
 FOR EACH ROW EXECUTE FUNCTION penforge_dataset_version_guard();
-- A context string alone grants nothing: the request, completed storage fence and absent
-- projects/live work are checked. PUBLIC cannot invoke the elevated purge routine.
CREATE FUNCTION penforge_privacy_erasure_valid() RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE r deletion_requests;
BEGIN
 SELECT * INTO r FROM deletion_requests
 WHERE id=NULLIF(current_setting('penforge.erasure_request',true),'')::uuid;
 RETURN FOUND AND r.scope='account' AND r.state='in_progress'
   AND r.progress->>'storage'='done'
   AND NOT EXISTS(SELECT 1 FROM projects WHERE owner_user_id::text IN
      (SELECT jsonb_array_elements_text(r.target_users)))
   AND NOT EXISTS(SELECT 1 FROM jobs WHERE owner_user_id::text IN
      (SELECT jsonb_array_elements_text(r.target_users)) AND state IN ('queued','running','cancel_requested'));
EXCEPTION WHEN invalid_text_representation THEN RETURN false;
END $$;
CREATE OR REPLACE FUNCTION penforge_identity_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND penforge_privacy_erasure_valid() THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'identity_claim_receipt_immutable' USING ERRCODE='23514';
END $$;
CREATE OR REPLACE FUNCTION penforge_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP IN ('DELETE','UPDATE') AND penforge_privacy_erasure_valid() THEN RETURN NULL; END IF;
 IF TG_OP='DELETE' AND current_setting('penforge.audit_retention',true)='v1' THEN RETURN NULL; END IF;
 RAISE EXCEPTION 'audit_append_only' USING ERRCODE='42501';
END $$;
CREATE FUNCTION penforge_purge_account(request_uuid uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE r deletion_requests; result bigint;
BEGIN
 PERFORM set_config('penforge.erasure_request',request_uuid::text,true);
 IF NOT penforge_privacy_erasure_valid() THEN
  RAISE EXCEPTION 'erasure_not_ready' USING ERRCODE='23514'; END IF;
 SELECT * INTO r FROM deletion_requests WHERE id=request_uuid FOR UPDATE;
 DELETE FROM audit_events WHERE actor_ref IN (SELECT jsonb_array_elements_text(r.target_users))
   OR resource_id::text IN (SELECT jsonb_array_elements_text(r.target_users));
 DELETE FROM identity_claims WHERE guest_user_id::text IN (SELECT jsonb_array_elements_text(r.target_users))
   OR destination_user_id::text IN (SELECT jsonb_array_elements_text(r.target_users));
 UPDATE deletion_requests SET user_id=NULL, project_id=NULL, identity_uids='[]', target_users='[]', consent_ids='[]'
 WHERE id<>request_uuid AND user_id::text IN (SELECT jsonb_array_elements_text(r.target_users));
 DELETE FROM users WHERE id::text IN (SELECT jsonb_array_elements_text(r.target_users));
 GET DIAGNOSTICS result=ROW_COUNT;
 PERFORM set_config('penforge.erasure_request','',true);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION penforge_purge_account(uuid) FROM PUBLIC;

CREATE FUNCTION penforge_privacy_step_fence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ctx jsonb; j jobs;
BEGIN
 IF NEW.progress IS NOT DISTINCT FROM OLD.progress THEN RETURN NULL; END IF;
 ctx:=NULLIF(current_setting('penforge.privacy_fence',true),'')::jsonb;
 IF ctx IS NULL THEN RAISE EXCEPTION 'privacy_fence_required' USING ERRCODE='23514'; END IF;
 SELECT * INTO j FROM jobs WHERE id=(ctx->>'job_id')::uuid;
 IF NOT FOUND OR j.job_type<>'privacy_maintenance' OR j.attempts<>(ctx->>'attempt')::int
   OR j.payload->>'deletion_request_id'<>NEW.id::text
   OR (ctx->>'expires_at')::timestamptz<=clock_timestamp()
   OR j.state NOT IN ('running','queued','succeeded') THEN
   RAISE EXCEPTION 'privacy_fence_lost' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER privacy_step_fence AFTER UPDATE ON deletion_requests
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_privacy_step_fence();

CREATE FUNCTION penforge_prune_audit() RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE seconds bigint; result bigint;
BEGIN
 SELECT COALESCE((value->>'audit_seconds')::bigint,31536000) INTO seconds FROM system_settings WHERE key='retention_policy.v1';
 seconds:=COALESCE(seconds,31536000);
 IF seconds<60 THEN RAISE EXCEPTION 'retention_policy_invalid'; END IF;
 PERFORM set_config('penforge.audit_retention','v1',true);
 DELETE FROM audit_events WHERE occurred_at<clock_timestamp()-make_interval(secs=>seconds);
 GET DIAGNOSTICS result=ROW_COUNT;
 PERFORM set_config('penforge.audit_retention','',true);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION penforge_prune_audit() FROM PUBLIC;

SET CONSTRAINTS ALL DEFERRED;
