"""Consent, contribution ledgers and narrowly authorized erasure; no automatic purge."""

from pathlib import Path

from alembic import op

revision = "0010_privacy"
down_revision = "0009_queue_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).resolve().parents[1].joinpath("privacy.sql").read_text())


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM deletion_requests) OR EXISTS(SELECT 1 FROM dataset_versions)
        OR EXISTS(SELECT 1 FROM dataset_contributions WHERE storage_key IS NOT NULL) THEN
        RAISE EXCEPTION 'privacy_requires_forward_repair'; END IF;
    END $$;
    ALTER TABLE assets DROP COLUMN last_used_at;
    DROP FUNCTION penforge_prune_audit();
    DROP TRIGGER privacy_step_fence ON deletion_requests;
    DROP FUNCTION penforge_privacy_step_fence();
    DROP FUNCTION penforge_purge_account(uuid);
    DROP FUNCTION penforge_privacy_erasure_valid();
    DROP TRIGGER training_consent_history ON training_consents;
    DROP FUNCTION penforge_consent_history_guard();
    DROP TRIGGER dataset_version_immutable ON dataset_versions;
    DROP TABLE dataset_versions;
    DROP FUNCTION penforge_dataset_version_guard();
    DROP INDEX deletion_requests_idem_idx;
    DROP INDEX deletion_requests_subject_idx;
    ALTER TABLE deletion_requests DROP COLUMN idempotency_hash, DROP COLUMN request_hash,
      DROP COLUMN project_ref, DROP COLUMN identity_uids, DROP COLUMN target_users, DROP COLUMN consent_ids, DROP COLUMN updated_at;
    DROP INDEX dataset_contributions_consent_idx;
    ALTER TABLE dataset_contributions DROP COLUMN storage_key, DROP COLUMN content_sha256,
      DROP COLUMN source_asset_id, DROP COLUMN transformation, DROP COLUMN source_lineage,
      DROP COLUMN copy_completed_at;
    """)
    # Restore the exact preceding guards, rather than weaken ordinary writes.
    directory = Path(__file__).resolve().parents[1]
    baseline = directory.joinpath("guards.sql").read_text()
    start = baseline.index("CREATE FUNCTION penforge_audit_guard()")
    end = baseline.index("CREATE TRIGGER audit_events_append_only", start)
    op.execute(baseline[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    previous = directory.joinpath("identity_guards.sql").read_text()
    start = previous.index("CREATE FUNCTION penforge_identity_receipt_guard()")
    end = previous.index("CREATE TRIGGER identity_claims_immutable", start)
    op.execute(previous[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
