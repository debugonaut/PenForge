"""Immutable snapshot-based style profiles; preserve earlier privacy lineage."""

import sqlalchemy as sa
from alembic import op

revision = "0011_personalization"
down_revision = "0010_privacy"
branch_labels = None
depends_on = None


def upgrade():
    # A sample cascade queues a revision UPDATE event before deleting that same
    # revision. Check current existence before validating the deferred event.
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef('penforge_extraction_reference_guard()'::regprocedure)")
    )
    anchor = "ELSIF TG_TABLE_NAME='glyph_revisions' THEN"
    assert definition and anchor in definition
    op.execute(
        definition.replace(
            anchor,
            anchor
            + "\n IF NOT EXISTS(SELECT 1 FROM glyph_revisions WHERE id=NEW.id) THEN RETURN NULL; END IF;",
        )
    )
    op.execute("""
    ALTER TABLE samples ADD COLUMN prompt_context jsonb;
    CREATE FUNCTION penforge_model_version_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR (to_jsonb(NEW)-'status') IS DISTINCT FROM (to_jsonb(OLD)-'status')
        OR (OLD.status='revoked' AND NEW.status <> 'revoked') THEN
        RAISE EXCEPTION 'model_version_immutable' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER model_version_history BEFORE UPDATE OR DELETE ON model_versions
      FOR EACH ROW EXECUTE FUNCTION penforge_model_version_history();
    ALTER TABLE glyph_revisions ADD COLUMN alternate_source_revision_id uuid,
      ADD COLUMN alternate_source_snapshot_id uuid;
    ALTER TABLE glyph_revisions ADD CONSTRAINT alternate_revision_fk FOREIGN KEY(alternate_source_revision_id)
      REFERENCES glyph_revisions(id) DEFERRABLE INITIALLY DEFERRED;
    ALTER TABLE glyph_revisions ADD CONSTRAINT alternate_snapshot_fk FOREIGN KEY(alternate_source_snapshot_id)
      REFERENCES project_snapshots(id) DEFERRABLE INITIALLY DEFERRED;
    CREATE FUNCTION penforge_alternate_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM glyph_revisions WHERE id=NEW.id) THEN RETURN NULL; END IF;
      IF (NEW.alternate_source_revision_id IS NULL) <> (NEW.alternate_source_snapshot_id IS NULL) THEN
        RAISE EXCEPTION 'alternate_source_incomplete' USING ERRCODE='23514'; END IF;
      IF NEW.alternate_source_revision_id IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM glyph_revisions r JOIN glyphs source ON source.id=r.glyph_id
          JOIN glyphs target ON target.id=NEW.glyph_id
          JOIN project_snapshots s ON s.id=NEW.alternate_source_snapshot_id
        WHERE r.id=NEW.alternate_source_revision_id AND r.approval='approved'
          AND r.origin <> 'ml_generated' AND r.source_sample_id=NEW.source_sample_id
          AND source.project_id=target.project_id AND source.codepoints=target.codepoints
          AND target.variant_index>0 AND s.project_id=target.project_id
          AND s.manifest->'glyphs'->>source.id::text=r.id::text
      ) THEN RAISE EXCEPTION 'alternate_source_mismatch' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER alternate_source_consistency AFTER INSERT OR UPDATE ON glyph_revisions
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION penforge_alternate_source_guard();
    ALTER TABLE style_profiles ADD COLUMN source_snapshot_id uuid,
      ADD COLUMN profile_sha256 bytea,
      ADD COLUMN algorithm_version text,
      ADD COLUMN created_by_user_id uuid;
    ALTER TABLE style_profiles ADD CONSTRAINT styles_source_snapshot_fk
      FOREIGN KEY(source_snapshot_id) REFERENCES project_snapshots(id) DEFERRABLE INITIALLY DEFERRED;
    ALTER TABLE style_profiles ADD CONSTRAINT styles_creator_fk FOREIGN KEY(created_by_user_id) REFERENCES users(id);
    ALTER TABLE style_profiles ADD CONSTRAINT styles_profile_sha256 CHECK(profile_sha256 IS NULL OR octet_length(profile_sha256)=32);
    CREATE UNIQUE INDEX styles_identity_idx ON style_profiles(project_id,profile_sha256);
    CREATE FUNCTION penforge_style_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND OLD.source_snapshot_id IS NULL AND NEW.source_snapshot_id IS NULL THEN RETURN NEW; END IF;
      IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM projects WHERE id=OLD.project_id) THEN RETURN OLD; END IF;
      IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'style_profile_immutable' USING ERRCODE='23514'; END IF;
      IF NEW.source_snapshot_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM projects p JOIN users u ON u.id=p.owner_user_id
          JOIN project_snapshots s ON s.project_id=p.id
        WHERE p.id=NEW.project_id AND s.id=NEW.source_snapshot_id
          AND p.status <> 'deleting' AND u.account_state='active'
          AND u.merged_into_user_id IS NULL AND NEW.created_by_user_id=u.id
          AND NEW.algorithm_version IS NOT NULL AND NEW.profile_sha256 IS NOT NULL
      ) THEN RAISE EXCEPTION 'style_scope_invalid' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER style_profiles_history BEFORE INSERT OR UPDATE OR DELETE ON style_profiles
      FOR EACH ROW EXECUTE FUNCTION penforge_style_history();
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM style_profiles WHERE source_snapshot_id IS NOT NULL)
      OR EXISTS(SELECT 1 FROM glyph_revisions WHERE alternate_source_revision_id IS NOT NULL)
      OR EXISTS(SELECT 1 FROM samples WHERE prompt_context IS NOT NULL)
      THEN RAISE EXCEPTION 'personalization_requires_forward_repair'; END IF; END $$;
      DROP TRIGGER style_profiles_history ON style_profiles;
      ALTER TABLE samples DROP COLUMN prompt_context;
      DROP TRIGGER model_version_history ON model_versions;
      DROP FUNCTION penforge_model_version_history();
      DROP TRIGGER alternate_source_consistency ON glyph_revisions;
      DROP FUNCTION penforge_alternate_source_guard();
      ALTER TABLE glyph_revisions DROP CONSTRAINT alternate_revision_fk, DROP CONSTRAINT alternate_snapshot_fk;
      ALTER TABLE glyph_revisions DROP COLUMN alternate_source_revision_id, DROP COLUMN alternate_source_snapshot_id;
      DROP FUNCTION penforge_style_history();
      DROP INDEX styles_identity_idx;
      ALTER TABLE style_profiles DROP CONSTRAINT styles_source_snapshot_fk,
        DROP CONSTRAINT styles_creator_fk, DROP CONSTRAINT styles_profile_sha256;
      ALTER TABLE style_profiles DROP COLUMN source_snapshot_id, DROP COLUMN profile_sha256,
        DROP COLUMN algorithm_version, DROP COLUMN created_by_user_id;
    """)
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef('penforge_extraction_reference_guard()'::regprocedure)")
    )
    op.execute(
        definition.replace(
            "\n IF NOT EXISTS(SELECT 1 FROM glyph_revisions WHERE id=NEW.id) THEN RETURN NULL; END IF;",
            "",
        )
    )
