"""Exercise the actual dependency guard without importing any application or model."""

import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "scripts/check_boundaries.py"


def run_guard(tmp_path: Path, source: str, relative: str = "apps/api/app/modules/jobs/router.py"):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    "source",
    [
        "import app.modules.jobs.repository as repo",
        "from app.modules.jobs.repository import JobsRepository",
        "from app.modules.jobs import repository as repo",
        "from .repository import JobsRepository",
        "from . import repository as repo",
        "from .repositories import JobsRepository",
        "from . import repositories",
        "from ...db import models",
        "from ...adapters import storage",
        "from sqlalchemy import select",
    ],
)
def test_router_cannot_bypass_its_domain_module(tmp_path, source):
    result = run_guard(tmp_path, source)
    assert result.returncode != 0, source
    assert "router bypasses service" in result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "from app.modules.projects.repository import ProjectsRepository",
        "from app.modules.projects import repository",
        "from ..projects.repository import ProjectsRepository",
        "from ..projects import repository",
    ],
)
def test_domain_modules_use_other_domains_public_service(tmp_path, source):
    result = run_guard(tmp_path, source, "apps/api/app/modules/jobs/service.py")
    assert result.returncode != 0
    assert "cross-module access must use public service" in result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "import torch",
        "from PIL import Image",
        "from packages.extraction import geometry",
        "from packages import extraction",
        "from packages.ml_core import registry",
        "from packages import ml_core",
        "from research import datasets",
    ],
)
def test_http_application_cannot_import_heavy_processing(tmp_path, source):
    result = run_guard(tmp_path, source, "apps/api/app/core/example.py")
    assert result.returncode != 0
    assert "forbidden API dependency" in result.stderr


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("apps/api/app/modules/jobs/router.py", "from .service import QueueService"),
        ("apps/api/app/modules/jobs/router.py", "from app.modules.jobs import service"),
        ("apps/api/app/modules/jobs/service.py", "from .repository import JobsRepository"),
        ("apps/api/app/modules/jobs/service.py", "from . import repository"),
        ("apps/api/app/modules/jobs/service.py", "from ..projects.service import ProjectsService"),
        ("apps/api/app/modules/jobs/service.py", "from app.modules.projects import service"),
        ("apps/api/app/modules/jobs/repository.py", "from app.db import models"),
        ("apps/api/app/main.py", "from app.modules.jobs.repository import JobsRepository"),
        ("packages/font-engine/penforge_font_engine/compiler.py", "from .spacing import measure"),
        (
            "packages/font-engine/penforge_font_engine/render/check.py",
            "from ..compiler import sfnt_bytes",
        ),
    ],
)
def test_supported_module_interfaces_stay_available(tmp_path, relative, source):
    result = run_guard(tmp_path, source, relative)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "from app import main",
        "import sqlalchemy",
        "from packages import ml_core",
        "import services.worker",
    ],
)
def test_font_engine_stays_independent(tmp_path, source):
    result = run_guard(tmp_path, source, "packages/font-engine/penforge_font_engine/compiler.py")
    assert result.returncode != 0
    assert "font engine must be independent" in result.stderr


def test_nested_font_module_cannot_hide_a_worker_dependency(tmp_path):
    result = run_guard(
        tmp_path,
        "from services.worker import exports",
        "packages/font-engine/penforge_font_engine/render/check.py",
    )
    assert result.returncode != 0
    assert "font engine must be independent" in result.stderr
