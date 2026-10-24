-- Frozen SQLAlchemy baseline snapshot; see 0001_initial.

CREATE TABLE users (
	id UUID NOT NULL, 
	firebase_uid TEXT NOT NULL, 
	is_anonymous BOOLEAN DEFAULT true NOT NULL, 
	account_state TEXT DEFAULT 'active' NOT NULL, 
	tier TEXT DEFAULT 'free_guest' NOT NULL, 
	merged_into_user_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT uq_users_firebase_uid UNIQUE (firebase_uid), 
	CONSTRAINT ck_users_account_state_values CHECK (account_state IN ('active','suspended','deleting')), 
	CONSTRAINT ck_users_no_self_merge CHECK (merged_into_user_id IS NULL OR merged_into_user_id <> id)
);

CREATE TABLE guest_usage (
	user_id UUID NOT NULL, 
	creations_used INTEGER DEFAULT 0 NOT NULL, 
	creations_limit INTEGER DEFAULT 3 NOT NULL, 
	first_seen_ip_hash BYTEA, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_guest_usage PRIMARY KEY (user_id), 
	CONSTRAINT ck_guest_usage_creations CHECK (creations_used >= 0 AND creations_limit >= 0 AND creations_used <= creations_limit), 
	CONSTRAINT ck_guest_usage_first_seen_ip_hash_length CHECK (first_seen_ip_hash IS NULL OR octet_length(first_seen_ip_hash) = 32)
);

CREATE TABLE projects (
	id UUID NOT NULL, 
	owner_user_id UUID NOT NULL, 
	name TEXT NOT NULL, 
	script_code TEXT NOT NULL, 
	status TEXT DEFAULT 'draft' NOT NULL, 
	active_snapshot_id UUID, 
	style_controls JSONB DEFAULT '{}'::jsonb NOT NULL, 
	retention_policy TEXT DEFAULT 'standard' NOT NULL, 
	row_version INTEGER DEFAULT 1 NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_projects PRIMARY KEY (id), 
	CONSTRAINT ck_projects_name_length CHECK (length(name) BETWEEN 1 AND 120), 
	CONSTRAINT ck_projects_script_code_format CHECK (script_code ~ '^[A-Z][a-z]{3}$'), 
	CONSTRAINT ck_projects_status_values CHECK (status IN ('draft','processing','ready','archived','deleting')), 
	CONSTRAINT ck_projects_row_version CHECK (row_version > 0)
);

CREATE TABLE assets (
	id UUID NOT NULL, 
	owner_user_id UUID NOT NULL, 
	project_id UUID, 
	kind TEXT NOT NULL, 
	storage_backend TEXT NOT NULL, 
	storage_key TEXT NOT NULL, 
	mime_type TEXT NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	sha256 BYTEA, 
	width_px INTEGER, 
	height_px INTEGER, 
	state TEXT DEFAULT 'pending_upload' NOT NULL, 
	reject_reason TEXT, 
	duplicate_of UUID, 
	retention_class TEXT NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	is_immutable BOOLEAN DEFAULT true NOT NULL, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_assets PRIMARY KEY (id), 
	CONSTRAINT ck_assets_kind_values CHECK (kind IN ('original_upload','preprocessed','segment','vector','checkpoint','export','validation_report','thumbnail','style_embedding')), 
	CONSTRAINT ck_assets_state_values CHECK (state IN ('pending_upload','uploaded','verifying','ready','rejected','expired','deleting','deleted')), 
	CONSTRAINT ck_assets_size_bytes CHECK (size_bytes >= 0), 
	CONSTRAINT ck_assets_sha256_length CHECK (sha256 IS NULL OR octet_length(sha256) = 32), 
	CONSTRAINT ck_assets_width_px CHECK (width_px IS NULL OR width_px > 0), 
	CONSTRAINT ck_assets_height_px CHECK (height_px IS NULL OR height_px > 0), 
	CONSTRAINT ck_assets_ready_verified CHECK (state <> 'ready' OR (sha256 IS NOT NULL AND verified_at IS NOT NULL)), 
	CONSTRAINT uq_assets_storage_key UNIQUE (storage_key)
);

CREATE TABLE samples (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	asset_id UUID NOT NULL, 
	source_type TEXT NOT NULL, 
	template_id TEXT, 
	template_version TEXT, 
	page_index INTEGER DEFAULT 0 NOT NULL, 
	state TEXT DEFAULT 'pending' NOT NULL, 
	quality JSONB DEFAULT '{}'::jsonb NOT NULL, 
	quality_score REAL, 
	quality_reasons TEXT[] DEFAULT '{}' NOT NULL, 
	consent_id_at_upload UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_samples PRIMARY KEY (id), 
	CONSTRAINT ck_samples_source_type_values CHECK (source_type IN ('template_scan','template_photo','freeform_photo','canvas_drawing')), 
	CONSTRAINT ck_samples_state_values CHECK (state IN ('pending','analyzed','needs_better_input','rejected')), 
	CONSTRAINT ck_samples_page_index CHECK (page_index >= 0), 
	CONSTRAINT ck_samples_template_identity CHECK ((template_id IS NULL) = (template_version IS NULL)), 
	CONSTRAINT ck_samples_quality_score CHECK (quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1))
);

CREATE TABLE glyphs (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	codepoints INTEGER[] NOT NULL, 
	variant_index SMALLINT DEFAULT 0 NOT NULL, 
	glyph_name TEXT, 
	coverage_status TEXT DEFAULT 'missing' NOT NULL, 
	active_revision_id UUID, 
	row_version INTEGER DEFAULT 1 NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_glyphs PRIMARY KEY (id), 
	CONSTRAINT ck_glyphs_codepoints_scalar CHECK (penforge_unicode_scalars(codepoints)), 
	CONSTRAINT ck_glyphs_variant_index CHECK (variant_index >= 0), 
	CONSTRAINT ck_glyphs_row_version CHECK (row_version > 0), 
	CONSTRAINT ck_glyphs_coverage_status_values CHECK (coverage_status IN ('missing','captured','needs_review','generated_pending','approved','excluded')), 
	CONSTRAINT uq_glyphs_project_id UNIQUE (project_id, codepoints, variant_index)
);

CREATE TABLE model_versions (
	id UUID NOT NULL, 
	name TEXT NOT NULL, 
	version TEXT NOT NULL, 
	task TEXT NOT NULL, 
	script_code TEXT, 
	format TEXT NOT NULL, 
	artifact_key TEXT NOT NULL, 
	sha256 BYTEA NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	input_schema JSONB NOT NULL, 
	output_schema JSONB NOT NULL, 
	compatible_worker TEXT NOT NULL, 
	resource_profile JSONB DEFAULT '{}'::jsonb NOT NULL, 
	license TEXT NOT NULL, 
	provenance JSONB DEFAULT '{}'::jsonb NOT NULL, 
	benchmark JSONB DEFAULT '{}'::jsonb NOT NULL, 
	status TEXT DEFAULT 'candidate' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_model_versions PRIMARY KEY (id), 
	CONSTRAINT ck_model_versions_task_values CHECK (task IN ('segmentation','recognition','generation','style_encoder','refiner','rules')), 
	CONSTRAINT ck_model_versions_format_values CHECK (format IN ('onnx','safetensors','json_rules')), 
	CONSTRAINT ck_model_versions_status_values CHECK (status IN ('candidate','approved','deprecated','revoked')), 
	CONSTRAINT ck_model_versions_sha256_length CHECK (sha256 IS NULL OR octet_length(sha256) = 32), 
	CONSTRAINT ck_model_versions_size_bytes CHECK (size_bytes >= 0), 
	CONSTRAINT ck_model_versions_script_code_format CHECK (script_code IS NULL OR script_code ~ '^[A-Z][a-z]{3}$'), 
	CONSTRAINT uq_model_versions_name UNIQUE (name, version)
);

CREATE TABLE glyph_revisions (
	id UUID NOT NULL, 
	glyph_id UUID NOT NULL, 
	seq BIGINT NOT NULL, 
	parent_revision_id UUID, 
	origin TEXT NOT NULL, 
	approval TEXT NOT NULL, 
	outline_inline JSONB, 
	outline_asset_id UUID, 
	metrics JSONB DEFAULT '{}'::jsonb NOT NULL, 
	content_hash BYTEA NOT NULL, 
	source_sample_id UUID, 
	confidence REAL, 
	quality JSONB, 
	model_version_id UUID, 
	job_id UUID, 
	created_by_user_id UUID, 
	approved_by_user_id UUID, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_glyph_revisions PRIMARY KEY (id), 
	CONSTRAINT ck_glyph_revisions_origin_values CHECK (origin IN ('auto_extract','user_capture','user_edit','ml_generated','restored')), 
	CONSTRAINT ck_glyph_revisions_approval_values CHECK (approval IN ('approved','pending','rejected')), 
	CONSTRAINT ck_glyph_revisions_seq CHECK (seq > 0), 
	CONSTRAINT ck_glyph_revisions_outline_source CHECK ((outline_inline IS NOT NULL) <> (outline_asset_id IS NOT NULL)), 
	CONSTRAINT ck_glyph_revisions_outline_inline_size CHECK (outline_inline IS NULL OR (jsonb_typeof(outline_inline) = 'object' AND octet_length(outline_inline::text) <= 32768)), 
	CONSTRAINT ck_glyph_revisions_content_hash_length CHECK (content_hash IS NULL OR octet_length(content_hash) = 32), 
	CONSTRAINT ck_glyph_revisions_confidence CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), 
	CONSTRAINT ck_glyph_revisions_approval_evidence CHECK ((approval = 'approved') = (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)), 
	CONSTRAINT ck_glyph_revisions_approval_pair CHECK ((approved_by_user_id IS NULL) = (approved_at IS NULL)), 
	CONSTRAINT uq_glyph_revisions_glyph_id UNIQUE (glyph_id, seq)
);

CREATE TABLE project_snapshots (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	kind TEXT NOT NULL, 
	label TEXT, 
	manifest JSONB NOT NULL, 
	manifest_sha256 BYTEA NOT NULL, 
	created_by_user_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_project_snapshots PRIMARY KEY (id), 
	CONSTRAINT ck_project_snapshots_kind_values CHECK (kind IN ('milestone','export','auto')), 
	CONSTRAINT ck_project_snapshots_manifest_sha256_length CHECK (manifest_sha256 IS NULL OR octet_length(manifest_sha256) = 32)
);

CREATE TABLE style_profiles (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	metrics JSONB NOT NULL, 
	embedding_asset_id UUID, 
	reference_glyph_ids UUID[] DEFAULT '{}' NOT NULL, 
	model_version_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_style_profiles PRIMARY KEY (id), 
	CONSTRAINT ck_style_profiles_version CHECK (version > 0), 
	CONSTRAINT uq_style_profiles_project_id UNIQUE (project_id, version)
);

CREATE TABLE jobs (
	id UUID NOT NULL, 
	project_id UUID, 
	owner_user_id UUID, 
	job_type TEXT NOT NULL, 
	payload JSONB NOT NULL, 
	payload_schema_version INTEGER NOT NULL, 
	stage TEXT, 
	state TEXT DEFAULT 'queued' NOT NULL, 
	priority SMALLINT DEFAULT 0 NOT NULL, 
	attempts INTEGER DEFAULT 0 NOT NULL, 
	max_attempts INTEGER DEFAULT 3 NOT NULL, 
	run_after TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	lease_holder TEXT, 
	lease_expires_at TIMESTAMP WITH TIME ZONE, 
	last_heartbeat_at TIMESTAMP WITH TIME ZONE, 
	progress REAL DEFAULT 0 NOT NULL, 
	checkpoint JSONB DEFAULT '{}'::jsonb NOT NULL, 
	cancel_requested BOOLEAN DEFAULT false NOT NULL, 
	idempotency_key TEXT, 
	resource_profile JSONB DEFAULT '{}'::jsonb NOT NULL, 
	usage JSONB DEFAULT '{}'::jsonb NOT NULL, 
	error_code TEXT, 
	error_detail TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_jobs PRIMARY KEY (id), 
	CONSTRAINT ck_jobs_state_values CHECK (state IN ('queued','running','cancel_requested','succeeded','failed','canceled')), 
	CONSTRAINT ck_jobs_progress CHECK (progress BETWEEN 0 AND 1), 
	CONSTRAINT ck_jobs_attempts CHECK (attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts), 
	CONSTRAINT ck_jobs_payload_schema_version CHECK (payload_schema_version > 0)
);

CREATE TABLE job_events (
	id BIGSERIAL NOT NULL, 
	job_id UUID NOT NULL, 
	kind TEXT NOT NULL, 
	from_state TEXT, 
	to_state TEXT, 
	stage TEXT, 
	progress REAL, 
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_job_events PRIMARY KEY (id), 
	CONSTRAINT ck_job_events_kind_values CHECK (kind IN ('state','stage','progress','warning','error')), 
	CONSTRAINT ck_job_events_progress CHECK (progress IS NULL OR progress BETWEEN 0 AND 1)
);

CREATE TABLE quota_balances (
	id UUID NOT NULL, 
	scope TEXT NOT NULL, 
	user_id UUID, 
	resource TEXT NOT NULL, 
	window_kind TEXT NOT NULL, 
	window_start DATE NOT NULL, 
	limit_amount BIGINT NOT NULL, 
	committed BIGINT DEFAULT 0 NOT NULL, 
	reserved BIGINT DEFAULT 0 NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_quota_balances PRIMARY KEY (id), 
	CONSTRAINT ck_quota_balances_scope_values CHECK (scope IN ('user','global')), 
	CONSTRAINT ck_quota_balances_window_kind_values CHECK (window_kind IN ('lifetime','day','month')), 
	CONSTRAINT ck_quota_balances_nonnegative CHECK (limit_amount >= 0 AND committed >= 0 AND reserved >= 0), 
	CONSTRAINT ck_quota_balances_scope_owner CHECK ((scope = 'user') = (user_id IS NOT NULL))
);

CREATE TABLE quota_reservations (
	id UUID NOT NULL, 
	balance_id UUID NOT NULL, 
	user_id UUID, 
	job_id UUID, 
	resource TEXT NOT NULL, 
	amount BIGINT NOT NULL, 
	status TEXT DEFAULT 'reserved' NOT NULL, 
	committed_amount BIGINT, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	settled_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_quota_reservations PRIMARY KEY (id), 
	CONSTRAINT ck_quota_reservations_amount CHECK (amount > 0), 
	CONSTRAINT ck_quota_reservations_committed_amount CHECK (committed_amount IS NULL OR committed_amount >= 0), 
	CONSTRAINT ck_quota_reservations_status_values CHECK (status IN ('reserved','committed','released','expired'))
);

CREATE TABLE idempotency_keys (
	user_id UUID NOT NULL, 
	key TEXT NOT NULL, 
	request_hash BYTEA NOT NULL, 
	state TEXT NOT NULL, 
	response_status INTEGER, 
	response_body JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_idempotency_keys PRIMARY KEY (user_id, key), 
	CONSTRAINT ck_idempotency_keys_state_values CHECK (state IN ('in_progress','completed')), 
	CONSTRAINT ck_idempotency_keys_request_hash_length CHECK (request_hash IS NULL OR octet_length(request_hash) = 32), 
	CONSTRAINT ck_idempotency_keys_response_status CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599), 
	CONSTRAINT ck_idempotency_keys_response_body_size CHECK (response_body IS NULL OR octet_length(response_body::text) <= 32768)
);

CREATE TABLE system_settings (
	key TEXT NOT NULL, 
	value JSONB NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_system_settings PRIMARY KEY (key)
);

CREATE TABLE font_exports (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	snapshot_id UUID NOT NULL, 
	job_id UUID, 
	requested_by_user_id UUID NOT NULL, 
	format TEXT NOT NULL, 
	family_name TEXT NOT NULL, 
	style_name TEXT DEFAULT 'Regular' NOT NULL, 
	version_string TEXT NOT NULL, 
	status TEXT DEFAULT 'pending' NOT NULL, 
	asset_id UUID, 
	coverage_report JSONB, 
	validation_status TEXT, 
	validation_report_asset_id UUID, 
	tool_versions JSONB, 
	model_version_ids UUID[] DEFAULT '{}' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finalized_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_font_exports PRIMARY KEY (id), 
	CONSTRAINT ck_font_exports_format_values CHECK (format IN ('ttf','otf','woff2')), 
	CONSTRAINT ck_font_exports_status_values CHECK (status IN ('pending','ready','failed')), 
	CONSTRAINT ck_font_exports_validation_status_values CHECK (validation_status IN ('passed','passed_with_warnings','failed')), 
	CONSTRAINT ck_font_exports_finalized_at CHECK (status = 'pending' OR finalized_at IS NOT NULL), 
	CONSTRAINT ck_font_exports_ready_validated CHECK (status <> 'ready' OR (asset_id IS NOT NULL AND validation_status IN ('passed','passed_with_warnings'))), 
	CONSTRAINT uq_font_exports_asset_id UNIQUE (asset_id)
);

CREATE TABLE training_consents (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	purpose TEXT NOT NULL, 
	policy_version TEXT NOT NULL, 
	granted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	withdrawn_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_training_consents PRIMARY KEY (id), 
	CONSTRAINT ck_training_consents_purpose_values CHECK (purpose IN ('model_training'))
);

CREATE TABLE dataset_contributions (
	id UUID NOT NULL, 
	sample_id UUID, 
	contributor_ref BYTEA NOT NULL, 
	consent_id UUID, 
	dataset_version TEXT, 
	training_run_ids TEXT[] DEFAULT '{}' NOT NULL, 
	status TEXT DEFAULT 'active' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	withdrawn_at TIMESTAMP WITH TIME ZONE, 
	removed_from_dataset_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_dataset_contributions PRIMARY KEY (id), 
	CONSTRAINT ck_dataset_contributions_status_values CHECK (status IN ('active','withdrawn','removed_from_dataset')), 
	CONSTRAINT ck_dataset_contributions_contributor_ref_length CHECK (contributor_ref IS NULL OR octet_length(contributor_ref) = 32)
);

CREATE TABLE deletion_requests (
	id UUID NOT NULL, 
	subject_ref BYTEA NOT NULL, 
	user_id UUID, 
	scope TEXT NOT NULL, 
	project_id UUID, 
	state TEXT DEFAULT 'requested' NOT NULL, 
	progress JSONB DEFAULT '{}'::jsonb NOT NULL, 
	evidence JSONB, 
	error_code TEXT, 
	requested_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_deletion_requests PRIMARY KEY (id), 
	CONSTRAINT ck_deletion_requests_scope_values CHECK (scope IN ('project','account','contributions_only')), 
	CONSTRAINT ck_deletion_requests_state_values CHECK (state IN ('requested','in_progress','completed','failed')), 
	CONSTRAINT ck_deletion_requests_subject_ref_length CHECK (subject_ref IS NULL OR octet_length(subject_ref) = 32)
);

CREATE TABLE audit_events (
	id BIGSERIAL NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	actor_ref TEXT NOT NULL, 
	action TEXT NOT NULL, 
	resource_type TEXT NOT NULL, 
	resource_id UUID, 
	outcome TEXT NOT NULL, 
	request_id TEXT, 
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_audit_events PRIMARY KEY (id), 
	CONSTRAINT ck_audit_events_outcome_values CHECK (outcome IN ('success','failure'))
);

ALTER TABLE users ADD CONSTRAINT fk_users_merged_into_user_id_users FOREIGN KEY(merged_into_user_id) REFERENCES users (id) ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE guest_usage ADD CONSTRAINT fk_guest_usage_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE projects ADD CONSTRAINT fk_projects_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT;

ALTER TABLE projects ADD CONSTRAINT projects_active_snapshot_fk FOREIGN KEY(active_snapshot_id) REFERENCES project_snapshots (id) ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX projects_owner_idx ON projects (owner_user_id, updated_at DESC);

ALTER TABLE assets ADD CONSTRAINT fk_assets_duplicate_of_assets FOREIGN KEY(duplicate_of) REFERENCES assets (id) ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE assets ADD CONSTRAINT fk_assets_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT;

ALTER TABLE assets ADD CONSTRAINT fk_assets_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE;

CREATE INDEX assets_expiry_idx ON assets (expires_at) WHERE state IN ('pending_upload','ready') AND expires_at IS NOT NULL;

CREATE UNIQUE INDEX assets_owner_sha_uniq ON assets (owner_user_id, sha256) WHERE kind = 'original_upload' AND state = 'ready';

CREATE INDEX assets_project_idx ON assets (project_id, kind) WHERE state <> 'deleted';

ALTER TABLE samples ADD CONSTRAINT fk_samples_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE NO ACTION;

ALTER TABLE samples ADD CONSTRAINT fk_samples_consent_id_at_upload_training_consents FOREIGN KEY(consent_id_at_upload) REFERENCES training_consents (id) ON DELETE SET NULL;

ALTER TABLE samples ADD CONSTRAINT fk_samples_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE;

CREATE INDEX samples_project_idx ON samples (project_id);

ALTER TABLE glyphs ADD CONSTRAINT fk_glyphs_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE;

ALTER TABLE glyphs ADD CONSTRAINT glyphs_active_rev_fk FOREIGN KEY(active_revision_id) REFERENCES glyph_revisions (id) ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX glyphs_project_status_idx ON glyphs (project_id, coverage_status);

ALTER TABLE glyph_revisions ADD CONSTRAINT fk_glyph_revisions_approved_by_user_id_users FOREIGN KEY(approved_by_user_id) REFERENCES users (id) ON DELETE NO ACTION;

ALTER TABLE glyph_revisions ADD CONSTRAINT fk_glyph_revisions_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE NO ACTION;

ALTER TABLE glyph_revisions ADD CONSTRAINT fk_glyph_revisions_glyph_id_glyphs FOREIGN KEY(glyph_id) REFERENCES glyphs (id) ON DELETE CASCADE;

ALTER TABLE glyph_revisions ADD CONSTRAINT fk_glyph_revisions_model_version_id_model_versions FOREIGN KEY(model_version_id) REFERENCES model_versions (id) ON DELETE NO ACTION;

ALTER TABLE glyph_revisions ADD CONSTRAINT fk_glyph_revisions_outline_asset_id_assets FOREIGN KEY(outline_asset_id) REFERENCES assets (id) ON DELETE NO ACTION;

ALTER TABLE glyph_revisions ADD CONSTRAINT fk_glyph_revisions_parent_revision_id_glyph_revisions FOREIGN KEY(parent_revision_id) REFERENCES glyph_revisions (id) ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE glyph_revisions ADD CONSTRAINT fk_glyph_revisions_source_sample_id_samples FOREIGN KEY(source_sample_id) REFERENCES samples (id) ON DELETE SET NULL;

ALTER TABLE glyph_revisions ADD CONSTRAINT glyph_revisions_job_fk FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE SET NULL;

CREATE INDEX glyph_revisions_glyph_idx ON glyph_revisions (glyph_id, seq DESC);

ALTER TABLE project_snapshots ADD CONSTRAINT fk_project_snapshots_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE NO ACTION;

ALTER TABLE project_snapshots ADD CONSTRAINT fk_project_snapshots_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE;

CREATE INDEX snapshots_project_idx ON project_snapshots (project_id, created_at DESC);

ALTER TABLE style_profiles ADD CONSTRAINT fk_style_profiles_embedding_asset_id_assets FOREIGN KEY(embedding_asset_id) REFERENCES assets (id) ON DELETE NO ACTION;

ALTER TABLE style_profiles ADD CONSTRAINT fk_style_profiles_model_version_id_model_versions FOREIGN KEY(model_version_id) REFERENCES model_versions (id) ON DELETE NO ACTION;

ALTER TABLE style_profiles ADD CONSTRAINT fk_style_profiles_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE;

ALTER TABLE jobs ADD CONSTRAINT fk_jobs_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT;

ALTER TABLE jobs ADD CONSTRAINT fk_jobs_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE;

CREATE INDEX jobs_claim_idx ON jobs (priority DESC, run_after, created_at) WHERE state = 'queued';

CREATE UNIQUE INDEX jobs_idem_uniq ON jobs (owner_user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE INDEX jobs_lease_idx ON jobs (lease_expires_at) WHERE state IN ('running','cancel_requested');

CREATE INDEX jobs_owner_state_idx ON jobs (owner_user_id, state);

CREATE INDEX jobs_project_idx ON jobs (project_id, created_at DESC);

ALTER TABLE job_events ADD CONSTRAINT fk_job_events_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE;

CREATE INDEX job_events_job_idx ON job_events (job_id, id);

ALTER TABLE quota_balances ADD CONSTRAINT fk_quota_balances_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE UNIQUE INDEX quota_balances_uniq ON quota_balances (scope, user_id, resource, window_kind, window_start) NULLS NOT DISTINCT;

ALTER TABLE quota_reservations ADD CONSTRAINT fk_quota_reservations_balance_id_quota_balances FOREIGN KEY(balance_id) REFERENCES quota_balances (id) ON DELETE CASCADE;

ALTER TABLE quota_reservations ADD CONSTRAINT fk_quota_reservations_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE SET NULL;

ALTER TABLE quota_reservations ADD CONSTRAINT fk_quota_reservations_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT;

CREATE INDEX quota_res_expiry_idx ON quota_reservations (expires_at) WHERE status = 'reserved';

CREATE INDEX quota_res_job_idx ON quota_reservations (job_id);

ALTER TABLE idempotency_keys ADD CONSTRAINT fk_idempotency_keys_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE INDEX idempotency_expiry_idx ON idempotency_keys (expires_at);

ALTER TABLE font_exports ADD CONSTRAINT fk_font_exports_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE NO ACTION;

ALTER TABLE font_exports ADD CONSTRAINT fk_font_exports_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE SET NULL;

ALTER TABLE font_exports ADD CONSTRAINT fk_font_exports_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE;

ALTER TABLE font_exports ADD CONSTRAINT fk_font_exports_requested_by_user_id_users FOREIGN KEY(requested_by_user_id) REFERENCES users (id) ON DELETE NO ACTION;

ALTER TABLE font_exports ADD CONSTRAINT fk_font_exports_snapshot_id_project_snapshots FOREIGN KEY(snapshot_id) REFERENCES project_snapshots (id) ON DELETE NO ACTION;

ALTER TABLE font_exports ADD CONSTRAINT fk_font_exports_validation_report_asset_id_assets FOREIGN KEY(validation_report_asset_id) REFERENCES assets (id) ON DELETE NO ACTION;

ALTER TABLE training_consents ADD CONSTRAINT fk_training_consents_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE UNIQUE INDEX training_consents_active_uniq ON training_consents (user_id, purpose) WHERE withdrawn_at IS NULL;

ALTER TABLE dataset_contributions ADD CONSTRAINT fk_dataset_contributions_consent_id_training_consents FOREIGN KEY(consent_id) REFERENCES training_consents (id) ON DELETE SET NULL;

ALTER TABLE dataset_contributions ADD CONSTRAINT fk_dataset_contributions_sample_id_samples FOREIGN KEY(sample_id) REFERENCES samples (id) ON DELETE SET NULL;
