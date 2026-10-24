"""Ratified v3 baseline with immutable lineage and ownership guards.

Revision ID: 0001_initial
Revises: None

The SQL files are frozen migration inputs. Future schema changes require a new
revision; this migration deliberately never imports mutable application metadata.
Downgrade destroys local data. Never automate it against production.
"""

from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "users",
    "guest_usage",
    "projects",
    "assets",
    "samples",
    "glyphs",
    "model_versions",
    "glyph_revisions",
    "project_snapshots",
    "style_profiles",
    "jobs",
    "job_events",
    "quota_balances",
    "quota_reservations",
    "idempotency_keys",
    "system_settings",
    "font_exports",
    "training_consents",
    "dataset_contributions",
    "deletion_requests",
    "audit_events",
)
_FUNCTIONS = (
    "penforge_reference_guard()",
    "penforge_glyph_identity_guard()",
    "penforge_audit_guard()",
    "penforge_job_guard()",
    "penforge_export_guard()",
    "penforge_asset_guard()",
    "penforge_snapshot_guard()",
    "penforge_revision_guard()",
    "penforge_unicode_scalars(integer[])",
)


def upgrade() -> None:
    directory = Path(__file__).resolve().parents[1]
    for name in ("functions.sql", "schema.sql", "guards.sql"):
        op.execute(directory.joinpath(name).read_text(encoding="utf-8"))


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f'DROP TABLE "{table}" CASCADE')
    for function in _FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")
