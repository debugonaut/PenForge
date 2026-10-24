-- Owners are locked before an insert/update can race a claim/deletion. Services
-- still authenticate and take the same user locks before touching child rows.
CREATE FUNCTION penforge_identity_owner_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_id uuid; previous_owner uuid; state text; merged uuid; project_state text;
BEGIN
  IF TG_TABLE_NAME IN ('projects','assets','jobs') THEN
    owner_id := NEW.owner_user_id;
    IF TG_OP='UPDATE' THEN previous_owner := OLD.owner_user_id; END IF;
  ELSE
    owner_id := NEW.user_id;
    IF TG_OP='UPDATE' THEN previous_owner := OLD.user_id; END IF;
  END IF;
  IF owner_id IS NOT NULL THEN
    SELECT account_state,merged_into_user_id INTO state,merged FROM users
      WHERE id=owner_id FOR SHARE;
    IF NOT FOUND OR merged IS NOT NULL THEN
      RAISE EXCEPTION 'identity_owner_inactive' USING ERRCODE='23514';
    END IF;
    IF state <> 'active' AND (TG_OP='INSERT' OR owner_id IS DISTINCT FROM previous_owner) THEN
      RAISE EXCEPTION 'identity_owner_inactive' USING ERRCODE='23514';
    END IF;
  END IF;
  IF TG_TABLE_NAME='jobs' THEN
    IF TG_OP='INSERT' THEN
      IF NEW.idempotency_scope_user_id IS NOT NULL AND NEW.idempotency_scope_user_id IS DISTINCT FROM owner_id THEN
        RAISE EXCEPTION 'job_idempotency_origin_mismatch' USING ERRCODE='23514';
      END IF;
      NEW.idempotency_scope_user_id := owner_id;
    ELSIF NEW.idempotency_scope_user_id IS DISTINCT FROM OLD.idempotency_scope_user_id THEN
      RAISE EXCEPTION 'job_idempotency_origin_immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.project_id IS NOT NULL AND (TG_OP='INSERT' OR (NEW.state='running' AND OLD.state='queued')) THEN
      SELECT status INTO project_state FROM projects WHERE id=NEW.project_id FOR SHARE;
      IF project_state='deleting' OR state <> 'active' THEN
        RAISE EXCEPTION 'job_scope_deleting' USING ERRCODE='23514';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER identity_owner_write BEFORE INSERT OR UPDATE ON projects FOR EACH ROW EXECUTE FUNCTION penforge_identity_owner_guard();
CREATE TRIGGER identity_owner_write BEFORE INSERT OR UPDATE ON assets FOR EACH ROW EXECUTE FUNCTION penforge_identity_owner_guard();
CREATE TRIGGER identity_owner_write BEFORE INSERT OR UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION penforge_identity_owner_guard();
CREATE TRIGGER identity_owner_write BEFORE INSERT OR UPDATE ON quota_balances FOR EACH ROW EXECUTE FUNCTION penforge_identity_owner_guard();
CREATE TRIGGER identity_owner_write BEFORE INSERT OR UPDATE ON quota_reservations FOR EACH ROW EXECUTE FUNCTION penforge_identity_owner_guard();

CREATE FUNCTION penforge_identity_user_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.is_anonymous=false AND NEW.is_anonymous=true THEN
    RAISE EXCEPTION 'identity_upgrade_monotonic' USING ERRCODE='23514';
  END IF;
  IF OLD.merged_into_user_id IS NOT NULL AND NEW.merged_into_user_id IS DISTINCT FROM OLD.merged_into_user_id THEN
    RAISE EXCEPTION 'identity_merge_final' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER identity_user_transition BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION penforge_identity_user_guard();

CREATE FUNCTION penforge_identity_consistency_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_id uuid; merged uuid; r record;
BEGIN
  IF TG_TABLE_NAME='users' THEN
    SELECT merged_into_user_id INTO merged FROM users WHERE id=NEW.id;
    IF merged IS NOT NULL AND (
      EXISTS (SELECT 1 FROM projects WHERE owner_user_id=NEW.id) OR
      EXISTS (SELECT 1 FROM assets WHERE owner_user_id=NEW.id) OR
      EXISTS (SELECT 1 FROM jobs WHERE owner_user_id=NEW.id) OR
      EXISTS (SELECT 1 FROM quota_balances WHERE user_id=NEW.id) OR
      EXISTS (SELECT 1 FROM quota_reservations WHERE user_id=NEW.id)
    ) THEN RAISE EXCEPTION 'merged_identity_retains_ownership' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='jobs' THEN
    SELECT * INTO r FROM jobs WHERE id=NEW.id;
    IF FOUND AND EXISTS (SELECT 1 FROM quota_reservations WHERE job_id=r.id AND user_id IS DISTINCT FROM r.owner_user_id) THEN
      RAISE EXCEPTION 'reservation_job_owner_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='quota_balances' THEN
    SELECT * INTO r FROM quota_balances WHERE id=NEW.id;
    IF FOUND AND EXISTS (SELECT 1 FROM quota_reservations WHERE balance_id=r.id AND
      (resource<>r.resource OR (r.scope='user' AND user_id IS DISTINCT FROM r.user_id))) THEN
      RAISE EXCEPTION 'reservation_balance_mismatch' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER identity_owner_consistency AFTER INSERT OR UPDATE ON users DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_identity_consistency_guard();
CREATE CONSTRAINT TRIGGER identity_owner_consistency AFTER INSERT OR UPDATE ON projects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_identity_consistency_guard();
CREATE CONSTRAINT TRIGGER identity_owner_consistency AFTER INSERT OR UPDATE ON jobs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_identity_consistency_guard();
CREATE CONSTRAINT TRIGGER identity_owner_consistency AFTER INSERT OR UPDATE ON quota_balances DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_identity_consistency_guard();

CREATE FUNCTION penforge_identity_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'identity_claim_receipt_immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER identity_claims_immutable BEFORE UPDATE OR DELETE ON identity_claims FOR EACH ROW EXECUTE FUNCTION penforge_identity_receipt_guard();

-- Historical upload consent is provenance; claiming never grants new consent.
CREATE OR REPLACE FUNCTION penforge_reference_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r record; parent_glyph uuid; parent_seq bigint; item record; parent_owner uuid;
BEGIN
  IF TG_TABLE_NAME='glyphs' THEN
    SELECT * INTO r FROM glyphs WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF r.active_revision_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM glyph_revisions WHERE id=r.active_revision_id AND glyph_id=r.id AND approval='approved'
    ) THEN RAISE EXCEPTION 'active_revision_requires_same_approved_glyph' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='projects' THEN
    SELECT * INTO r FROM projects WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF r.active_snapshot_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM project_snapshots WHERE id=r.active_snapshot_id AND project_id=r.id
    ) THEN RAISE EXCEPTION 'active_snapshot_project_mismatch' USING ERRCODE='23514'; END IF;
    IF EXISTS (SELECT 1 FROM assets WHERE project_id=r.id AND owner_user_id<>r.owner_user_id)
      OR EXISTS (SELECT 1 FROM jobs WHERE project_id=r.id AND owner_user_id IS DISTINCT FROM r.owner_user_id) THEN
      RAISE EXCEPTION 'project_child_owner_mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='assets' THEN
    SELECT * INTO r FROM assets WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF r.project_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM projects WHERE id=r.project_id AND owner_user_id=r.owner_user_id) THEN
      RAISE EXCEPTION 'asset_owner_mismatch' USING ERRCODE='23514'; END IF;
    IF r.duplicate_of IS NOT NULL AND (r.duplicate_of=r.id OR NOT EXISTS (
      SELECT 1 FROM assets WHERE id=r.duplicate_of AND owner_user_id=r.owner_user_id AND duplicate_of IS NULL
      AND (r.kind<>'original_upload' OR r.state<>'ready' OR (kind='original_upload' AND sha256=r.sha256))
    )) THEN RAISE EXCEPTION 'asset_duplicate_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='samples' THEN
    SELECT * INTO r FROM samples WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM assets WHERE id=r.asset_id AND project_id=r.project_id) THEN
      RAISE EXCEPTION 'sample_asset_project_mismatch' USING ERRCODE='23514'; END IF;
    IF r.consent_id_at_upload IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM training_consents c JOIN users origin ON origin.id=c.user_id
      JOIN projects p ON (p.owner_user_id=c.user_id OR p.owner_user_id=origin.merged_into_user_id)
      WHERE c.id=r.consent_id_at_upload AND p.id=r.project_id
    ) THEN RAISE EXCEPTION 'sample_consent_owner_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='glyph_revisions' THEN
    SELECT v.*,g.project_id INTO r FROM glyph_revisions v JOIN glyphs g ON g.id=v.glyph_id WHERE v.id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF r.parent_revision_id IS NOT NULL THEN
      SELECT glyph_id,seq INTO parent_glyph,parent_seq FROM glyph_revisions WHERE id=r.parent_revision_id;
      IF parent_glyph IS DISTINCT FROM r.glyph_id OR parent_seq >= r.seq THEN
        RAISE EXCEPTION 'revision_parent_lineage' USING ERRCODE='23514'; END IF;
    END IF;
    IF r.outline_asset_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM assets WHERE id=r.outline_asset_id AND project_id=r.project_id AND kind='vector' AND state='ready') THEN
      RAISE EXCEPTION 'revision_outline_asset_mismatch' USING ERRCODE='23514'; END IF;
    IF r.source_sample_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM samples WHERE id=r.source_sample_id AND project_id=r.project_id) THEN
      RAISE EXCEPTION 'revision_sample_mismatch' USING ERRCODE='23514'; END IF;
    IF r.job_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs WHERE id=r.job_id AND project_id=r.project_id) THEN
      RAISE EXCEPTION 'revision_job_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='project_snapshots' THEN
    SELECT * INTO r FROM project_snapshots WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF jsonb_typeof(r.manifest) <> 'object' OR jsonb_typeof(r.manifest->'glyphs') IS DISTINCT FROM 'object' THEN
      RAISE EXCEPTION 'snapshot_manifest_shape' USING ERRCODE='23514'; END IF;
    FOR item IN SELECT * FROM jsonb_each_text(r.manifest->'glyphs') LOOP
      IF NOT EXISTS (SELECT 1 FROM glyphs g JOIN glyph_revisions v ON v.glyph_id=g.id
        WHERE g.id::text=item.key AND v.id::text=item.value AND g.project_id=r.project_id AND v.approval='approved') THEN
        RAISE EXCEPTION 'snapshot_revision_mismatch' USING ERRCODE='23514'; END IF;
    END LOOP;
  ELSIF TG_TABLE_NAME='jobs' THEN
    SELECT * INTO r FROM jobs WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF r.project_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM projects WHERE id=r.project_id AND owner_user_id=r.owner_user_id) THEN
      RAISE EXCEPTION 'job_owner_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='font_exports' THEN
    SELECT * INTO r FROM font_exports WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM project_snapshots WHERE id=r.snapshot_id AND project_id=r.project_id) THEN
      RAISE EXCEPTION 'export_snapshot_mismatch' USING ERRCODE='23514'; END IF;
    IF r.asset_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM assets WHERE id=r.asset_id AND project_id=r.project_id AND kind='export' AND state='ready') THEN
      RAISE EXCEPTION 'export_asset_mismatch' USING ERRCODE='23514'; END IF;
    IF r.validation_report_asset_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM assets WHERE id=r.validation_report_asset_id AND project_id=r.project_id AND kind='validation_report' AND state='ready') THEN
      RAISE EXCEPTION 'export_report_mismatch' USING ERRCODE='23514'; END IF;
    IF r.job_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs WHERE id=r.job_id AND project_id=r.project_id) THEN
      RAISE EXCEPTION 'export_job_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='style_profiles' THEN
    SELECT * INTO r FROM style_profiles WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF r.embedding_asset_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM assets WHERE id=r.embedding_asset_id AND project_id=r.project_id AND kind='style_embedding') THEN
      RAISE EXCEPTION 'style_embedding_mismatch' USING ERRCODE='23514'; END IF;
    IF EXISTS (SELECT 1 FROM unnest(r.reference_glyph_ids) id WHERE NOT EXISTS
      (SELECT 1 FROM glyphs g WHERE g.id=id AND g.project_id=r.project_id)) THEN
      RAISE EXCEPTION 'style_reference_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='users' THEN
    IF EXISTS (WITH RECURSIVE chain AS (
      SELECT id,merged_into_user_id,ARRAY[id] path,false cycle FROM users WHERE id=NEW.id
      UNION ALL SELECT u.id,u.merged_into_user_id,c.path||u.id,u.id=ANY(c.path)
      FROM users u JOIN chain c ON u.id=c.merged_into_user_id WHERE NOT c.cycle
    ) SELECT 1 FROM chain WHERE cycle) THEN
      RAISE EXCEPTION 'user_merge_cycle' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='quota_reservations' THEN
    SELECT * INTO r FROM quota_reservations WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM quota_balances b WHERE b.id=r.balance_id AND b.resource=r.resource
       AND (b.scope='global' OR b.user_id=r.user_id)) THEN
      RAISE EXCEPTION 'reservation_balance_mismatch' USING ERRCODE='23514'; END IF;
    IF r.job_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs WHERE id=r.job_id AND owner_user_id IS NOT DISTINCT FROM r.user_id) THEN
      RAISE EXCEPTION 'reservation_job_owner_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='dataset_contributions' THEN
    SELECT * INTO r FROM dataset_contributions WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF r.status='active' AND (r.sample_id IS NULL OR r.consent_id IS NULL OR NOT EXISTS (
      SELECT 1 FROM samples s JOIN projects p ON p.id=s.project_id JOIN training_consents c ON c.user_id=p.owner_user_id
      WHERE s.id=r.sample_id AND c.id=r.consent_id AND c.withdrawn_at IS NULL
    )) THEN RAISE EXCEPTION 'active_contribution_requires_owner_consent' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='training_consents' THEN
    IF EXISTS (
      SELECT 1 FROM dataset_contributions d WHERE d.consent_id=NEW.id AND d.status='active'
      AND NOT EXISTS (
        SELECT 1 FROM training_consents c JOIN samples s ON s.id=d.sample_id
        JOIN projects p ON p.id=s.project_id
        WHERE c.id=d.consent_id AND c.user_id=p.owner_user_id AND c.withdrawn_at IS NULL
      )
    ) THEN RAISE EXCEPTION 'active_contribution_requires_owner_consent' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NULL;
END $$;
