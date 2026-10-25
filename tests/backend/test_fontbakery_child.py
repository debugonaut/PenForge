"""Real subprocess controls for the validator's installer-import boundary."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHILD = ROOT / "packages/font-engine/penforge_font_engine/_fontbakery_child.py"


def run_stub(tmp_path, code, *, prelude="", arguments=None):
    package = tmp_path / "fontbakery"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(code)
    source, output = tmp_path / "font.ttf", tmp_path / "result.json"
    source.write_bytes(b"synthetic stub; no font parsing in this control")
    # An installer body must never execute, even if supplied by package discovery.
    marker = tmp_path / "installer-executed"
    for name in ("pip", "pip_api"):
        installer = tmp_path / name
        installer.mkdir()
        (installer / "__init__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        )
    args = arguments if arguments is not None else [str(source), str(output)]
    runner = (
        "import runpy,sys,types; "
        f"sys.path.insert(0,{str(tmp_path)!r}); "
        f"sys.argv={[str(CHILD), *args]!r}; "
        f"{prelude + '; ' if prelude else ''}"
        f"runpy.run_path({str(CHILD)!r},run_name='__main__')"
    )
    result = subprocess.run([sys.executable, "-I", "-c", runner], capture_output=True, timeout=10)
    assert not marker.exists()
    return result, output


@pytest.mark.parametrize("name", ["pip", "pip._vendor.urllib3", "pip_api", "pip_api._call"])
def test_installer_import_is_denied_before_body_execution(tmp_path, name):
    result, output = run_stub(tmp_path, f"import importlib\nimportlib.import_module({name!r})")
    assert result.returncode == 2 and not output.exists()


@pytest.mark.parametrize("name", ["pip", "pip._vendor", "pip_api", "pip_api._call"])
def test_preloaded_installer_cannot_bypass_finder(tmp_path, name):
    result, output = run_stub(
        tmp_path,
        "raise AssertionError('FontBakery must not load')",
        prelude=f"sys.modules[{name!r}]=types.ModuleType({name!r})",
    )
    assert result.returncode == 2 and not output.exists()


def test_swallowed_import_rejection_still_fails_child(tmp_path):
    result, _ = run_stub(
        tmp_path,
        "def main():\n"
        "    try:\n        import pip\n"
        "    except ImportError:\n        pass\n"
        "    return 0\n",
    )
    assert result.returncode == 2


def test_late_injected_installer_cannot_report_success(tmp_path):
    result, _ = run_stub(
        tmp_path,
        "def main():\n"
        "    import sys,types\n"
        "    sys.modules['pip']=types.ModuleType('pip')\n"
        "    return 0\n",
    )
    assert result.returncode == 2


def test_exact_profile_arguments_and_noninstaller_imports(tmp_path):
    result, output = run_stub(
        tmp_path,
        "def main():\n"
        "    import json,sys\n"
        "    from pathlib import Path\n"
        "    Path(sys.argv[6]).write_text(json.dumps(sys.argv))\n"
        "    return 0\n",
    )
    assert result.returncode == 0, result.stderr
    arguments = json.loads(output.read_text())
    assert arguments == [
        "fontbakery",
        "check-profile",
        str(CHILD.with_name("fontbakery_profile.py")),
        str(tmp_path / "font.ttf"),
        "--skip-network",
        "--json",
        str(output),
        "-q",
    ]


@pytest.mark.parametrize("code", [0, 1, 2, None, "unexpected error"])
def test_fontbakery_exit_status_is_preserved_or_rejected(tmp_path, code):
    result, _ = run_stub(tmp_path, f"def main():\n    raise SystemExit({code!r})\n")
    assert result.returncode == (code if isinstance(code, int) else (0 if code is None else 2))


def test_extra_arguments_cannot_select_another_profile(tmp_path):
    result, _ = run_stub(tmp_path, "raise AssertionError('not reached')", arguments=["--help"])
    assert result.returncode == 2


def test_installer_body_positive_control_runs_without_guard(tmp_path):
    marker = tmp_path / "installer-executed"
    package = tmp_path / "pip"
    package.mkdir()
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0,{str(tmp_path)!r}); import pip",
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and marker.read_text() == "executed"
