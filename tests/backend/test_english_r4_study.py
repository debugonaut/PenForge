"""R4 split and selection mechanics; no archive scan or model execution."""

from research.english import r4_study


def test_r4_primary_partitions_match_pinned_nist_inventory():
    # The checksum-pinned by_write archive has no HSF8 entries.
    assert r4_study.PRIMARY_PARTITIONS == (0, 1, 2, 3, 6, 7)


def test_r4_writer_split_excludes_every_prior_writer_and_stratifies_partitions():
    writers = {
        f"{partition}:{index:04}": partition
        for partition in r4_study.PRIMARY_PARTITIONS
        for index in range(8)
    }
    writers.update({f"4:{index:04}": 4 for index in range(4)})
    excluded = {r4_study.nist_writer_id("0:0000"), r4_study.nist_writer_id("4:0000")}

    assignments, counts = r4_study.writer_splits(writers, excluded, 19)

    reversed_assignments, _ = r4_study.writer_splits(
        dict(reversed(list(writers.items()))), excluded, 19
    )
    assert assignments == reversed_assignments
    assert not excluded & {r4_study.nist_writer_id(writer) for writer in assignments}
    for partition in r4_study.PRIMARY_PARTITIONS:
        group = {split for writer, split in assignments.items() if writers[writer] == partition}
        assert group == {"train", "validation", "test"}
        assert all(counts[str(partition)][split] > 0 for split in group)
    assert all(
        assignments[writer] == "cross_test"
        for writer, partition in writers.items()
        if partition == r4_study.SCHOOL_PARTITION and writer not in {"4:0000"}
    )


def test_r4_selection_keeps_exact_case_classes_writer_disjoint_and_cross_test_optional():
    rows = []
    for split, partition, prefix, count in (
        ("train", 0, "tr", 3),
        ("validation", 1, "va", 2),
        ("test", 2, "te", 2),
        ("cross_test", 4, "hs", 1),
    ):
        for label in r4_study.LETTERS:
            for index in range(count):
                rows.append(
                    {
                        "id": f"{prefix}-{ord(label)}-{index}",
                        "hash": f"{prefix}-hash-{ord(label)}-{index}",
                        "path": f"images/{prefix}-hash-{ord(label)}-{index}.png",
                        "pixel_hash": f"{prefix}-pixels-{ord(label)}-{index}",
                        "label": label,
                        "writer": f"{prefix}-writer-{index}",
                        "source_group": f"{prefix}-page-{index}",
                        "partition": partition,
                        "split": split,
                        "source_member": f"{prefix}/{ord(label)}/{index}.png",
                    }
                )

    primary, cross, report = r4_study.select_rows(
        rows,
        seed=23,
        previous_hashes=set(),
        previous_pixel_hashes=set(),
        train_cap=3,
        evaluation_cap=2,
        minimum_evaluation=2,
    )

    assert len(primary) == 52 * (3 + 2 + 2)
    assert not cross
    assert report["cross_domain_test_available"] is False
    assert report["partitions"]["cross_test"]["samples"] == 0
    for split in ("train", "validation", "test"):
        split_rows = [row for row in primary if row["split"] == split]
        assert {row["label"] for row in split_rows} == set(r4_study.LETTERS)
        assert len({(row["label"], row["writer"]) for row in split_rows}) == len(split_rows)
    writer_sets = {
        split: {row["writer"] for row in primary if row["split"] == split}
        for split in ("train", "validation", "test")
    }
    assert not writer_sets["train"] & writer_sets["validation"]
    assert not writer_sets["train"] & writer_sets["test"]
    assert not writer_sets["validation"] & writer_sets["test"]


def test_r4_selection_quarantines_old_content_and_cross_writer_pixel_duplicates():
    rows = []
    for split, prefix, count in (("train", "tr", 2), ("validation", "va", 2), ("test", "te", 2)):
        for label in r4_study.LETTERS:
            for index in range(count):
                rows.append(
                    {
                        "id": f"{prefix}-{ord(label)}-{index}",
                        "hash": f"hash-{prefix}-{ord(label)}-{index}",
                        "path": "unused",
                        "pixel_hash": f"pixels-{prefix}-{ord(label)}-{index}",
                        "label": label,
                        "writer": f"{prefix}-writer-{index}",
                        "source_group": f"{prefix}-page-{index}",
                        "partition": 0,
                        "split": split,
                        "source_member": f"{prefix}/{ord(label)}/{index}.png",
                    }
                )
    rows[0]["pixel_hash"] = rows[52]["pixel_hash"]
    prior = {rows[2]["hash"]}

    primary, _, report = r4_study.select_rows(
        rows,
        seed=31,
        previous_hashes=prior,
        previous_pixel_hashes=set(),
        train_cap=1,
        evaluation_cap=1,
        minimum_evaluation=1,
    )

    assert rows[0]["id"] not in {row["id"] for row in primary}
    assert rows[52]["id"] not in {row["id"] for row in primary}
    assert rows[2]["id"] not in {row["id"] for row in primary}
    assert report["pixel_ambiguity_rows_quarantined"] == 2
    assert report["previous_study_overlap_rows_quarantined"] == 1
