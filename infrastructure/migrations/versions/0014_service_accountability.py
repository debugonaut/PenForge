"""Versioned service acceptance and bounded private misuse reports."""

from alembic import op

revision = "0014_service_accountability"
down_revision = "0013_account_workspace"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE service_agreements (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      policy_version text NOT NULL, policy_sha256 text NOT NULL CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
      policy_document jsonb NOT NULL, terms_accepted boolean NOT NULL CHECK (terms_accepted),
      handwriting_rights_confirmed boolean NOT NULL CHECK (handwriting_rights_confirmed),
      accepted_at timestamptz NOT NULL DEFAULT now(), UNIQUE (user_id, policy_sha256)
    );
    CREATE FUNCTION penforge_guard_service_agreement() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' AND penforge_privacy_erasure_valid() AND EXISTS (
        SELECT 1 FROM deletion_requests r
        WHERE r.id=NULLIF(current_setting('penforge.erasure_request',true),'')::uuid
          AND r.target_users @> jsonb_build_array(OLD.user_id::text)
      ) THEN RETURN OLD; END IF;
      RAISE EXCEPTION 'service_agreement_immutable';
    END $$;
    CREATE TRIGGER service_agreement_immutable BEFORE UPDATE OR DELETE ON service_agreements
      FOR EACH ROW EXECUTE FUNCTION penforge_guard_service_agreement();
    CREATE TABLE abuse_reports (
      id uuid PRIMARY KEY, category text NOT NULL CHECK (category IN ('impersonation','fraud','rights','other')),
      details text NOT NULL CHECK (length(details) BETWEEN 20 AND 4000),
      contact text CHECK (contact IS NULL OR length(contact) <= 254), resource_id uuid,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX abuse_reports_created_idx ON abuse_reports(created_at);
    """)


def downgrade():
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM service_agreements) OR EXISTS (SELECT 1 FROM abuse_reports) THEN
        RAISE EXCEPTION 'accountability_records_require_forward_repair';
      END IF;
    END $$;
    DROP TABLE abuse_reports;
    DROP TABLE service_agreements;
    DROP FUNCTION penforge_guard_service_agreement();
    """)
