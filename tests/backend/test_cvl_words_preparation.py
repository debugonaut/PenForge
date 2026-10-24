"""Actual-schema and fixed-image checks for the private CVL adapter."""

from __future__ import annotations

import hashlib
import io
import json
from collections import Counter

import pytest
from PIL import Image

from research.english.cvl import SOURCE
from research.english.cvl_words import NS, annotation, check_staging, raster, select, writer_splits


def page_xml(*, page="0001-1", text="Hello") -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <PcGts xmlns="{NS[1:-1]}"><Page imageFilename="{page}.tif" imageWidth="100" imageHeight="40">
      <AttrRegion attrType="1" fontType="2" id="{page}-0-0" text="{text}" />
    </Page></PcGts>'''.encode()


def test_annotation_joins_filename_from_exact_page_word_id_and_label():
    rows, evidence = annotation(page_xml(), "cvl-database-1-1/trainset/xml/0001-1_attributes.xml")

    assert rows[0]["member"] == "cvl-database-1-1/trainset/words/0001/0001-1-0-0-Hello.tif"
    assert rows[0]["label"] == "Hello"
    assert rows[0]["raw_writer"] == "0001"
    assert evidence["physical_encoding"] == "utf8"


def test_annotation_rejects_reference_and_page_identity_mismatch():
    with pytest.raises(ValueError, match="page_reference_mismatch"):
        annotation(page_xml(page="0002-1"), "cvl-database-1-1/trainset/xml/0001-1_attributes.xml")


def test_annotation_records_empty_pages_without_inventing_a_reference():
    raw = f'<PcGts xmlns="{NS[1:-1]}"><Page imageWidth="0" imageHeight="0" /></PcGts>'.encode()
    rows, evidence = annotation(raw, "cvl-database-1-1/testset/xml/0161-2_attributes.xml")

    assert rows == []
    assert evidence["without_handwriting"] is True


def test_annotation_allows_unlabelled_region_to_reuse_a_labeled_word_id():
    raw = page_xml().replace(
        b'<AttrRegion attrType="1" fontType="2" id="0001-1-0-0" text="Hello" />',
        b'<AttrRegion attrType="1" fontType="2" id="0001-1-0-0" />'
        b'<AttrRegion attrType="1" fontType="2" id="0001-1-0-0" text="Hello" />',
    )
    rows, evidence = annotation(raw, "cvl-database-1-1/trainset/xml/0001-1_attributes.xml")

    assert [row["label"] for row in rows] == ["Hello"]
    assert evidence["unlabelled_regions"] == 1


def test_annotation_tracks_known_duplicate_ids_in_excluded_german_passage():
    raw = page_xml(page="0001-6", text="Wort")
    raw = raw.replace(
        b'<AttrRegion attrType="1" fontType="2" id="0001-6-0-0" text="Wort" />',
        b'<AttrRegion attrType="1" fontType="2" id="0001-6-0-0" text="Wort" />'
        b'<AttrRegion attrType="1" fontType="2" id="0001-6-0-0" text="Text" />',
    )
    rows, evidence = annotation(raw, "cvl-database-1-1/testset/xml/0001-6_attributes.xml")

    assert {row["label"] for row in rows} == {"Wort", "Text"}
    assert evidence["duplicate_labeled_ids"] == 1


def test_annotation_rejects_entities_and_unsafe_labels():
    raw = page_xml(text="../Hello")
    with pytest.raises(ValueError, match="word_label_cannot_change_archive_path"):
        annotation(raw, "cvl-database-1-1/trainset/xml/0001-1_attributes.xml")

    entity = b'<!DOCTYPE x [<!ENTITY y "z">]><PcGts />'
    with pytest.raises(ValueError, match="entities_dtd"):
        annotation(entity, "cvl-database-1-1/trainset/xml/0001-1_attributes.xml")


def test_writer_splits_follow_the_publishers_disjoint_groups():
    rows = [
        {"publisher_partition": "trainset", "raw_writer": f"{writer:04d}"}
        for writer in range(1, 28)
    ] + [
        {"publisher_partition": "testset", "raw_writer": f"{writer:04d}"}
        for writer in range(52, 68)
    ]
    splits = writer_splits(rows, 20261021)

    assert Counter(splits.values()) == {"train": 21, "validation": 6, "test": 16}
    assert len(splits) == 43


def test_raster_strips_metadata_and_keeps_original_pixels():
    output = io.BytesIO()
    image = Image.new("L", (18, 9), color=200)
    image.putpixel((0, 0), 80)
    image.save(output, format="TIFF")
    png, pixel_hash = raster(output.getvalue())

    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (18, 9)
        assert image.mode == "RGB"
        assert image.getexif() == {}
        assert image.getpixel((0, 0)) == (80, 80, 80)
    assert len(pixel_hash) == 64


def test_selection_enforces_private_per_writer_cap_and_unique_pixels():
    rows = [
        {
            "id": f"sample-{index:03d}",
            "writer": "cvl-w-private",
            "split": "train",
            "label": f"word{index}",
            "pixel_hash": f"{index:064x}",
        }
        for index in range(64)
    ]
    selected, conflicts = select(rows, 20261021)

    assert len(selected) == 64
    assert len({row["pixel_hash"] for row in selected}) == 64
    assert conflicts == 0


def test_staging_check_rehashes_mappings_and_content_addressed_images(tmp_path):
    staging = tmp_path / "external-staging"
    (staging / "images").mkdir(parents=True)
    rows = []
    for split, count in (("train", 64), ("validation", 16), ("test", 16)):
        for index in range(count):
            ordinal = len(rows)
            image = Image.new("RGB", (3, 3), (ordinal, 0, 0))
            pixel_hash = hashlib.sha256("3x3:RGB:".encode() + image.tobytes()).hexdigest()
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=False, compress_level=6)
            data = output.getvalue()
            checksum = hashlib.sha256(data).hexdigest()
            (staging / "images" / f"{checksum}.png").write_bytes(data)
            rows.append(
                {
                    "id": f"sample-{ordinal:03d}",
                    "path": f"images/{checksum}.png",
                    "hash": checksum,
                    "pixel_hash": pixel_hash,
                    "label": "Hello",
                    "writer": f"writer-{split}",
                    "source_group": f"page-{split}",
                    "split": split,
                }
            )
    mapping = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    source_name, source_size, source_md5 = SOURCE
    (staging / "study.jsonl").write_bytes(mapping)
    (staging / "candidates-private.jsonl").write_bytes(mapping)
    (staging / "preparation.json").write_text(
        json.dumps(
            {
                "version": "cvl-word-preparation-r3-v1",
                "sources": {source_name: {"bytes": source_size, "md5": source_md5}},
                "dataset_admitted": False,
                "model_inference": False,
                "training": "not_run",
                "nonexact_duplicate_review": "pending",
                "mapping_sha256": hashlib.sha256(mapping).hexdigest(),
                "candidate_mapping_sha256": hashlib.sha256(mapping).hexdigest(),
            }
        )
    )

    result = check_staging(staging)
    assert result["samples"] == 96
    assert result["images_rehashed"] == 96
    assert result["dataset_admitted"] is False

    (staging / rows[0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="staged_image_digest_changed"):
        check_staging(staging)
