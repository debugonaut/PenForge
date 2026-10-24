"""Fail CI if lightweight API isolation or router/service boundaries regress."""

import ast
from collections.abc import Iterator
from pathlib import Path

FORBIDDEN_API = {
    "torch",
    "tensorflow",
    "cv2",
    "numpy",
    "PIL",
    "fontTools",
    "penforge_font_engine",
    "uharfbuzz",
    "ots",
    "pathops",
    "onnxruntime",
    "ml_core",
    "research",
}
API_ROOT = Path("apps/api/app")
FONT_ROOT = Path("packages/font-engine/penforge_font_engine")


def imported_modules(path: Path, root: Path, package_name: str) -> Iterator[str]:
    """Resolve relative imports and `from package import module` before checking policy."""
    package = [package_name, *path.relative_to(root).parts[:-1]]
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parent = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*parent, *([node.module] if node.module else [])])
            yield module
            for alias in node.names:
                if alias.name != "*":
                    yield f"{module}.{alias.name}"


violations: list[str] = []
for path in API_ROOT.rglob("*.py"):
    relative = path.relative_to(API_ROOT).parts
    own_module = relative[1] if relative[0] == "modules" and len(relative) > 2 else None
    for name in imported_modules(path, API_ROOT, "app"):
        parts = name.split(".")
        if parts[0] in FORBIDDEN_API or ".".join(parts[:2]) in {
            "packages.extraction",
            "packages.ml_core",
        }:
            violations.append(f"{path}: forbidden API dependency {name}")
        if path.name == "router.py" and (
            name.startswith(("sqlalchemy", "app.db", "app.adapters.storage"))
            or {"repository", "repositories"}.intersection(parts)
        ):
            violations.append(f"{path}: router bypasses service: {name}")
        if own_module and parts[:2] == ["app", "modules"] and len(parts) > 3:
            if parts[2] != own_module and parts[3] != "service":
                violations.append(f"{path}: cross-module access must use public service")
if violations:
    raise SystemExit("\n".join(violations))
print("API dependency and service boundaries passed")

for path in FONT_ROOT.rglob("*.py"):
    for name in imported_modules(path, FONT_ROOT, "penforge_font_engine"):
        if name.split(".")[0] in {
            "app",
            "sqlalchemy",
            "firebase_admin",
            "torch",
            "cv2",
            "numpy",
            "packages",
            "services",
        }:
            raise SystemExit(f"{path}: font engine must be independent: {name}")
print("Font engine dependency boundary passed")
