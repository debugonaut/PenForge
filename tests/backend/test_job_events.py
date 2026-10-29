"""Real HTTP/PG event ordering, reconnect, ownership, ETags and revocation."""

import json
import threading
import time
import uuid

import pytest
import sqlalchemy as sa
from app.db import models as m
from test_uploads_database import uploads_database as uploads_database
from test_uploads_http import SOURCE, headers, initialize
from test_uploads_http import capture_http as capture_http

pytestmark = pytest.mark.integration


def create_job(client, project):
    created, _, _ = initialize(client, project)
    assert (
        client.put(created["upload"]["url"], content=SOURCE, headers=headers()).status_code == 204
    )
    result = client.post(f"/api/v1/uploads/{created['asset_id']}/complete", headers=headers())
    assert result.status_code == 202, result.text
    return result.json()["job_id"]


def test_sse_polling_etag_resume_owner_isolation(capture_http):
    client, sync, users, projects, uploads = capture_http
    job = create_job(client, projects[0])
    path = f"/api/v1/jobs/{job}"
    before = client.get(path, headers=headers())
    assert before.status_code == 200
    assert before.json()["eta_seconds"] is None
    assert (
        client.get(path, headers=headers() | {"If-None-Match": before.headers["etag"]}).status_code
        == 304
    )
    canceled = client.post(path + "/cancel", headers=headers())
    assert canceled.status_code == 202 and canceled.json()["state"] == "canceled"
    stream = client.get(path + "/events", headers=headers())
    assert stream.status_code == 200 and "text/event-stream" in stream.headers["content-type"]
    snapshots = [
        json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")
    ]
    assert snapshots[-1] == client.get(path, headers=headers()).json()
    assert [x["event_id"] for x in snapshots] == sorted({x["event_id"] for x in snapshots})
    resumed = client.get(
        path + "/events", headers=headers() | {"Last-Event-ID": str(before.json()["event_id"])}
    )
    unseen = [
        json.loads(line[6:]) for line in resumed.text.splitlines() if line.startswith("data: ")
    ]
    assert unseen and all(x["event_id"] > before.json()["event_id"] for x in unseen)
    assert unseen[-1]["state"] == "canceled"
    for suffix in ("", "/events", "/cancel"):
        result = (
            client.post(path + suffix, headers=headers("other-token"))
            if suffix == "/cancel"
            else client.get(path + suffix, headers=headers("other-token"))
        )
        assert result.status_code == 404
    assert (
        client.get(path + "/events", headers=headers() | {"Last-Event-ID": "nonsense"}).status_code
        == 400
    )
    assert "private-file" not in stream.text and "Bearer" not in stream.text
    with sync.connect() as con:
        assert con.scalar(sa.select(sa.func.count()).select_from(m.job_streams)) == 0


@pytest.mark.parametrize("change", ["account", "ownership"])
def test_live_stream_revokes_when_scope_changes(capture_http, change):
    client, sync, users, projects, uploads = capture_http
    job = create_job(client, projects[0])
    result = []
    thread = threading.Thread(
        target=lambda: result.append(client.get(f"/api/v1/jobs/{job}/events", headers=headers()))
    )
    thread.start()
    deadline = time.monotonic() + 5
    while True:
        with sync.connect() as con:
            opened = con.scalar(sa.select(sa.func.count()).select_from(m.job_streams))
        if opened:
            break
        assert time.monotonic() < deadline
        time.sleep(0.02)
    with sync.begin() as con:
        if change == "account":
            con.execute(
                m.users.update().where(m.users.c.id == users[0]).values(account_state="suspended")
            )
        else:
            con.execute(
                m.projects.update()
                .where(m.projects.c.id == projects[0])
                .values(owner_user_id=users[1])
            )
            con.execute(
                m.assets.update()
                .where(m.assets.c.project_id == projects[0])
                .values(owner_user_id=users[1])
            )
            balances = {
                r["resource"]: r["id"]
                for r in con.execute(
                    sa.select(m.quota_balances).where(m.quota_balances.c.user_id == users[1])
                ).mappings()
            }
            refs = list(
                con.execute(
                    sa.select(m.quota_reservations, m.quota_balances.c.scope)
                    .join(
                        m.quota_balances, m.quota_balances.c.id == m.quota_reservations.c.balance_id
                    )
                    .where(m.quota_reservations.c.user_id == users[0])
                ).mappings()
            )
            for ref in refs:
                con.execute(
                    m.quota_reservations.update()
                    .where(m.quota_reservations.c.id == ref["id"])
                    .values(
                        user_id=users[1],
                        balance_id=balances[ref["resource"]]
                        if ref["scope"] == "user"
                        else ref["balance_id"],
                    )
                )
            con.execute(
                m.jobs.update().where(m.jobs.c.id == uuid.UUID(job)).values(owner_user_id=users[1])
            )
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "event: revoked" in result[0].text
    assert client.get(f"/api/v1/jobs/{job}", headers=headers()).status_code in (403, 404)
