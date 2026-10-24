"""Grant an existing, non-owner worker role; no role/provider is provisioned.

Use a separate operator-created NOINHERIT login without migration/app membership.
Local CLI development may still use the admin URL; hosted use remains gated on
separate credentials. Workers receive no revision/snapshot/activation privileges.
"""

import os
import re
import sys

from app.db.session import make_engine
from sqlalchemy import text


def grant_worker_role(connection, role: str, schema: str = "public") -> None:
    if not all(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", v) for v in (role, schema)):
        raise ValueError("Invalid SQL role or schema")
    target = f'"{role}"'

    def sql(value):
        connection.execute(text(value))

    sql(f'GRANT USAGE ON SCHEMA "{schema}" TO {target}')
    sql(f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM {target}')
    for table in (
        "users",
        "projects",
        "assets",
        "samples",
        "jobs",
        "job_events",
        "quota_balances",
        "quota_reservations",
        "glyphs",
        "glyph_candidates",
        "ml_result_artifacts",
        "system_settings",
        "job_checkpoints",
        "model_versions",
    ):
        sql(f'GRANT SELECT ON "{schema}".{table} TO {target}')
    for table in ("assets", "jobs", "quota_balances", "quota_reservations"):
        sql(f'GRANT INSERT, UPDATE ON "{schema}".{table} TO {target}')
    for table in ("users", "projects", "glyphs"):
        sql(f'GRANT UPDATE(updated_at) ON "{schema}".{table} TO {target}')
    sql(f'GRANT UPDATE(state,quality,quality_reasons) ON "{schema}".samples TO {target}')
    for table in ("job_events", "ml_result_artifacts", "job_checkpoints"):
        sql(f'GRANT INSERT ON "{schema}".{table} TO {target}')
    sql(
        f'GRANT INSERT(id,project_id,codepoints,variant_index,coverage_status) ON "{schema}".glyphs TO {target}'
    )
    sql(
        f'GRANT INSERT(id,result_artifact_id,project_id,glyph_id,source_sample_id,origin,rank,outline_inline,outline_asset_id,metrics,content_hash,evidence) ON "{schema}".glyph_candidates TO {target}'
    )
    sql(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO {target}')


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: grant_worker_role.py EXISTING_ROLE")
    engine = make_engine(os.environ["DATABASE_URL"])
    with engine.begin() as connection:
        grant_worker_role(connection, sys.argv[1])
    engine.dispose()
