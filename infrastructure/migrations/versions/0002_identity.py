"""Identity receipts, transfer-safe dedupe/idempotency and ownership race backstops.

Revision ID: 0002_identity
Revises: 0001_initial

Frozen SQL inputs; downgrade is available before any claim. Afterwards use a
forward repair: restoring the old indexes could discard/collide preserved work.
"""

from pathlib import Path

from alembic import op

revision = "0002_identity"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    directory = Path(__file__).resolve().parents[1]
    # Validate the previous release and run backfill checks before trigger DDL.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute(directory.joinpath("identity_schema.sql").read_text(encoding="utf-8"))
    op.execute(directory.joinpath("identity_guards.sql").read_text(encoding="utf-8"))
    op.execute("SET CONSTRAINTS ALL DEFERRED")


def downgrade() -> None:
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM identity_claims) THEN
        RAISE EXCEPTION 'identity_claims_require_forward_repair';
      END IF;
    END $$""")
    for table in ("projects", "assets", "jobs", "quota_balances", "quota_reservations"):
        op.execute(f"DROP TRIGGER identity_owner_write ON {table}")
    for table in ("users", "projects", "jobs", "quota_balances"):
        op.execute(f"DROP TRIGGER identity_owner_consistency ON {table}")
    op.execute("DROP TRIGGER identity_user_transition ON users")
    op.execute("DROP TRIGGER identity_claims_immutable ON identity_claims")
    op.execute("DROP TABLE identity_claims")
    op.execute("DROP INDEX assets_owner_inventory_idx")
    op.execute("DROP FUNCTION penforge_identity_receipt_guard()")
    op.execute("DROP FUNCTION penforge_identity_user_guard()")
    op.execute("DROP FUNCTION penforge_identity_owner_guard()")
    op.execute("DROP FUNCTION penforge_identity_consistency_guard()")
    op.execute("DROP INDEX jobs_idem_uniq")
    op.execute("ALTER TABLE jobs DROP COLUMN idempotency_scope_user_id")
    op.execute(
        "CREATE UNIQUE INDEX jobs_idem_uniq ON jobs(owner_user_id,idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    op.execute("DROP INDEX assets_owner_sha_uniq")
    op.execute(
        "CREATE UNIQUE INDEX assets_owner_sha_uniq ON assets(owner_user_id,sha256) WHERE kind='original_upload' AND state='ready'"
    )
    baseline = Path(__file__).resolve().parents[1].joinpath("guards.sql").read_text()
    reference = baseline[baseline.index("CREATE FUNCTION penforge_reference_guard()") :]
    reference = reference[: reference.index("CREATE CONSTRAINT TRIGGER")]
    op.execute(reference.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
