"""Stable privacy HMAC format. Never rotate its key without journal migration."""

import hashlib
import hmac


def privacy_reference(secret: bytes, domain: str, value: object) -> bytes:
    return hmac.new(
        secret,
        ("penforge-privacy-v1\0" + domain + "\0" + str(value)).encode(),
        hashlib.sha256,
    ).digest()
