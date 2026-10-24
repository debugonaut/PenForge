from datetime import UTC, datetime

import pytest
from penforge_contracts import FontBuildManifest, FontOptions, safe_name


def manifest(**changes):
    return FontBuildManifest.model_validate(
        dict(
            snapshot_hash="sha256:" + "a" * 64,
            snapshot_created_at=datetime(2026, 10, 20, tzinfo=UTC),
            family_name="Private Font",
            tool_versions={"compiler": "1"},
        )
        | changes
    )


def test_manifest_identity_is_frozen_and_content_derived():
    a = manifest()
    assert a.build_id == manifest().build_id
    assert a.build_id != manifest(style_name="Bold").build_id
    assert a.build_id != manifest(options=FontOptions(fs_type=4)).build_id
    assert a.build_id != manifest(tool_versions={"compiler": "2"}).build_id
    assert a.build_id != manifest(snapshot_created_at=datetime(2026, 10, 21, tzinfo=UTC)).build_id
    assert len(a.postscript_name) <= 63 and a.postscript_name.isascii()


def test_names_and_private_request_fields():
    assert safe_name("  My <Font>\n ") == "My Font"
    assert manifest(family_name="लेखन").postscript_name.isascii()
    for field in ("request_id", "export_id", "owner_user_id", "signed_url"):
        with pytest.raises(ValueError):
            manifest(**{field: "forbidden"})
    with pytest.raises(ValueError):
        manifest(snapshot_created_at=datetime(2026, 10, 20))
