"""Apply grants to a pre-created least-privilege app role, using an admin URL.

Example: DATABASE_URL=... python infrastructure/migrations/grant_app_role.py penforge_app
Role creation/password delivery belongs to the operator, not migrations. Runtime
credentials must not own tables or inherit the migration role; grants cannot
remove a PostgreSQL owner's inherent rights. No connection values are printed.
"""

import os
import re
import sys

from app.db.session import make_engine
from sqlalchemy import text


def grant_app_role(role: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role):
        raise ValueError("Role must be a bounded lowercase PostgreSQL identifier")
    engine = make_engine(os.environ["DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        connection.execute(
            text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
        )
        connection.execute(
            text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        )
        connection.execute(text(f'REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM "{role}"'))
        connection.execute(text(f'GRANT EXECUTE ON FUNCTION penforge_prune_audit() TO "{role}"'))
        connection.execute(
            text(f'GRANT EXECUTE ON FUNCTION penforge_purge_account(uuid) TO "{role}"')
        )
        connection.execute(text(f'REVOKE ALL ON alembic_version FROM "{role}"'))
        connection.execute(
            text(f'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON model_versions FROM "{role}"')
        )
    engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: grant_app_role.py EXISTING_ROLE")
    grant_app_role(sys.argv[1])
