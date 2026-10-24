"""SQLAlchemy Core baseline from v3 Part B §7.3, strengthened by ADR 0002.

PostgreSQL is intentional: arrays, JSONB, deferred FK cycles, partial indexes,
NULLS NOT DISTINCT and database triggers are part of the consistency contract.
These tables do not implement feature APIs, identity provisioning or a queue loop.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def col(
    name: str,
    type_: Any,
    *,
    nullable: bool = False,
    default: str | None = None,
    primary: bool = False,
) -> sa.Column[Any]:
    return sa.Column(
        name,
        type_,
        nullable=nullable,
        primary_key=primary,
        server_default=sa.text(default) if default is not None else None,
    )


def uid(name: str = "id", *, nullable: bool = False, primary: bool = False) -> sa.Column[Any]:
    return col(name, UUID(as_uuid=True), nullable=nullable, primary=primary or name == "id")


def text(name: str, *, nullable: bool = False, default: str | None = None) -> sa.Column[Any]:
    return col(name, sa.Text, nullable=nullable, default=default)


def stamp(
    name: str = "created_at", *, nullable: bool = False, default: str | None = "now()"
) -> sa.Column[Any]:
    return col(name, sa.DateTime(timezone=True), nullable=nullable, default=default)


def js(name: str, *, nullable: bool = False, default: str | None = "'{}'::jsonb") -> sa.Column[Any]:
    # Nullable outline sources use SQL NULL, not JSON's null value, so the
    # inline-versus-artifact XOR and size guards retain their intended meaning.
    kind = JSONB(none_as_null=True) if name == "outline_inline" else JSONB()
    return col(name, kind, nullable=nullable, default=default)


def check(expression: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expression, name=name)


def choices(name: str, values: str) -> sa.CheckConstraint:
    return check(f"{name} IN ({','.join(repr(v) for v in values.split())})", name + "_values")


def fk(
    source: str,
    target: str,
    *,
    delete: str = "NO ACTION",
    deferred: bool = False,
    name: str | None = None,
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [source],
        [target],
        name=name,
        ondelete=delete,
        use_alter=source in {"active_revision_id", "active_snapshot_id"},
        deferrable=True if deferred else None,
        initially="DEFERRED" if deferred else None,
    )


def digest(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return col(name, sa.LargeBinary, nullable=nullable)


def digest_check(name: str) -> sa.CheckConstraint:
    return check(f"{name} IS NULL OR octet_length({name}) = 32", name + "_length")


users = sa.Table(
    "users",
    metadata,
    uid(),
    text("firebase_uid"),
    col("is_anonymous", sa.Boolean, default="true"),
    text("account_state", default="'active'"),
    text("tier", default="'free_guest'"),
    uid("merged_into_user_id", nullable=True),
    stamp(),
    stamp("updated_at"),
    stamp("last_seen_at"),
    sa.UniqueConstraint("firebase_uid"),
    choices("account_state", "active suspended deleting"),
    check("merged_into_user_id IS NULL OR merged_into_user_id <> id", "no_self_merge"),
    fk("merged_into_user_id", "users.id", deferred=True),
)
guest_usage = sa.Table(
    "guest_usage",
    metadata,
    uid("user_id", primary=True),
    col("creations_used", sa.Integer, default="0"),
    col("creations_limit", sa.Integer, default="3"),
    digest("first_seen_ip_hash", nullable=True),
    stamp(),
    stamp("updated_at"),
    check(
        "creations_used >= 0 AND creations_limit >= 0 AND creations_used <= creations_limit",
        "creations",
    ),
    digest_check("first_seen_ip_hash"),
    fk("user_id", "users.id", delete="CASCADE"),
)
projects = sa.Table(
    "projects",
    metadata,
    uid(),
    uid("owner_user_id"),
    text("name"),
    text("script_code"),
    text("required_set_id", nullable=True),
    text("status", default="'draft'"),
    uid("active_snapshot_id", nullable=True),
    js("style_controls"),
    text("retention_policy", default="'standard'"),
    col("row_version", sa.Integer, default="1"),
    stamp(),
    stamp("updated_at"),
    check("length(name) BETWEEN 1 AND 120", "name_length"),
    check("script_code ~ '^[A-Z][a-z]{3}$'", "script_code_format"),
    check(
        "required_set_id IS NULL OR (script_code='Deva' AND required_set_id='deva-tier1-ucd17-v1')",
        "required_set",
    ),
    choices("status", "draft processing ready archived deleting"),
    check("row_version > 0", "row_version"),
    fk("owner_user_id", "users.id", delete="RESTRICT"),
    fk(
        "active_snapshot_id",
        "project_snapshots.id",
        deferred=True,
        name="projects_active_snapshot_fk",
    ),
)
sa.Index("projects_owner_idx", projects.c.owner_user_id, projects.c.updated_at.desc())
sa.Index(
    "projects_owner_cursor_idx",
    projects.c.owner_user_id,
    projects.c.updated_at.desc(),
    projects.c.id.desc(),
)
assets = sa.Table(
    "assets",
    metadata,
    uid(),
    uid("owner_user_id"),
    uid("project_id", nullable=True),
    text("kind"),
    text("storage_backend"),
    text("storage_key"),
    text("source_type", nullable=True),
    digest("declared_sha256", nullable=True),
    digest("document_hash", nullable=True),
    uid("parent_asset_id", nullable=True),
    uid("verify_job_id", nullable=True),
    text("template_id", nullable=True),
    text("template_version", nullable=True),
    digest("template_manifest_sha256", nullable=True),
    col("page_index", sa.Integer, nullable=True),
    digest("upload_capability_hash", nullable=True),
    uid("upload_issued_owner_id", nullable=True),
    digest("download_capability_hash", nullable=True),
    uid("download_issued_owner_id", nullable=True),
    stamp("cleanup_completed_at", nullable=True, default=None),
    text("mime_type"),
    col("size_bytes", sa.BigInteger),
    digest("sha256", nullable=True),
    col("width_px", sa.Integer, nullable=True),
    col("height_px", sa.Integer, nullable=True),
    text("state", default="'pending_upload'"),
    text("reject_reason", nullable=True),
    uid("duplicate_of", nullable=True),
    text("retention_class"),
    stamp("expires_at", nullable=True, default=None),
    col("is_immutable", sa.Boolean, default="true"),
    stamp("verified_at", nullable=True, default=None),
    stamp(),
    stamp("deleted_at", nullable=True, default=None),
    choices(
        "kind",
        "original_upload raw_strokes preprocessed segment vector checkpoint export validation_report thumbnail style_embedding",
    ),
    choices("state", "pending_upload uploaded verifying ready rejected expired deleting deleted"),
    check("size_bytes >= 0", "size_bytes"),
    digest_check("sha256"),
    check("width_px IS NULL OR width_px > 0", "width_px"),
    check("height_px IS NULL OR height_px > 0", "height_px"),
    check("state <> 'ready' OR (sha256 IS NOT NULL AND verified_at IS NOT NULL)", "ready_verified"),
    sa.UniqueConstraint("storage_key"),
    uid("worker_job_id", nullable=True),
    fk("worker_job_id", "jobs.id", delete="SET NULL"),
    fk("owner_user_id", "users.id", delete="RESTRICT"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("duplicate_of", "assets.id", deferred=True),
    fk("parent_asset_id", "assets.id", deferred=True),
    fk("verify_job_id", "jobs.id", delete="SET NULL"),
    fk("upload_issued_owner_id", "users.id", delete="RESTRICT"),
    fk("download_issued_owner_id", "users.id", delete="RESTRICT"),
    digest_check("declared_sha256"),
    digest_check("document_hash"),
    digest_check("template_manifest_sha256"),
    digest_check("upload_capability_hash"),
    digest_check("download_capability_hash"),
    check(
        "source_type IS NULL OR source_type IN ('template_scan','template_photo','freeform_photo','canvas_drawing')",
        "source_type_values",
    ),
    check(
        "(template_id IS NULL) = (template_version IS NULL) AND (template_id IS NULL) = (template_manifest_sha256 IS NULL)",
        "template_identity",
    ),
)
sa.Index(
    "assets_project_idx",
    assets.c.project_id,
    assets.c.kind,
    postgresql_where=sa.text("state <> 'deleted'"),
)
sa.Index(
    "assets_expiry_idx",
    assets.c.expires_at,
    postgresql_where=sa.text("state IN ('pending_upload','ready') AND expires_at IS NOT NULL"),
)
sa.Index(
    "assets_owner_inventory_idx",
    assets.c.owner_user_id,
    assets.c.project_id,
    postgresql_where=sa.text("state <> 'deleted'"),
)
sa.Index(
    "assets_owner_sha_uniq",
    assets.c.owner_user_id,
    assets.c.sha256,
    unique=True,
    postgresql_where=sa.text(
        "kind = 'original_upload' AND state = 'ready' AND duplicate_of IS NULL"
    ),
)
samples = sa.Table(
    "samples",
    metadata,
    uid(),
    uid("project_id"),
    uid("asset_id"),
    text("source_type"),
    text("template_id", nullable=True),
    text("template_version", nullable=True),
    col("page_index", sa.Integer, default="0"),
    digest("source_sha256", nullable=True),
    digest("template_manifest_sha256", nullable=True),
    digest("document_hash", nullable=True),
    text("state", default="'pending'"),
    js("quality"),
    js("prompt_context", nullable=True, default=None),
    col("quality_score", sa.REAL, nullable=True),
    col("quality_reasons", ARRAY(sa.Text), default="'{}'"),
    uid("consent_id_at_upload", nullable=True),
    stamp(),
    choices("source_type", "template_scan template_photo freeform_photo canvas_drawing"),
    choices("state", "pending analyzed needs_better_input rejected"),
    check("page_index >= 0", "page_index"),
    check("(template_id IS NULL) = (template_version IS NULL)", "template_identity"),
    check("quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)", "quality_score"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("asset_id", "assets.id"),
    fk("consent_id_at_upload", "training_consents.id", delete="SET NULL"),
    digest_check("source_sha256"),
    digest_check("template_manifest_sha256"),
    digest_check("document_hash"),
)
sa.Index("samples_project_idx", samples.c.project_id)
glyphs = sa.Table(
    "glyphs",
    metadata,
    uid(),
    uid("project_id"),
    col("codepoints", ARRAY(sa.Integer)),
    col("variant_index", sa.SmallInteger, default="0"),
    text("glyph_name", nullable=True),
    text("coverage_status", default="'missing'"),
    uid("active_revision_id", nullable=True),
    col("row_version", sa.Integer, default="1"),
    stamp(),
    stamp("updated_at"),
    check("penforge_unicode_scalars(codepoints)", "codepoints_scalar"),
    check("variant_index >= 0", "variant_index"),
    check("row_version > 0", "row_version"),
    choices("coverage_status", "missing captured needs_review generated_pending approved excluded"),
    sa.UniqueConstraint("project_id", "codepoints", "variant_index"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("active_revision_id", "glyph_revisions.id", deferred=True, name="glyphs_active_rev_fk"),
)
sa.Index("glyphs_project_status_idx", glyphs.c.project_id, glyphs.c.coverage_status)
model_versions = sa.Table(
    "model_versions",
    metadata,
    uid(),
    text("name"),
    text("version"),
    text("task"),
    text("script_code", nullable=True),
    text("format"),
    text("artifact_key"),
    digest("sha256"),
    col("size_bytes", sa.BigInteger),
    js("input_schema", default=None),
    js("output_schema", default=None),
    text("compatible_worker"),
    js("resource_profile"),
    text("license"),
    js("provenance"),
    js("benchmark"),
    text("status", default="'candidate'"),
    stamp(),
    choices("task", "segmentation recognition generation style_encoder refiner rules"),
    choices("format", "onnx safetensors json_rules"),
    choices("status", "candidate approved deprecated revoked"),
    digest_check("sha256"),
    check("size_bytes >= 0", "size_bytes"),
    check("script_code IS NULL OR script_code ~ '^[A-Z][a-z]{3}$'", "script_code_format"),
    sa.UniqueConstraint("name", "version"),
)
glyph_revisions = sa.Table(
    "glyph_revisions",
    metadata,
    uid(),
    uid("glyph_id"),
    col("seq", sa.BigInteger),
    uid("parent_revision_id", nullable=True),
    uid("alternate_source_revision_id", nullable=True),
    uid("alternate_source_snapshot_id", nullable=True),
    text("origin"),
    text("approval"),
    js("outline_inline", nullable=True, default=None),
    uid("outline_asset_id", nullable=True),
    js("metrics"),
    digest("content_hash"),
    uid("source_sample_id", nullable=True),
    uid("source_candidate_id", nullable=True),
    col("confidence", sa.REAL, nullable=True),
    js("quality", nullable=True, default=None),
    uid("model_version_id", nullable=True),
    uid("job_id", nullable=True),
    uid("created_by_user_id", nullable=True),
    uid("approved_by_user_id", nullable=True),
    stamp("approved_at", nullable=True, default=None),
    stamp(),
    choices("origin", "auto_extract user_capture user_edit ml_generated restored"),
    choices("approval", "approved pending rejected"),
    check("seq > 0", "seq"),
    check("(outline_inline IS NOT NULL) <> (outline_asset_id IS NOT NULL)", "outline_source"),
    check(
        "outline_inline IS NULL OR (jsonb_typeof(outline_inline) = 'object' AND octet_length(outline_inline::text) <= 32768)",
        "outline_inline_size",
    ),
    digest_check("content_hash"),
    check("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", "confidence"),
    check(
        "(approval = 'approved') = (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
        "approval_evidence",
    ),
    check("(approved_by_user_id IS NULL) = (approved_at IS NULL)", "approval_pair"),
    sa.UniqueConstraint("glyph_id", "seq"),
    fk("glyph_id", "glyphs.id", delete="CASCADE"),
    fk("parent_revision_id", "glyph_revisions.id", deferred=True),
    fk("outline_asset_id", "assets.id"),
    fk("source_sample_id", "samples.id", delete="SET NULL"),
    fk("source_candidate_id", "glyph_candidates.id", deferred=True),
    fk("model_version_id", "model_versions.id"),
    fk(
        "alternate_source_revision_id",
        "glyph_revisions.id",
        deferred=True,
        name="alternate_revision_fk",
    ),
    fk(
        "alternate_source_snapshot_id",
        "project_snapshots.id",
        deferred=True,
        name="alternate_snapshot_fk",
    ),
    fk("job_id", "jobs.id", delete="SET NULL", name="glyph_revisions_job_fk"),
    fk("created_by_user_id", "users.id"),
    fk("approved_by_user_id", "users.id"),
)
sa.Index("glyph_revisions_glyph_idx", glyph_revisions.c.glyph_id, glyph_revisions.c.seq.desc())
ml_result_artifacts = sa.Table(
    "ml_result_artifacts",
    metadata,
    uid(),
    uid("project_id"),
    uid("job_id"),
    col("attempt", sa.Integer),
    text("stage"),
    uid("manifest_asset_id"),
    digest("manifest_sha256"),
    js("input_hashes"),
    js("pipeline"),
    stamp(),
    check("attempt > 0", "attempt"),
    digest_check("manifest_sha256"),
    sa.UniqueConstraint("job_id", "stage"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("job_id", "jobs.id"),
    fk("manifest_asset_id", "assets.id"),
)
sa.Index(
    "ml_results_project_idx", ml_result_artifacts.c.project_id, ml_result_artifacts.c.created_at
)
glyph_candidates = sa.Table(
    "glyph_candidates",
    metadata,
    uid(),
    uid("result_artifact_id"),
    uid("project_id"),
    uid("glyph_id"),
    uid("source_sample_id"),
    text("origin"),
    col("rank", sa.Integer),
    js("outline_inline", nullable=True, default=None),
    uid("outline_asset_id", nullable=True),
    js("metrics"),
    digest("content_hash"),
    js("evidence"),
    text("review_status", default="'pending'"),
    stamp(),
    choices(
        "origin",
        "authentic_extraction manual_drawing geometric_reconstruction style_conditioned_generation",
    ),
    choices("review_status", "pending approved rejected"),
    check("rank > 0", "rank"),
    check("(outline_inline IS NOT NULL) <> (outline_asset_id IS NOT NULL)", "outline_source"),
    check(
        "outline_inline IS NULL OR (jsonb_typeof(outline_inline) = 'object' AND octet_length(outline_inline::text) <= 32768)",
        "outline_inline_size",
    ),
    digest_check("content_hash"),
    fk("result_artifact_id", "ml_result_artifacts.id", delete="CASCADE"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("glyph_id", "glyphs.id", delete="CASCADE"),
    fk("source_sample_id", "samples.id"),
    fk("outline_asset_id", "assets.id"),
)
sa.Index(
    "candidates_glyph_rank_idx",
    glyph_candidates.c.glyph_id,
    glyph_candidates.c.rank,
    glyph_candidates.c.id,
)
project_snapshots = sa.Table(
    "project_snapshots",
    metadata,
    uid(),
    uid("project_id"),
    text("kind"),
    text("label", nullable=True),
    js("manifest", default=None),
    digest("manifest_sha256"),
    uid("created_by_user_id", nullable=True),
    stamp(),
    choices("kind", "milestone export auto"),
    digest_check("manifest_sha256"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("created_by_user_id", "users.id"),
)
sa.Index(
    "snapshots_project_idx", project_snapshots.c.project_id, project_snapshots.c.created_at.desc()
)
style_profiles = sa.Table(
    "style_profiles",
    metadata,
    uid(),
    uid("project_id"),
    col("version", sa.Integer),
    js("metrics", default=None),
    uid("embedding_asset_id", nullable=True),
    col("reference_glyph_ids", ARRAY(UUID), default="'{}'"),
    uid("model_version_id", nullable=True),
    uid("source_snapshot_id", nullable=True),
    digest("profile_sha256", nullable=True),
    text("algorithm_version", nullable=True),
    uid("created_by_user_id", nullable=True),
    stamp(),
    check("version > 0", "version"),
    sa.UniqueConstraint("project_id", "version"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("embedding_asset_id", "assets.id"),
    fk("model_version_id", "model_versions.id"),
    fk(
        "source_snapshot_id",
        "project_snapshots.id",
        deferred=True,
        name="styles_source_snapshot_fk",
    ),
    fk("created_by_user_id", "users.id", name="styles_creator_fk"),
    check("profile_sha256 IS NULL OR octet_length(profile_sha256)=32", "styles_profile_sha256"),
)
sa.Index(
    "styles_identity_idx", style_profiles.c.project_id, style_profiles.c.profile_sha256, unique=True
)
jobs = sa.Table(
    "jobs",
    metadata,
    uid(),
    uid("project_id", nullable=True),
    uid("owner_user_id", nullable=True),
    text("job_type"),
    js("payload", default=None),
    col("payload_schema_version", sa.Integer),
    text("stage", nullable=True),
    text("state", default="'queued'"),
    col("priority", sa.SmallInteger, default="0"),
    col("attempts", sa.Integer, default="0"),
    col("max_attempts", sa.Integer, default="3"),
    stamp("run_after"),
    text("lease_holder", nullable=True),
    stamp("lease_expires_at", nullable=True, default=None),
    stamp("last_heartbeat_at", nullable=True, default=None),
    col("progress", sa.REAL, default="0"),
    js("checkpoint"),
    col("cancel_requested", sa.Boolean, default="false"),
    text("idempotency_key", nullable=True),
    uid("idempotency_scope_user_id", nullable=True),
    js("resource_profile"),
    js("usage"),
    text("error_code", nullable=True),
    text("error_detail", nullable=True),
    stamp(),
    stamp("started_at", nullable=True, default=None),
    stamp("finished_at", nullable=True, default=None),
    choices("state", "queued running cancel_requested succeeded failed canceled"),
    check("progress BETWEEN 0 AND 1", "progress"),
    check("attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts", "attempts"),
    check("payload_schema_version > 0", "payload_schema_version"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("owner_user_id", "users.id", delete="RESTRICT"),
    fk("idempotency_scope_user_id", "users.id", delete="RESTRICT"),
)
sa.Index(
    "jobs_idem_uniq",
    jobs.c.idempotency_scope_user_id,
    jobs.c.idempotency_key,
    unique=True,
    postgresql_where=sa.text("idempotency_key IS NOT NULL"),
)
sa.Index(
    "jobs_claim_idx",
    jobs.c.priority.desc(),
    jobs.c.run_after,
    jobs.c.created_at,
    postgresql_where=sa.text("state = 'queued'"),
)
sa.Index(
    "jobs_lease_idx",
    jobs.c.lease_expires_at,
    postgresql_where=sa.text("state IN ('running','cancel_requested')"),
)
sa.Index("jobs_project_idx", jobs.c.project_id, jobs.c.created_at.desc())
sa.Index("jobs_owner_state_idx", jobs.c.owner_user_id, jobs.c.state)
job_events = sa.Table(
    "job_events",
    metadata,
    col("id", sa.BigInteger, primary=True),
    uid("job_id"),
    text("kind"),
    text("from_state", nullable=True),
    text("to_state", nullable=True),
    text("stage", nullable=True),
    col("progress", sa.REAL, nullable=True),
    js("metadata"),
    stamp(),
    js("state_snapshot", nullable=True, default=None),
    choices("kind", "state stage progress warning error"),
    check("progress IS NULL OR progress BETWEEN 0 AND 1", "progress"),
    fk("job_id", "jobs.id", delete="CASCADE"),
)
sa.Index("job_events_job_idx", job_events.c.job_id, job_events.c.id)
quota_balances = sa.Table(
    "quota_balances",
    metadata,
    uid(),
    text("scope"),
    uid("user_id", nullable=True),
    text("resource"),
    text("window_kind"),
    col("window_start", sa.Date),
    col("limit_amount", sa.BigInteger),
    col("committed", sa.BigInteger, default="0"),
    col("reserved", sa.BigInteger, default="0"),
    stamp("updated_at"),
    choices("scope", "user global"),
    choices("window_kind", "lifetime day month"),
    check("limit_amount >= 0 AND committed >= 0 AND reserved >= 0", "nonnegative"),
    check("(scope = 'user') = (user_id IS NOT NULL)", "scope_owner"),
    fk("user_id", "users.id", delete="CASCADE"),
)
sa.Index(
    "quota_balances_uniq",
    quota_balances.c.scope,
    quota_balances.c.user_id,
    quota_balances.c.resource,
    quota_balances.c.window_kind,
    quota_balances.c.window_start,
    unique=True,
    postgresql_nulls_not_distinct=True,
)
quota_reservations = sa.Table(
    "quota_reservations",
    metadata,
    uid(),
    uid("balance_id"),
    uid("user_id", nullable=True),
    uid("job_id", nullable=True),
    uid("asset_id", nullable=True),
    text("resource"),
    col("amount", sa.BigInteger),
    text("status", default="'reserved'"),
    col("committed_amount", sa.BigInteger, nullable=True),
    stamp("expires_at", default=None),
    stamp(),
    stamp("settled_at", nullable=True, default=None),
    check("amount > 0", "amount"),
    check("committed_amount IS NULL OR committed_amount >= 0", "committed_amount"),
    choices("status", "reserved committed released expired"),
    fk("balance_id", "quota_balances.id", delete="CASCADE"),
    fk("user_id", "users.id", delete="RESTRICT"),
    fk("job_id", "jobs.id", delete="SET NULL"),
)
sa.Index(
    "quota_res_expiry_idx",
    quota_reservations.c.expires_at,
    postgresql_where=sa.text("status = 'reserved'"),
)
sa.Index("quota_res_job_idx", quota_reservations.c.job_id)
quota_reservations.append_constraint(fk("asset_id", "assets.id", delete="SET NULL"))
sa.Index("quota_res_asset_idx", quota_reservations.c.asset_id)
idempotency_keys = sa.Table(
    "idempotency_keys",
    metadata,
    uid("user_id", primary=True),
    col("key", sa.Text, primary=True),
    digest("request_hash"),
    text("state"),
    col("response_status", sa.Integer, nullable=True),
    js("response_body", nullable=True, default=None),
    stamp(),
    stamp("expires_at", default=None),
    choices("state", "in_progress completed"),
    digest_check("request_hash"),
    check("response_status IS NULL OR response_status BETWEEN 100 AND 599", "response_status"),
    check(
        "response_body IS NULL OR octet_length(response_body::text) <= 32768", "response_body_size"
    ),
    fk("user_id", "users.id", delete="CASCADE"),
)
sa.Index("idempotency_expiry_idx", idempotency_keys.c.expires_at)
system_settings = sa.Table(
    "system_settings",
    metadata,
    col("key", sa.Text, primary=True),
    js("value", default=None),
    stamp("updated_at"),
)
font_exports = sa.Table(
    "font_exports",
    metadata,
    uid(),
    uid("project_id"),
    uid("snapshot_id"),
    uid("job_id", nullable=True),
    uid("requested_by_user_id"),
    text("format"),
    text("family_name"),
    text("style_name", default="'Regular'"),
    text("version_string"),
    js("build_manifest", nullable=True, default=None),
    digest("build_id", nullable=True),
    js("validation_report", nullable=True, default=None),
    col("finalized_attempt", sa.Integer, nullable=True),
    text("status", default="'pending'"),
    uid("asset_id", nullable=True),
    js("coverage_report", nullable=True, default=None),
    text("validation_status", nullable=True),
    uid("validation_report_asset_id", nullable=True),
    js("tool_versions", nullable=True, default=None),
    col("model_version_ids", ARRAY(UUID), default="'{}'"),
    stamp(),
    stamp("finalized_at", nullable=True, default=None),
    choices("format", "ttf otf woff2"),
    choices("status", "pending ready failed"),
    choices("validation_status", "passed passed_with_warnings failed"),
    check("status = 'pending' OR finalized_at IS NOT NULL", "finalized_at"),
    check(
        "status <> 'ready' OR (asset_id IS NOT NULL AND validation_status IN ('passed','passed_with_warnings'))",
        "ready_validated",
    ),
    sa.UniqueConstraint("asset_id"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("snapshot_id", "project_snapshots.id"),
    fk("job_id", "jobs.id", delete="SET NULL"),
    fk("requested_by_user_id", "users.id"),
    fk("asset_id", "assets.id"),
    fk("validation_report_asset_id", "assets.id"),
)
training_consents = sa.Table(
    "training_consents",
    metadata,
    uid(),
    uid("user_id"),
    text("purpose"),
    text("policy_version"),
    stamp("granted_at"),
    stamp("withdrawn_at", nullable=True, default=None),
    choices("purpose", "model_training"),
    fk("user_id", "users.id", delete="CASCADE"),
)
sa.Index(
    "training_consents_active_uniq",
    training_consents.c.user_id,
    training_consents.c.purpose,
    unique=True,
    postgresql_where=sa.text("withdrawn_at IS NULL"),
)
dataset_contributions = sa.Table(
    "dataset_contributions",
    metadata,
    uid(),
    uid("sample_id", nullable=True),
    digest("contributor_ref"),
    uid("consent_id", nullable=True),
    text("dataset_version", nullable=True),
    col("training_run_ids", ARRAY(sa.Text), default="'{}'"),
    text("status", default="'active'"),
    stamp(),
    stamp("withdrawn_at", nullable=True, default=None),
    stamp("removed_from_dataset_at", nullable=True, default=None),
    choices("status", "active withdrawn removed_from_dataset"),
    digest_check("contributor_ref"),
    fk("sample_id", "samples.id", delete="SET NULL"),
    fk("consent_id", "training_consents.id", delete="SET NULL"),
)
deletion_requests = sa.Table(
    "deletion_requests",
    metadata,
    uid(),
    digest("subject_ref"),
    uid("user_id", nullable=True),
    text("scope"),
    uid("project_id", nullable=True),
    text("state", default="'requested'"),
    js("progress"),
    js("evidence", nullable=True, default=None),
    text("error_code", nullable=True),
    stamp("requested_at"),
    stamp("completed_at", nullable=True, default=None),
    choices("scope", "project account contributions_only"),
    choices("state", "requested in_progress completed failed"),
    digest_check("subject_ref"),
)
audit_events = sa.Table(
    "audit_events",
    metadata,
    col("id", sa.BigInteger, primary=True),
    stamp("occurred_at"),
    text("actor_ref"),
    text("action"),
    text("resource_type"),
    uid("resource_id", nullable=True),
    text("outcome"),
    text("request_id", nullable=True),
    js("metadata"),
    choices("outcome", "success failure"),
)

# Exact, content-free natural-idempotency receipt; actor/history references stay original.
identity_claims = sa.Table(
    "identity_claims",
    metadata,
    uid("guest_user_id", primary=True),
    uid("destination_user_id"),
    text("mode"),
    js("response_body", default=None),
    stamp(),
    choices("mode", "linked transferred"),
    check("octet_length(response_body::text) <= 32768", "response_body_size"),
    check("(mode = 'linked') = (guest_user_id = destination_user_id)", "mode_subjects"),
    fk("guest_user_id", "users.id", delete="RESTRICT"),
    fk("destination_user_id", "users.id", delete="RESTRICT"),
)
sa.Index("identity_claims_destination_idx", identity_claims.c.destination_user_id)

job_checkpoints = sa.Table(
    "job_checkpoints",
    metadata,
    uid(),
    uid("job_id"),
    uid("owner_user_id"),
    uid("project_id"),
    text("stage"),
    text("cache_key"),
    uid("manifest_asset_id"),
    col("manifest_sha256", sa.LargeBinary),
    js("dependencies", default=None),
    col("attempt", sa.Integer),
    text("lease_holder"),
    stamp(),
    stamp("expires_at", default=None),
    sa.UniqueConstraint("job_id", "stage", "cache_key"),
    check("octet_length(manifest_sha256)=32", "manifest_hash"),
    fk("job_id", "jobs.id", delete="CASCADE"),
    fk("owner_user_id", "users.id"),
    fk("project_id", "projects.id"),
    fk("manifest_asset_id", "assets.id"),
)
sa.Index("job_checkpoints_expiry_idx", job_checkpoints.c.expires_at)
job_streams = sa.Table(
    "job_streams",
    metadata,
    uid(),
    uid("job_id"),
    uid("owner_user_id"),
    stamp("expires_at", default=None),
    fk("job_id", "jobs.id", delete="CASCADE"),
    fk("owner_user_id", "users.id"),
)
sa.Index("job_streams_expiry_idx", job_streams.c.expires_at)

# Phase 07: durable transient work targets are cleared on completion. Receipts use HMACs.
for column in (
    digest("idempotency_hash", nullable=True),
    digest("request_hash", nullable=True),
    digest("project_ref", nullable=True),
    js("identity_uids", default="'[]'::jsonb"),
    js("target_users", default="'[]'::jsonb"),
    js("consent_ids", default="'[]'::jsonb"),
    stamp("updated_at"),
):
    deletion_requests.append_column(column)
sa.Index("deletion_requests_subject_idx", deletion_requests.c.subject_ref, deletion_requests.c.id)
sa.Index(
    "deletion_requests_idem_idx",
    deletion_requests.c.subject_ref,
    deletion_requests.c.idempotency_hash,
    unique=True,
    postgresql_where=sa.text("idempotency_hash IS NOT NULL"),
)
for column in (
    text("storage_key", nullable=True),
    digest("content_sha256", nullable=True),
    uid("source_asset_id", nullable=True),
    text("transformation", default="'legacy_unavailable'"),
    js("source_lineage"),
    stamp("copy_completed_at", nullable=True, default=None),
):
    dataset_contributions.append_column(column)
dataset_contributions.append_constraint(fk("source_asset_id", "assets.id", delete="SET NULL"))
sa.Index("dataset_contributions_consent_idx", dataset_contributions.c.consent_id)
dataset_versions = sa.Table(
    "dataset_versions",
    metadata,
    text("version"),
    js("manifest", default=None),
    digest("manifest_sha256"),
    stamp(),
    sa.PrimaryKeyConstraint("version"),
    digest_check("manifest_sha256"),
)

assets.append_column(stamp("last_used_at"))

# Mutable account working state. These rows hold bounded draft metadata and
# references; canonical approved geometry, snapshots and exports remain in their
# own immutable tables.
project_workspaces = sa.Table(
    "project_workspaces",
    metadata,
    uid("project_id", primary=True),
    uid("owner_user_id"),
    js("workspace", default=None),
    col("row_version", sa.Integer, default="1"),
    stamp(),
    stamp("updated_at"),
    check("row_version > 0", "row_version"),
    check("pg_column_size(workspace) <= 1048576", "workspace_size"),
    fk("project_id", "projects.id", delete="CASCADE"),
    fk("owner_user_id", "users.id", delete="CASCADE"),
)
sa.Index("project_workspaces_owner_idx", project_workspaces.c.owner_user_id)
writing_documents = sa.Table(
    "writing_documents",
    metadata,
    uid(),
    uid("owner_user_id"),
    text("title"),
    text("format"),
    js("document", default=None),
    col("row_version", sa.Integer, default="1"),
    stamp(),
    stamp("updated_at"),
    check("length(title) BETWEEN 1 AND 120", "title_length"),
    choices("format", "letter note card"),
    check("row_version > 0", "row_version"),
    check("pg_column_size(document) <= 262144", "document_size"),
    fk("owner_user_id", "users.id", delete="CASCADE"),
)
sa.Index(
    "writing_documents_owner_cursor_idx",
    writing_documents.c.owner_user_id,
    writing_documents.c.updated_at.desc(),
    writing_documents.c.id.desc(),
)

# Service use is distinct from optional model-training consent.
service_agreements = sa.Table(
    "service_agreements",
    metadata,
    uid(),
    uid("user_id"),
    text("policy_version"),
    text("policy_sha256"),
    js("policy_document", default=None),
    col("terms_accepted", sa.Boolean),
    col("handwriting_rights_confirmed", sa.Boolean),
    stamp("accepted_at"),
    fk("user_id", "users.id", delete="CASCADE"),
    sa.UniqueConstraint("user_id", "policy_sha256"),
    check("policy_sha256 ~ '^[0-9a-f]{64}$'", "hash"),
    check("terms_accepted AND handwriting_rights_confirmed", "affirmative"),
)
abuse_reports = sa.Table(
    "abuse_reports",
    metadata,
    uid(),
    text("category"),
    text("details"),
    text("contact", nullable=True),
    uid("resource_id", nullable=True),
    stamp(),
    choices("category", "impersonation fraud rights other"),
    check("length(details) BETWEEN 20 AND 4000", "details"),
    check("contact IS NULL OR length(contact) <= 254", "contact"),
)
sa.Index("abuse_reports_created_idx", abuse_reports.c.created_at)
