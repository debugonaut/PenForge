"""Shared PostgreSQL admission windows.

Local/test may use bounded process memory; hosted API instances must share these rows.
Only digests derived from HMACs or owner UUIDs are stored, never a raw address.
"""

from alembic import op

revision = "0015_shared_rate_limits"
down_revision = "0014_service_accountability"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE rate_limit_windows (
          key text NOT NULL,
          limit_amount integer NOT NULL,
          window_seconds integer NOT NULL,
          window_start timestamptz NOT NULL,
          used integer NOT NULL,
          CONSTRAINT pk_rate_limit_windows PRIMARY KEY (key, limit_amount, window_seconds, window_start),
          CONSTRAINT ck_rate_limit_windows_key_length CHECK (length(key) BETWEEN 1 AND 256),
          CONSTRAINT ck_rate_limit_windows_limit_amount CHECK (limit_amount > 0),
          CONSTRAINT ck_rate_limit_windows_window_seconds CHECK (window_seconds > 0),
          CONSTRAINT ck_rate_limit_windows_used CHECK (used > 0)
        );
        CREATE INDEX rate_limit_windows_start_idx ON rate_limit_windows(window_start);
        """
    )


def downgrade():
    op.execute("DROP TABLE rate_limit_windows;")
