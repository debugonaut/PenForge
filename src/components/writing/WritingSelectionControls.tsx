"use client";

import type { WritingDocument } from "@/lib/writing";
import {
  resolvedWritingStyle,
  WRITING_STYLE_BOUNDS,
  type WritingSelection,
  type WritingStyle,
} from "@/lib/writing-text";
import styles from "./WritingDesk.module.css";

export default function WritingSelectionControls({
  document,
  selection,
  onChange,
  onReset,
  id,
}: {
  document: WritingDocument;
  selection: WritingSelection;
  onChange: (patch: WritingStyle, key: string) => void;
  onReset: () => void;
  id: string;
}) {
  const selected = document[selection.field].slice(
    selection.start,
    selection.end,
  );
  const titleScale =
    document.format === "card"
      ? 1.5
      : document.format === "letter"
        ? 1.34
        : 1.25;
  const titleMax =
    document.format === "card" ? 94 : document.format === "letter" ? 84 : 76;
  const base = {
    fontSize:
      selection.field === "title"
        ? Math.min(titleMax, document.fontSize * titleScale)
        : document.fontSize,
    weight:
      selection.field === "title"
        ? Math.min(700, document.weight + 50)
        : document.weight,
    spacing: document.spacing,
    baselineShift: 0,
  };
  const ranges = document.textStyles || [];
  const value = resolvedWritingStyle(
    ranges,
    selection.field,
    selection.start,
    base,
  );
  const points = [
    selection.start,
    ...ranges
      .filter((range) => range.field === selection.field)
      .flatMap((range) => [range.start, range.end])
      .filter((point) => point > selection.start && point < selection.end),
  ];
  const mixed = (key: keyof typeof base) =>
    points.some(
      (point) =>
        resolvedWritingStyle(ranges, selection.field, point, base)[key] !==
        value[key],
    );
  const changed = document.textStyles?.some(
    (range) =>
      range.field === selection.field &&
      range.start < selection.end &&
      range.end > selection.start,
  );
  const spaces = /^ +$/.test(selected);
  const summary = spaces
    ? `${selected.length} ${selected.length === 1 ? "space" : "spaces"}`
    : selected.replace(/\n/g, " ↵ ");
  return (
    <>
      <div className={styles.selectionSummary}>
        <span title={summary}>{summary}</span>
        <button type="button" onClick={onReset} disabled={!changed}>
          Reset selection
        </button>
      </div>
      <div className={styles.tuningGrid}>
        {(
          [
            { key: "fontSize", name: "Size", step: 1, suffix: "px" },
            { key: "weight", name: "Weight", step: 10, suffix: "" },
            { key: "spacing", name: "Spacing", step: 0.1, suffix: "px" },
            {
              key: "baselineShift",
              name: "Vertical position",
              step: 1,
              suffix: "px",
            },
          ] as const
        ).map((control) => (
          <label
            key={control.key}
            htmlFor={`${id}-selection-${control.key}`}
            className={styles.tuningControl}
          >
            <span className={styles.tuningLabel}>
              {control.name}
              <output>
                {mixed(control.key)
                  ? "Mixed"
                  : `${Number(value[control.key].toFixed(2))}${control.suffix}`}
              </output>
            </span>
            <input
              id={`${id}-selection-${control.key}`}
              type="range"
              aria-label={`Selection ${control.name.toLowerCase()}`}
              min={WRITING_STYLE_BOUNDS[control.key][0]}
              max={WRITING_STYLE_BOUNDS[control.key][1]}
              step={control.step}
              value={value[control.key]}
              aria-valuetext={
                mixed(control.key)
                  ? "Mixed values. Adjust to apply to the whole selection."
                  : undefined
              }
              onChange={(event) =>
                onChange(
                  { [control.key]: Number(event.target.value) },
                  control.key,
                )
              }
            />
          </label>
        ))}
      </div>
      {spaces && (
        <div className={styles.spaceActions}>
          <button
            type="button"
            disabled={value.spacing <= -1}
            onClick={() =>
              onChange(
                {
                  spacing: Math.max(
                    -1,
                    Number((value.spacing - 0.5).toFixed(1)),
                  ),
                },
                "space",
              )
            }
          >
            Tighter
          </button>
          <button
            type="button"
            disabled={value.spacing >= 16}
            onClick={() =>
              onChange(
                {
                  spacing: Math.min(
                    16,
                    Number((value.spacing + 0.5).toFixed(1)),
                  ),
                },
                "space",
              )
            }
          >
            Wider
          </button>
        </div>
      )}
    </>
  );
}
