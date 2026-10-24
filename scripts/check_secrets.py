"""Fail if a credential looks committed.

Scans text files outside ignored build/vendor directories for high-signal credential
shapes. Prints ``path:line: pattern`` only — never the matched value — so CI logs cannot
leak the secret they are reporting.

Placeholders and the documented local development defaults are allowed explicitly.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".next",
    ".next-dev",
    ".venv",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "__pycache__",
    ".local",
    "artifacts",
    "playwright-report",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".zip",
    ".gz",
    ".pdf",
    ".pyc",
    ".webm",
    ".mp4",
}
TEXT_LIMIT = 2 * 1024 * 1024

PATTERNS = {
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "neon_password_url": re.compile(
        r"postgres(?:ql)?(?:\+psycopg)?://[^\s:@/]+:(?!CHANGE_ME@)[^\s:@/]{8,}@"
    ),
    "firebase_api_key": re.compile(r'"apiKey"\s*:\s*"AIza[0-9A-Za-z_-]{35}"'),
    "bearer_literal": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{40,}"),
}

# Files whose whole purpose is to hold placeholders.
ALLOW_FILES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    "docs/backend-ml-infra/phase-01-identity/development.md",
    # Contains only the deliberate credential-redaction test fixtures.
    "tests/backend/test_foundation.py",
}
# Documented local-only defaults; never valid outside local/test.
ALLOW_LINES = (
    "penforge:penforge@127.0.0.1",
    "local-penforge-development-only",
    "AIzaSy" + "x" * 33,
)


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > TEXT_LIMIT:
                continue
        except OSError:
            continue
        yield path


def main() -> int:
    findings: list[str] = []
    for path in iter_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in ALLOW_FILES:
            continue
        if path.name.startswith(".env") and not path.name.endswith(
            (".example", ".sample", ".template")
        ):
            findings.append(f"{relative}:1: env_file_committed")
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if any(allowed in line for allowed in ALLOW_LINES):
                continue
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{relative}:{number}: {name}")
    if findings:
        print("Potential committed credentials:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("No committed credential patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
