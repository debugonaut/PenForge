"""Integrity-checked content-free tombstones outside database restore state."""

import hashlib
import hmac
import json
from uuid import UUID

from app.adapters.storage.base import StorageError
from app.core.privacy_reference import privacy_reference


class ErasureJournal:
    def __init__(self, storage, secret: bytes):
        if len(secret) < 32:
            raise ValueError("privacy_key_required")
        self.storage, self.secret = storage, secret

    def reference(self, domain, value):
        return privacy_reference(self.secret, domain, value)

    def progress_token(self, row):
        return self.reference("receipt", str(row["id"]) + bytes(row["subject_ref"]).hex()).hex()

    def write(self, row):
        data = dict(
            accepted_at=row["requested_at"].isoformat(),
            subjects=[self.reference("subject", uid).hex() for uid in row["identity_uids"]],
            version=1,
            request_id=str(row["id"]),
            scope=row["scope"],
            subject_ref=bytes(row["subject_ref"]).hex(),
            project_ref=bytes(row["project_ref"]).hex() if row["project_ref"] else None,
        )
        body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        envelope = json.dumps(
            dict(body=data, mac=hmac.new(self.secret, body, hashlib.sha256).hexdigest()),
            sort_keys=True,
        ).encode()
        self.storage.put_immutable("privacy/erasures/" + str(row["id"]), envelope)

    def entries(self):
        result = []
        for entry in self.storage.list_objects("privacy/erasures/"):
            envelope = json.loads(self.storage.read_object(entry.key, max_bytes=4096))
            data = envelope["body"]
            if (
                set(data)
                != {
                    "version",
                    "request_id",
                    "scope",
                    "subject_ref",
                    "project_ref",
                    "accepted_at",
                    "subjects",
                }
                or data["version"] != 1
            ):
                raise StorageError("erasure_journal_invalid")
            UUID(data["request_id"])
            if data["scope"] not in {"account", "project", "contributions_only"}:
                raise StorageError("erasure_journal_invalid")
            body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
            if not hmac.compare_digest(
                envelope["mac"], hmac.new(self.secret, body, hashlib.sha256).hexdigest()
            ):
                raise StorageError("erasure_journal_invalid")
            result.append(data)
        return result

    def account_blocked(self, uid):
        ref = self.reference("subject", uid).hex()
        return any(
            e["scope"] == "account" and (e["subject_ref"] == ref or ref in e["subjects"])
            for e in self.entries()
        )
