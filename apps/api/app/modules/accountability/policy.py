"""One policy payload for the legal pages and the server's immutable receipts."""

import hashlib
import json
from pathlib import Path

from app.core.config import Settings

POLICY_PATH = (
    Path(__file__).resolve().parents[5] / "packages/contracts/policies/service-use.v1.json"
)


def current_policy(settings: Settings) -> dict:
    policy = json.loads(POLICY_PATH.read_text())
    policy["operator"] = {
        "name": settings.service_operator_name,
        "contact": settings.service_operator_contact,
        "jurisdiction": "India",
    }
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return {
        "version": policy["version"],
        "sha256": digest,
        "document": policy,
        "ready": settings.service_ready
        and bool(settings.privacy_key)
        and (settings.app_env in ("local", "test") or policy["status"] == "reviewed"),
    }
