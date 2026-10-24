"use client";
import { useState } from "react";
import type { CanonicalOutline } from "@/lib/api-boundary";
import { contentHash } from "../../../packages/contracts/src/canonical";
import styles from "./CloudReviewPanel.module.css";

type Evidence = NonNullable<CanonicalOutline["indic"]>;
const choices = [
  "Character",
  "Above mark",
  "Below mark",
  "Half form",
  "Reph",
  "Rakar",
  "Joined form",
  "Nukta form",
];
export function IndicGeometryReview({
  outline,
  onChange,
  disabled,
}: {
  outline: CanonicalOutline;
  onChange: (value: CanonicalOutline) => void;
  disabled: boolean;
}) {
  const [kind, setKind] = useState("Character"),
    [x, setX] = useState(""),
    [y, setY] = useState(""),
    [error, setError] = useState("");
  const [side, setSide] = useState("Above");
  async function apply() {
    setError("");
    const mark = kind.includes("mark"),
      cps = outline.codepoints;
    if (
      (mark || kind === "Character" || kind === "Reph") &&
      (!x.trim() || !y.trim() || ![Number(x), Number(y)].every(Number.isFinite))
    ) {
      setError("Enter the attachment point in font units.");
      return;
    }
    const role: Evidence["role"] = mark
      ? "mark"
      : kind === "Half form"
        ? "half"
        : kind === "Reph"
          ? "reph"
          : kind === "Rakar"
            ? "rakar"
            : kind === "Joined form"
              ? "conjunct"
              : "base";
    const feature =
      kind === "Half form"
        ? "half"
        : kind === "Reph"
          ? "rphf"
          : kind === "Rakar"
            ? "rkrf"
            : kind === "Joined form"
              ? "akhn"
              : kind === "Nukta form"
                ? "nukt"
                : null;
    if (
      (feature === "half" && (cps.length !== 2 || cps[1] !== 2381)) ||
      (feature === "rphf" && cps.join() !== "2352,2381") ||
      (feature === "rkrf" && cps.slice(-2).join() !== "2381,2352") ||
      (feature === "akhn" && !cps.includes(2381)) ||
      (feature === "nukt" && !cps.includes(2364))
    ) {
      setError(
        "This form needs a matching captured sequence. Capture and review it first.",
      );
      return;
    }
    const base = { ...outline };
    delete base.indic;
    if (mark || role === "reph")
      base.metrics = { ...base.metrics, advance_width: 0 };
    const anchors: Evidence["anchors"] =
      outline.indic?.role === role ? { ...outline.indic.anchors } : {};
    if (x.trim() && y.trim())
      anchors[
        mark
          ? kind === "Above mark"
            ? "above_mark"
            : "below_mark"
          : role === "reph"
            ? "above_mark"
            : side === "Below"
              ? "below_base"
              : "above_base"
      ] = { x: Number(x), y: Number(y) };
    const indic: Evidence = {
      schema_version: 1,
      role,
      source_classification: "owner_engineering_record",
      geometry_hash: await contentHash(JSON.parse(JSON.stringify(base))),
      anchors,
      substitution: feature ? { feature, input_codepoints: [...cps] } : null,
    };
    onChange({ ...base, indic });
  }
  return (
    <details>
      <summary>Character and mark positioning</summary>
      <fieldset className={styles.fields} disabled={disabled}>
        <p>
          Use a captured shape. Positioning stays pending until you save and
          approve it.
        </p>
        <label>
          Shape
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {choices.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Attachment
          <select value={side} onChange={(e) => setSide(e.target.value)}>
            <option>Above</option>
            <option>Below</option>
          </select>
        </label>
        <label>
          Attachment horizontal position
          <input
            type="number"
            value={x}
            onChange={(e) => setX(e.target.value)}
          />
        </label>
        <label>
          Attachment vertical position
          <input
            type="number"
            value={y}
            onChange={(e) => setY(e.target.value)}
          />
        </label>
        <button type="button" onClick={() => void apply()}>
          Keep positioning for review
        </button>
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </details>
  );
}
