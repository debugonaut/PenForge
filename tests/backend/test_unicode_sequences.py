import hashlib
import json
from pathlib import Path

import pytest
from penforge_contracts.unicode import capture_sequence, graphemes, normalize, sequence_evidence

ROOT = Path(__file__).parents[2]
UCD = ROOT / "packages/contracts/unicode/17.0.0"


def test_pinned_unicode_integrity():
    manifest = json.loads((UCD / "sources.json").read_bytes())
    for row in manifest["sources"]:
        assert hashlib.sha256((UCD / row["path"]).read_bytes()).hexdigest() == row["sha256"]


def test_all_unicode_17_grapheme_conformance_vectors():
    for line in (UCD / "GraphemeBreakTest.txt").read_text().splitlines():
        body = line.split("#")[0].strip()
        if not body:
            continue
        expected = []
        current = []
        for item in body.split():
            if item == "÷":
                if current:
                    expected.append(current)
                    current = []
            elif item != "×":
                current.append(int(item, 16))
        assert graphemes("".join(chr(cp) for seq in expected for cp in seq)) == expected, line


def test_source_and_derived_nukta_remain_distinct():
    a, b = sequence_evidence("क़"), sequence_evidence("क़")
    assert a["source_codepoints"] != b["source_codepoints"]
    assert a["source_hash"] != b["source_hash"]
    assert a["derived"] == b["derived"]
    assert normalize([0x1D15E], "NFD") == [0x1D157, 0x1D165]
    assert normalize([0x1100, 0x1161, 0x11A8], "NFC") == [0xAC01]


@pytest.mark.parametrize("text", ["क्ष", "कि", "क्\u200dष", "क्\u200c", "़", "क़"])
def test_capture_clusters(text):
    capture_sequence([ord(c) for c in text])


@pytest.mark.parametrize("text", ["क\u202e", "\u200d", "कख", "\ud800", "क\u200c"])
def test_unsupported_capture_policy(text):
    with pytest.raises(ValueError):
        capture_sequence([ord(c) for c in text])


def test_full_pinned_unicode_normalization_conformance():
    for line in (UCD / "NormalizationTest.txt").read_text().splitlines():
        body = line.split("#")[0].strip()
        if not body or body.startswith("@"):
            continue
        c1, c2, c3, c4, c5 = ([int(x, 16) for x in part.split()] for part in body.split(";")[:5])
        for source in (c1, c2, c3):
            assert normalize(source, "NFC") == c2, line
            assert normalize(source, "NFD") == c3, line
        for source in (c4, c5):
            assert normalize(source, "NFC") == c4, line
            assert normalize(source, "NFD") == c5, line
