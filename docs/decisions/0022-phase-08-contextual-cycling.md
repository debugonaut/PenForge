# 0022 — Left-to-right adjacent-repeat cycling

Status: accepted for local opt-in Phase 08 after N=2 then N=3 engineering checks.
Date: 2026-10-20.

FontTools feaLib builds one chained contextual calt lookup referencing single
substitutions. For each enabled set [base, alt1, alt2], previous base changes the
current base to alt1; previous alt1 changes current base to alt2. After alt2, no rule
matches and current base remains base. N=2 uses only the first rule. HarfBuzz's
left-to-right buffer update supplies deterministic state within an adjacent repeat.
Different characters, spaces/punctuation and new shaping runs reset naturally.

Realism 0 uses base only, 1–50 permits two forms, 51–100 permits three. Only approved
authentic samples participate. Source byte hash then revision hash orders alternatives,
independent of insertion/UUID/variant order. Duplicate source bytes cannot add a form.
Insufficient samples preserve the base font. No random geometry, joins or ligatures.

All formats require base cmap/metrics, feature-off fallback, no added .notdef, repeat
invariants, deterministic output and all existing five validation layers before export
activation. The suite covers eeeeeeee, hello wellness, spaces, digits, punctuation,
word/run boundaries and independent-process reproducibility. Exact glyph sequences and
fixture-font hashes are retained under artifacts/phase08/fonts. These authored geometry
controls establish font engineering, not writer fidelity or human preference.

Some applications disable calt; features-off uses approved base outlines. Opt-in fonts
carry this warning. Old realism-zero manifests retain the same serialized options and
base compilation behavior. New exports do not modify previous font bytes.
