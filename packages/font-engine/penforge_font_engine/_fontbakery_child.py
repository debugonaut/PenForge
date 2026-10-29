"""Run the fixed validation profile without Python installer imports.

This is an application call-path guard, not an operating-system sandbox or a
vulnerability disposition. Keep the declared FontBakery dependencies installed.
The surrounding isolated worker retains its resource and network restrictions.
"""

from __future__ import annotations

import importlib.abc
import sys
from pathlib import Path

INSTALLERS = frozenset({"pip", "pip_api"})


def installer_loaded() -> bool:
    return any(name.partition(".")[0] in INSTALLERS for name in sys.modules)


class InstallerImportBlocker(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.attempted = False

    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] in INSTALLERS:
            self.attempted = True
            raise ModuleNotFoundError("fontbakery_installer_import_denied")
        return None


def main() -> int:
    if len(sys.argv) != 3 or installer_loaded():
        return 2
    source, output = (Path(value).resolve() for value in sys.argv[1:])
    if not source.is_file() or source == output:
        return 2
    guard = InstallerImportBlocker()
    # Install before importing FontBakery or discovering its checks/dependencies.
    # It stays active for the lifetime of this one-use child, including shutdown.
    sys.meta_path.insert(0, guard)
    sys.argv = [
        "fontbakery",
        "check-profile",
        str(Path(__file__).with_name("fontbakery_profile.py").resolve()),
        str(source),
        "--skip-network",
        "--json",
        str(output),
        "-q",
    ]
    try:
        from fontbakery.cli import main as check  # type: ignore[import-untyped]

        result = check()
    except ModuleNotFoundError:
        if not guard.attempted:
            raise
        result = 2
    except SystemExit as error:
        result = error.code
    # An import denied and caught inside a dependency must still fail validation.
    if guard.attempted or installer_loaded():
        return 2
    return result if isinstance(result, int) else (0 if result is None else 2)


if __name__ == "__main__":
    raise SystemExit(main())
