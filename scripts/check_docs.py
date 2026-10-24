"""Check local Markdown links and preserved source hashes without reading private source paths."""

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote

HASHES = {
    "docs/specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md": "99e15238f9eb5ab8fd28e15f8f551c15c9d74cdcc25e4f2f7c06c350a9be4dc9",
    "docs/specifications/design-quality-update.md": "93b08dccd8e2ca732a8517a4af694ec5e034f8f48a1433cce06dd4daec9f97cd",
}
errors = []
for name, expected in HASHES.items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
        errors.append(f"Preserved source changed: {name}")
# The two local agent handoffs are deliberately absent from a public checkout.
files = [
    path
    for name in ("AGENTS.md", "PROJECT_STATUS.md", "README.md", "document.md")
    if (path := Path(name)).is_file()
]
files += list(Path("docs").rglob("*.md")) + list(Path("research").rglob("*.md"))
files += list(Path("packages").rglob("README.md"))
files += list(Path("artifacts/phase-00").glob("*.md"))
count = 0
for file in files:
    for target in re.findall(r"\]\(([^)]+)\)", file.read_text()):
        target = target.strip("<>").split("#")[0].split("?")[0]
        if not target or "://" in target or target.startswith(("mailto:", "data:")):
            continue
        path = (file.parent / unquote(target)).resolve()
        count += 1
        if not path.exists():
            errors.append(f"{file}: missing {target}")
if errors:
    raise SystemExit("\n".join(errors))
print(f"Passed {count} local links across {len(files)} files and both preserved source hashes")
