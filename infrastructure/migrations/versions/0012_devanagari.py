"""Pin the required inventory without changing earlier capture/font lineage."""

from alembic import op

revision = "0012_devanagari"
down_revision = "0011_personalization"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE projects ADD COLUMN required_set_id text;
    UPDATE projects SET required_set_id='deva-tier1-ucd17-v1' WHERE script_code='Deva';
    ALTER TABLE projects ADD CONSTRAINT ck_projects_required_set CHECK
      (required_set_id IS NULL OR (script_code='Deva' AND required_set_id='deva-tier1-ucd17-v1'));
    CREATE FUNCTION penforge_required_set_pin() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND OLD.required_set_id IS NOT NULL
        AND NEW.required_set_id IS DISTINCT FROM OLD.required_set_id THEN
        RAISE EXCEPTION 'required_set_immutable' USING ERRCODE='23514'; END IF;
      IF NEW.script_code='Deva' THEN NEW.required_set_id='deva-tier1-ucd17-v1'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER project_required_set BEFORE INSERT OR UPDATE ON projects
      FOR EACH ROW EXECUTE FUNCTION penforge_required_set_pin();
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM project_snapshots WHERE manifest->>'required_set_id' IS NOT NULL)
      THEN RAISE EXCEPTION 'devanagari_lineage_requires_forward_repair'; END IF; END $$;
      DROP TRIGGER project_required_set ON projects;
      DROP FUNCTION penforge_required_set_pin();
      ALTER TABLE projects DROP CONSTRAINT ck_projects_required_set;
      ALTER TABLE projects DROP COLUMN required_set_id;
    """)
