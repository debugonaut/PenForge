"""Deterministic Indic features from explicit approved canonical evidence only."""

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from penforge_contracts import GlyphOutline, content_hash
from penforge_contracts.unicode import normalize


def evidence_hash(rows):
    return content_hash(
        sorted([r["outline"] for r in rows if r.get("approval") == "approved"], key=content_hash)
    )


def glyph_name(row):
    return "form" + content_hash(row["outline"])[7:27]


def forms(rows, manifest):
    if manifest.script_code != "Deva":
        return []
    if manifest.indic_evidence_hash != evidence_hash(rows):
        raise ValueError("indic_evidence_hash_mismatch")
    values = []
    for row in rows:
        if row.get("approval") != "approved":
            continue
        o = GlyphOutline.model_validate(row["outline"]).model_dump(mode="json")
        indic = o.get("indic")
        if row.get("origin") in {
            "ml_generated",
            "geometric_reconstruction",
            "style_conditioned_generation",
        }:
            raise ValueError("unqualified_indic_generated_form")
        if indic:
            base = {k: v for k, v in o.items() if k != "indic"}
            if indic["geometry_hash"] != content_hash(base):
                raise ValueError("anchor_geometry_hash_mismatch")
            if o["mapping"] == "gsub_only" and not indic["substitution"]:
                raise ValueError("context_rule_required")
            for p in indic["anchors"].values():
                if not manifest.options.descender <= p["y"] <= manifest.options.ascender:
                    raise ValueError("anchor_outside_font_metrics")
        if o["mapping"] == "gsub_only" and indic:
            if (
                o["units_per_em"] != manifest.options.units_per_em
                or not 0 <= o["metrics"]["advance_width"] <= 32767
            ):
                raise ValueError("invalid_form_metrics")
            if o["codepoints"] != indic["substitution"]["input_codepoints"]:
                raise ValueError("form_source_sequence_mismatch")
            for c in o["contours"]:
                for p in [c["start"]] + [
                    v for s in c["segments"] for k, v in s.items() if k != "type"
                ]:
                    if (
                        not -32700 <= p["x"] <= 32700
                        or not manifest.options.descender <= p["y"] <= manifest.options.ascender
                    ):
                        raise ValueError("form_geometry_outside_font_metrics")
            values.append(row | dict(outline=o))
    return sorted(values, key=glyph_name)


def feature_source(rows, manifest, names):
    extra = forms(rows, manifest)
    by_name = {
        names[r["outline"]["codepoints"][0]]: r["outline"]
        for r in rows
        if r.get("approval") == "approved"
        and r["outline"]["mapping"] == "cmap"
        and r.get("variant_index", 0) == 0
    }
    by_name.update({glyph_name(r): r["outline"] for r in extra})
    substitutions: dict[str, list[str]] = {}
    base: list[str] = []
    ligatures: list[str] = []
    marks: list[str] = []
    components: list[str] = []
    above = []
    below = []

    def anchor(point):
        return f"<anchor {round(point['x'])} {round(point['y'])}>"

    for name, o in sorted(by_name.items()):
        e = o.get("indic") or {}
        role = e.get("role", "base")
        (
            marks
            if role in {"mark", "reph"}
            else ligatures
            if role in {"conjunct", "rakar"}
            else components
            if role == "half"
            else base
        ).append(name)
        anchors = e.get("anchors", {})
        if "above_mark" in anchors:
            above.append(name)
        if "below_mark" in anchors:
            below.append(name)
        sub = e.get("substitution")
        if sub:
            cps = sub["input_codepoints"]
            feature = sub["feature"]
            if feature in {"nukt", "ccmp"} and normalize(cps, "NFD") != normalize(
                o["codepoints"], "NFD"
            ):
                raise ValueError("nukta_identity_mismatch")
            if any(cp not in names for cp in cps):
                raise ValueError("substitution_input_unavailable")
            source = " ".join(names[cp] for cp in cps)
            substitutions.setdefault(feature, []).append(f"sub {source} by {name};")
    lines = ["languagesystem DFLT dflt;", "languagesystem dev2 dflt;"]
    for tag in (
        "ccmp",
        "nukt",
        "akhn",
        "rphf",
        "rkrf",
        "blwf",
        "half",
        "cjct",
        "pres",
        "abvs",
        "blws",
        "psts",
        "haln",
    ):
        if substitutions.get(tag):
            rules = sorted(set(substitutions[tag]))
            lines.append(f"feature {tag} {{ " + " ".join(rules) + f" }} {tag};")
    for key, entries in [("Above", above), ("Below", below)]:
        a = "above_mark" if key == "Above" else "below_mark"
        for name in entries:
            lines.append(
                f"markClass {name} {anchor(by_name[name]['indic']['anchors'][a])} @MC{key};"
            )
    for key, entries, feature, a in [
        ("Above", above, "abvm", "above_base"),
        ("Below", below, "blwm", "below_base"),
    ]:
        if not entries:
            continue
        rules = []
        stack = []
        for name, o in sorted(by_name.items()):
            e = o.get("indic") or {}
            anchors = e.get("anchors", {})
            if a in anchors and name not in marks:
                rules.append(
                    f"pos {'ligature' if name in ligatures else 'base'} {name} {anchor(anchors[a])} mark @MC{key};"
                )
            if key == "Above" and name in marks and "stack_above_base" in anchors:
                stack.append(
                    f"pos mark {name} {anchor(anchors['stack_above_base'])} mark @MCAbove;"
                )
        if rules or stack:
            lines.append(f"feature {feature} {{")
            if rules:
                lines.extend(rules)
            if stack:
                lines.extend(stack)
            lines.append(f"}} {feature};")

    def group(values):
        return "[" + " ".join(values) + "]" if values else ""

    lines.append(
        "table GDEF { GlyphClassDef "
        + ", ".join(group(v) for v in (base, ligatures, marks, components))
        + "; } GDEF;"
    )
    return "\n".join(lines) + "\n"


def add_features(font, rows, manifest, names):
    source = feature_source(rows, manifest, names)
    addOpenTypeFeaturesFromString(font, source)
    return source
