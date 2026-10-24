-- Invariants guard consistency. Application services must still authorize every write.
CREATE FUNCTION penforge_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE project_owner uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF EXISTS (SELECT 1 FROM project_snapshots s
      WHERE s.manifest->'glyphs'->>OLD.glyph_id::text = OLD.id::text
        AND EXISTS (SELECT 1 FROM projects WHERE id=s.project_id)) THEN
      RAISE EXCEPTION 'snapshot_referenced_revision' USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
  END IF;
  SELECT p.owner_user_id INTO project_owner FROM glyphs g JOIN projects p ON p.id=g.project_id
    WHERE g.id=NEW.glyph_id;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_by_user_id IS NOT NULL AND NEW.created_by_user_id IS DISTINCT FROM project_owner THEN
      RAISE EXCEPTION 'revision_creator_owner_mismatch' USING ERRCODE = '23514';
    END IF;
    IF NEW.origin = 'ml_generated' AND NEW.approval <> 'pending' THEN
      RAISE EXCEPTION 'generated_revision_requires_review' USING ERRCODE = '23514';
    END IF;
  ELSE
    IF (to_jsonb(NEW) - ARRAY['approval','approved_by_user_id','approved_at','source_sample_id','job_id'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['approval','approved_by_user_id','approved_at','source_sample_id','job_id']) THEN
      RAISE EXCEPTION 'revision_content_immutable' USING ERRCODE = '23514';
    END IF;
    IF (NEW.source_sample_id IS DISTINCT FROM OLD.source_sample_id AND NOT
        (NEW.source_sample_id IS NULL AND NOT EXISTS (SELECT 1 FROM samples WHERE id=OLD.source_sample_id)))
       OR (NEW.job_id IS DISTINCT FROM OLD.job_id AND NOT
        (NEW.job_id IS NULL AND NOT EXISTS (SELECT 1 FROM jobs WHERE id=OLD.job_id))) THEN
      RAISE EXCEPTION 'revision_provenance_immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.approval <> 'pending' AND
      (NEW.approval,NEW.approved_by_user_id,NEW.approved_at) IS DISTINCT FROM
      (OLD.approval,OLD.approved_by_user_id,OLD.approved_at) THEN
      RAISE EXCEPTION 'revision_review_final' USING ERRCODE = '23514';
    END IF;
  END IF;
  IF NEW.approval = 'approved' AND (TG_OP = 'INSERT' OR OLD.approval = 'pending') THEN
    IF NEW.approved_by_user_id IS DISTINCT FROM project_owner OR NEW.approved_at IS NULL THEN
      RAISE EXCEPTION 'owner_approval_required' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER glyph_revisions_immutable BEFORE INSERT OR UPDATE OR DELETE ON glyph_revisions
  FOR EACH ROW EXECUTE FUNCTION penforge_revision_guard();

CREATE FUNCTION penforge_snapshot_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM projects WHERE id=OLD.project_id) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'snapshot_immutable' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER project_snapshots_immutable BEFORE UPDATE OR DELETE ON project_snapshots
  FOR EACH ROW EXECUTE FUNCTION penforge_snapshot_guard();

CREATE FUNCTION penforge_asset_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.project_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM projects WHERE id=OLD.project_id) THEN
      RETURN OLD;
    END IF;
    IF OLD.state <> 'deleted' THEN
      RAISE EXCEPTION 'asset_delete_requires_deletion_state' USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
  END IF;
  IF NEW.project_id IS DISTINCT FROM OLD.project_id THEN
    RAISE EXCEPTION 'asset_project_immutable' USING ERRCODE = '23514';
  END IF;
  -- Remains protected after ready -> deleting -> deleted, preventing a bypass by state changes.
  IF OLD.verified_at IS NOT NULL AND
     (NEW.storage_backend,NEW.storage_key,NEW.sha256,NEW.size_bytes,NEW.mime_type,
      NEW.width_px,NEW.height_px,NEW.kind,NEW.is_immutable,NEW.verified_at)
     IS DISTINCT FROM
     (OLD.storage_backend,OLD.storage_key,OLD.sha256,OLD.size_bytes,OLD.mime_type,
      OLD.width_px,OLD.height_px,OLD.kind,OLD.is_immutable,OLD.verified_at) THEN
    RAISE EXCEPTION 'ready_asset_content_immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.state <> OLD.state AND NOT (
    (OLD.state='pending_upload' AND NEW.state IN ('uploaded','expired','deleting')) OR
    (OLD.state='uploaded' AND NEW.state IN ('verifying','deleting')) OR
    (OLD.state='verifying' AND NEW.state IN ('ready','rejected','deleting')) OR
    (OLD.state IN ('ready','rejected','expired') AND NEW.state='deleting') OR
    (OLD.state='deleting' AND NEW.state='deleted')) THEN
    RAISE EXCEPTION 'asset_state_transition' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER assets_immutable_content BEFORE UPDATE OR DELETE ON assets
  FOR EACH ROW EXECUTE FUNCTION penforge_asset_guard();

CREATE FUNCTION penforge_export_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    IF NOT EXISTS (SELECT 1 FROM projects WHERE id=OLD.project_id) THEN RETURN OLD; END IF;
    IF OLD.status IN ('ready','failed') THEN
      RAISE EXCEPTION 'final_export_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status IN ('ready','failed') THEN
    -- FK SET NULL is permissible only during deletion of the referenced job.
    IF NOT (NEW.job_id IS NULL AND OLD.job_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM jobs WHERE id=OLD.job_id)
      AND to_jsonb(NEW)-'job_id' = to_jsonb(OLD)-'job_id') THEN
      RAISE EXCEPTION 'final_export_immutable' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER font_exports_immutable_after_final BEFORE UPDATE OR DELETE ON font_exports
  FOR EACH ROW EXECUTE FUNCTION penforge_export_guard();

CREATE FUNCTION penforge_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.state <> OLD.state AND NOT (
    (OLD.state='queued' AND NEW.state IN ('running','canceled')) OR
    (OLD.state='running' AND NEW.state IN ('succeeded','failed','queued','cancel_requested')) OR
    (OLD.state='cancel_requested' AND NEW.state='canceled')) THEN
    RAISE EXCEPTION 'job_state_transition' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER jobs_terminal_states_final BEFORE UPDATE ON jobs
  FOR EACH ROW EXECUTE FUNCTION penforge_job_guard();

CREATE FUNCTION penforge_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_append_only' USING ERRCODE = '42501';
END $$;
CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION penforge_audit_guard();
REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM PUBLIC;

CREATE FUNCTION penforge_glyph_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.project_id,NEW.codepoints,NEW.variant_index) IS DISTINCT FROM
     (OLD.project_id,OLD.codepoints,OLD.variant_index) THEN
    RAISE EXCEPTION 'glyph_identity_immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER glyphs_identity_immutable BEFORE UPDATE ON glyphs
  FOR EACH ROW EXECUTE FUNCTION penforge_glyph_identity_guard();

-- Deferred checks see final transaction state, permitting atomic ownership claims and FK cycles.
CREATE FUNCTION penforge_reference_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
    )) THEN RAISE EXCEPTION 'asset_duplicate_mismatch' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='samples' THEN
    SELECT * INTO r FROM samples WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM assets WHERE id=r.asset_id AND project_id=r.project_id) THEN
      RAISE EXCEPTION 'sample_asset_project_mismatch' USING ERRCODE='23514'; END IF;
    IF r.consent_id_at_upload IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM training_consents c JOIN projects p ON p.owner_user_id=c.user_id
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
CREATE CONSTRAINT TRIGGER glyphs_active_revision_must_be_approved AFTER INSERT OR UPDATE ON glyphs
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER projects_reference_consistency AFTER INSERT OR UPDATE ON projects
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER assets_reference_consistency AFTER INSERT OR UPDATE ON assets
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER samples_reference_consistency AFTER INSERT OR UPDATE ON samples
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER revisions_reference_consistency AFTER INSERT OR UPDATE ON glyph_revisions
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER snapshots_reference_consistency AFTER INSERT OR UPDATE ON project_snapshots
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER jobs_reference_consistency AFTER INSERT OR UPDATE ON jobs
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER exports_reference_consistency AFTER INSERT OR UPDATE ON font_exports
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER styles_reference_consistency AFTER INSERT OR UPDATE ON style_profiles
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER users_merge_consistency AFTER INSERT OR UPDATE ON users
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER reservations_reference_consistency AFTER INSERT OR UPDATE ON quota_reservations
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER contributions_reference_consistency AFTER INSERT OR UPDATE ON dataset_contributions
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
CREATE CONSTRAINT TRIGGER consents_reference_consistency AFTER INSERT OR UPDATE ON training_consents
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_reference_guard();
