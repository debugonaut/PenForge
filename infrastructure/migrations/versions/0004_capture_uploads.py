"""Expand immutable captures, publication provenance and storage reservations.

Revision ID: 0004_capture_uploads
Revises: 0003_project_cursor
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0004_capture_uploads"
down_revision = "0003_project_cursor"
branch_labels = None
depends_on = None

ASSET_COLUMNS = {
    "source_type": sa.Text(),
    "declared_sha256": sa.LargeBinary(),
    "document_hash": sa.LargeBinary(),
    "parent_asset_id": UUID(),
    "verify_job_id": UUID(),
    "template_id": sa.Text(),
    "template_version": sa.Text(),
    "template_manifest_sha256": sa.LargeBinary(),
    "page_index": sa.Integer(),
    "upload_capability_hash": sa.LargeBinary(),
    "upload_issued_owner_id": UUID(),
    "download_capability_hash": sa.LargeBinary(),
    "download_issued_owner_id": UUID(),
    "cleanup_completed_at": sa.DateTime(timezone=True),
}


def upgrade() -> None:
    for name, kind in ASSET_COLUMNS.items():
        op.add_column("assets", sa.Column(name, kind, nullable=True))
    op.drop_constraint(op.f("ck_assets_kind_values"), "assets", type_="check")
    op.create_check_constraint(
        op.f("ck_assets_kind_values"),
        "assets",
        "kind IN ('original_upload','raw_strokes','preprocessed','segment','vector','checkpoint','export','validation_report','thumbnail','style_embedding')",
    )
    for name in (
        "declared_sha256",
        "document_hash",
        "template_manifest_sha256",
        "upload_capability_hash",
        "download_capability_hash",
    ):
        op.create_check_constraint(
            op.f(f"ck_assets_{name}_length"), "assets", f"{name} IS NULL OR octet_length({name})=32"
        )
    op.create_check_constraint(
        op.f("ck_assets_source_type_values"),
        "assets",
        "source_type IS NULL OR source_type IN ('template_scan','template_photo','freeform_photo','canvas_drawing')",
    )
    op.create_check_constraint(
        op.f("ck_assets_template_identity"),
        "assets",
        "(template_id IS NULL) = (template_version IS NULL) AND (template_id IS NULL) = (template_manifest_sha256 IS NULL)",
    )
    for name, target, deletion in (
        ("parent_asset_id", "assets", None),
        ("verify_job_id", "jobs", "SET NULL"),
        ("upload_issued_owner_id", "users", "RESTRICT"),
        ("download_issued_owner_id", "users", "RESTRICT"),
    ):
        op.create_foreign_key(
            f"fk_assets_{name}_{target}",
            "assets",
            target,
            [name],
            ["id"],
            ondelete=deletion,
            deferrable=True if name == "parent_asset_id" else None,
            initially="DEFERRED" if name == "parent_asset_id" else None,
        )
    for name in ("source_sha256", "template_manifest_sha256", "document_hash"):
        op.add_column("samples", sa.Column(name, sa.LargeBinary(), nullable=True))
        op.create_check_constraint(
            op.f(f"ck_samples_{name}_length"),
            "samples",
            f"{name} IS NULL OR octet_length({name})=32",
        )
    op.add_column("quota_reservations", sa.Column("asset_id", UUID(), nullable=True))
    op.create_foreign_key(
        "fk_quota_reservations_asset_id_assets",
        "quota_reservations",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("quota_res_asset_idx", "quota_reservations", ["asset_id"])
    op.execute("""
    CREATE FUNCTION penforge_capture_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE a record; p record; canonical record;
    BEGIN
      IF TG_TABLE_NAME='assets' THEN
        IF TG_OP='UPDATE' AND (NEW.source_type,NEW.declared_sha256,NEW.document_hash,NEW.parent_asset_id,NEW.template_id,NEW.template_version,NEW.template_manifest_sha256,NEW.page_index)
          IS DISTINCT FROM (OLD.source_type,OLD.declared_sha256,OLD.document_hash,OLD.parent_asset_id,OLD.template_id,OLD.template_version,OLD.template_manifest_sha256,OLD.page_index) THEN
          RAISE EXCEPTION 'capture_provenance_immutable' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='samples' THEN
        IF TG_OP='UPDATE' AND (NEW.project_id,NEW.asset_id,NEW.source_type,NEW.template_id,NEW.template_version,NEW.page_index,NEW.source_sha256,NEW.template_manifest_sha256,NEW.document_hash)
          IS DISTINCT FROM (OLD.project_id,OLD.asset_id,OLD.source_type,OLD.template_id,OLD.template_version,OLD.page_index,OLD.source_sha256,OLD.template_manifest_sha256,OLD.document_hash) THEN
          RAISE EXCEPTION 'sample_source_immutable' USING ERRCODE='23514'; END IF;
        IF NEW.source_sha256 IS NOT NULL THEN
          SELECT * INTO a FROM assets WHERE id=NEW.asset_id;
          SELECT * INTO p FROM projects WHERE id=NEW.project_id FOR SHARE;
          IF a.project_id IS DISTINCT FROM NEW.project_id OR a.owner_user_id IS DISTINCT FROM p.owner_user_id OR a.source_type IS DISTINCT FROM NEW.source_type OR a.sha256 IS DISTINCT FROM NEW.source_sha256 OR a.template_id IS DISTINCT FROM NEW.template_id OR a.template_version IS DISTINCT FROM NEW.template_version OR a.template_manifest_sha256 IS DISTINCT FROM NEW.template_manifest_sha256 OR a.document_hash IS DISTINCT FROM NEW.document_hash THEN
            RAISE EXCEPTION 'sample_source_binding' USING ERRCODE='23514'; END IF;
          IF a.state <> 'ready' THEN
            SELECT * INTO canonical FROM assets WHERE id=a.duplicate_of;
            IF a.state<>'rejected' OR a.reject_reason<>'duplicate' OR canonical.state<>'ready' OR canonical.owner_user_id IS DISTINCT FROM p.owner_user_id OR canonical.sha256 IS DISTINCT FROM a.sha256 THEN
              RAISE EXCEPTION 'sample_requires_verified_source' USING ERRCODE='23514'; END IF;
          END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER captures_immutable BEFORE UPDATE ON assets FOR EACH ROW EXECUTE FUNCTION penforge_capture_guard();
    CREATE TRIGGER samples_source_binding BEFORE INSERT OR UPDATE ON samples FOR EACH ROW EXECUTE FUNCTION penforge_capture_guard();
    CREATE FUNCTION penforge_capture_parent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE a record;
    BEGIN
      SELECT * INTO a FROM assets WHERE id=NEW.id;
      IF FOUND AND a.parent_asset_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM assets WHERE id=a.parent_asset_id AND owner_user_id=a.owner_user_id AND project_id=a.project_id AND state='ready'
      ) THEN RAISE EXCEPTION 'capture_parent_scope' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER captures_parent_consistency AFTER INSERT OR UPDATE ON assets DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_capture_parent_guard();

    """)


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT 1 FROM assets WHERE source_type IS NOT NULL) OR EXISTS(SELECT 1 FROM samples WHERE source_sha256 IS NOT NULL) THEN RAISE EXCEPTION 'capture_downgrade_requires_forward_repair'; END IF; END $$"""
    )
    op.execute(
        "DROP TRIGGER captures_parent_consistency ON assets; DROP FUNCTION penforge_capture_parent_guard(); DROP TRIGGER samples_source_binding ON samples; DROP TRIGGER captures_immutable ON assets; DROP FUNCTION penforge_capture_guard()"
    )
    op.drop_index("quota_res_asset_idx", table_name="quota_reservations")
    op.drop_column("quota_reservations", "asset_id")
    for name in ("source_sha256", "template_manifest_sha256", "document_hash"):
        op.drop_column("samples", name)
    for name in reversed(ASSET_COLUMNS):
        op.drop_column("assets", name)
    op.drop_constraint(op.f("ck_assets_kind_values"), "assets", type_="check")
    op.create_check_constraint(
        op.f("ck_assets_kind_values"),
        "assets",
        "kind IN ('original_upload','preprocessed','segment','vector','checkpoint','export','validation_report','thumbnail','style_embedding')",
    )
