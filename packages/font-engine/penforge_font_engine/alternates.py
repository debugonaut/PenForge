"""Deterministic adjacent-repeat cycling with authentic approved source lineage."""

import io
import re

import uharfbuzz as hb
from fontTools.ttLib import TTFont


def enabled_alternates(rows, manifest):
    if manifest.script_code == "Deva":
        return {}
    limit = 0 if manifest.options.realism == 0 else 1 if manifest.options.realism <= 50 else 2
    if not limit:
        return {}
    base = {
        r["outline"]["codepoints"][0]: r
        for r in rows
        if r.get("approval") == "approved" and r.get("variant_index", 0) == 0
    }
    groups: dict[int, list[dict]] = {}
    for row in rows:
        if row.get("approval") != "approved" or row.get("variant_index", 0) == 0:
            continue
        cp = row["outline"]["codepoints"][0]
        if cp not in base or cp == 32:
            continue
        if row.get("origin") not in {
            "authentic_extraction",
            "manual_drawing",
            "auto_extract",
            "user_capture",
            "user_edit",
            "restored",
        }:
            raise ValueError("authentic_alternates_required")
        if not all(
            re.fullmatch(r"sha256:[0-9a-f]{64}", row.get(k, ""))
            for k in ("source_hash", "revision_hash")
        ):
            raise ValueError("alternate_lineage_required")
        groups.setdefault(cp, []).append(row)
    result = {}
    for cp, candidates in sorted(groups.items()):
        used = {base[cp].get("source_hash")}
        selected = []
        for row in sorted(candidates, key=lambda r: (r["source_hash"], r["revision_hash"])):
            if row["source_hash"] in used:
                continue
            used.add(row["source_hash"])
            selected.append(row)
            if len(selected) == limit:
                break
        if selected:
            result[cp] = selected
    return result


def cycling_source(groups):
    if not groups:
        return ""
    lines = ["lookup PF_repeat_cycle {"]
    for names in groups.values():
        for previous, following in zip(names, names[1:]):
            lines.append(f"sub {previous} {names[0]}' by {following};")
    lines += ["} PF_repeat_cycle;", "feature calt { lookup PF_repeat_cycle; } calt;"]
    return "\n".join(lines)


def shape_ids(data, text, enabled):
    font = hb.Font(hb.Face(data))
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.direction, buffer.script, buffer.language = "ltr", "Latn", "en"
    hb.shape(font, buffer, {"calt": enabled, "liga": False, "kern": False})
    return [i.codepoint for i in buffer.glyph_infos]


def validate_cycling(raw, groups):
    font = TTFont(io.BytesIO(raw))
    cmap = font.getBestCmap()
    corpus = ["eeeeeeee", "hello wellness", "ee eee", "ee.eee", "ee11ee!!", "e e", "ee\nee"]
    corpus += [chr(cp) * 8 for cp in sorted(groups)]
    evidence = []
    for text in corpus:
        expected = [font.getGlyphID(cmap[ord(c)]) if ord(c) in cmap else 0 for c in text]
        off = shape_ids(raw, text, False)
        on = shape_ids(raw, text, True)
        if off != expected or len(on) != len(expected) or on != shape_ids(raw, text, True):
            raise ValueError("calt_determinism_or_fallback_failed")
        for i, c in enumerate(text):
            cp = ord(c)
            allowed = [
                font.getGlyphID(n)
                for n in groups.get(cp, [cmap[cp]] if cp in cmap else [".notdef"])
            ]
            if on[i] not in allowed or (cp in cmap and on[i] == 0):
                raise ValueError("calt_mapping_failed")
            if i and text[i - 1] == c and len(allowed) > 1 and on[i - 1] == on[i]:
                raise ValueError("calt_adjacent_repeat_failed")
        evidence.append({"text": text, "off": off, "on": on})
    for cp in groups:
        if shape_ids(raw, chr(cp), True) != [font.getGlyphID(cmap[cp])]:
            raise ValueError("calt_run_boundary_failed")
    return evidence
