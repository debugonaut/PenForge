"""Publication safety over explicit synthetic inventories, with no Git mutations."""

import hashlib
import json

import pytest

from scripts.audit_publication import audit, inventory_paths


def test_public_inventory_hashes_exact_bytes_and_requires_manual_review(tmp_path):
    (tmp_path / "README.md").write_text("[License](LICENSE)\n")
    (tmp_path / "LICENSE").write_text("Owner-selected license\n")
    report = audit(tmp_path, ["README.md", "LICENSE"])
    assert report["automatic_checks_passed"]
    assert (
        report["files"][0]["sha256"]
        == hashlib.sha256((tmp_path / "LICENSE").read_bytes()).hexdigest()
    )
    assert "source_and_asset_licenses" in report["manual_review_required"]
    (tmp_path / "LICENSE").write_text("Changed after review\n")
    assert audit(tmp_path, ["README.md", "LICENSE"])["files"] != report["files"]


@pytest.mark.parametrize(
    "name",
    [
        "research/prepared/local-r1/split-ids.jsonl",
        "research/results/local-r1/result.json",
        "research/manifests/writers-v1.yaml",
        "research/CHECKPOINT_2026-10-21.inventory.json",
        "research/runs/private/font.ttf",
        "public/fonts/local-demo/PenForge-Cursive.woff2",
        ".env.production",
        "AGENTS.md",
        "apps/api/AGENTS.md",
        "NEXT_CHAT_HANDOFF.md",
        "research/PAGE_TO_FONT_BUILD.md",
        "research/ENGLISH_VOLUNTEER_HOLDOUT.md",
    ],
)
def test_private_files_are_rejected_even_if_explicitly_selected(tmp_path, name):
    report = audit(tmp_path, [name])
    assert not report["automatic_checks_passed"]
    assert report["problems"] == [{"path": name, "code": "private_file"}]
    assert report["files"] == []


@pytest.mark.parametrize(
    "paths",
    [[], ["../private"], ["/private"], ["a/../b"], ["./a"], ["a", "a"], ["a\nb"], ["a\\b"], [1]],
)
def test_inventory_cannot_escape_or_obscure_its_paths(paths):
    with pytest.raises(ValueError):
        inventory_paths(paths)


def test_links_are_checked_against_the_public_set_even_when_private_targets_exist(tmp_path):
    (tmp_path / "README.md").write_text("[Local handoff](PROJECT_STATUS.md)\n")
    (tmp_path / "PROJECT_STATUS.md").write_text("Private local content")
    report = audit(tmp_path, ["README.md"])
    assert report["problems"] == [
        {"path": "README.md", "line": 1, "code": "link_not_in_public_inventory"}
    ]


def test_renamed_records_remain_private_but_authored_metadata_is_a_control(tmp_path):
    path = tmp_path / "innocent.json"
    path.write_text(
        json.dumps(
            {"record_type": "writer_registry", "identities": [{"dataset_id": "real-writer-corpus"}]}
        )
    )
    assert audit(tmp_path, [path.name])["problems"][0]["code"] == "private_sample_or_writer_records"
    path.write_text(
        json.dumps(
            {
                "record_type": "dataset_manifest",
                "dataset_id": "real-corpus",
                "samples": [{"sample_id": "private"}],
            }
        )
    )
    assert audit(tmp_path, [path.name])["problems"][0]["code"] == "private_sample_or_writer_records"
    path.write_text(
        json.dumps(
            {
                "record_type": "writer_registry",
                "identities": [{"dataset_id": "split-validator-metadata"}],
            }
        )
    )
    assert audit(tmp_path, [path.name])["automatic_checks_passed"]


def test_symlinks_are_not_accepted_as_audited_file_content(tmp_path):
    (tmp_path / "target.md").write_text("Content")
    (tmp_path / "link.md").symlink_to("target.md")
    assert audit(tmp_path, ["link.md"])["problems"][0]["code"] == "symlink_not_publishable"


def test_binary_assets_are_hashed_without_mistaking_this_for_license_review(tmp_path):
    (tmp_path / "asset.bin").write_bytes(b"\xff\xfe\x00")
    report = audit(tmp_path, ["asset.bin"])
    assert report["automatic_checks_passed"]
    assert report["files"][0]["manual_binary_or_large_file_review"]


def test_machine_specific_documentation_is_flagged_without_echoing_its_content(tmp_path):
    (tmp_path / "README.md").write_text("Source: /Users/private-person/private-file\n")
    report = audit(tmp_path, ["README.md"])
    assert report["problems"] == [
        {"path": "README.md", "line": 1, "code": "machine_specific_documentation_path"}
    ]
    assert "private-person" not in json.dumps(report)
