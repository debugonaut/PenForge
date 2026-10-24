"use client";

import type { MeasuredStyle as Profile } from "@/lib/extraction-client";
import styles from "./CloudReviewPanel.module.css";

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function MeasuredStyle({ profile }: { profile: Profile }) {
  const summary = object(profile.metrics.summary);
  const lean = number(object(summary.slant_proxy_degrees).median);
  const cap = number(object(summary.cap_height).median);
  const xheight = number(object(summary.x_height).median);
  const refs = Array.isArray(profile.metrics.references)
    ? profile.metrics.references.slice(0, 12)
    : [];
  return (
    <div className={styles.notice} aria-label="Measured approved style">
      <strong>Your approved shapes · measurement {profile.version}</strong>
      <div className={styles.measurements}>
        <div>
          <small>Outline lean</small>
          <b>{lean === null ? "Not measured" : `${lean.toFixed(1)}°`}</b>
        </div>
        <div>
          <small>Capital height</small>
          <b>{cap === null ? "Needs capitals" : `${cap.toFixed(0)} units`}</b>
        </div>
        <div>
          <small>Small-letter height</small>
          <b>
            {xheight === null
              ? "Needs small letters"
              : `${xheight.toFixed(0)} units`}
          </b>
        </div>
      </div>
      {cap !== null && xheight !== null && (
        <svg
          viewBox="0 0 320 72"
          role="img"
          aria-label={`Capital height ${cap.toFixed(0)}; small-letter height ${xheight.toFixed(0)} font units`}
          className={styles.proportions}
        >
          <text x="0" y="23">
            Capitals
          </text>
          <rect
            x="100"
            y="9"
            height="18"
            width={Math.max(
              1,
              Math.min(200, (cap / Math.max(cap, xheight, 1)) * 200),
            )}
          />
          <text x="0" y="57">
            Small letters
          </text>
          <rect
            x="100"
            y="43"
            height="18"
            width={Math.max(
              1,
              Math.min(200, (xheight / Math.max(cap, xheight, 1)) * 200),
            )}
          />
        </svg>
      )}
      {refs.length > 0 && (
        <details>
          <summary>Compare widths and space around each shape</summary>
          <div className={styles.widths}>
            {refs.map((raw, index) => {
              const row = object(raw),
                geometry = object(row.geometry);
              const cp = Array.isArray(row.codepoints)
                ? row.codepoints[0]
                : null;
              const width = number(geometry.width);
              const left = number(geometry.left_bearing),
                right = number(geometry.right_bearing);
              return (
                <div key={index}>
                  <strong>
                    {typeof cp === "number" && cp >= 0 && cp <= 0x10ffff
                      ? String.fromCodePoint(cp)
                      : "Shape"}
                  </strong>
                  <meter
                    min="0"
                    max="1000"
                    value={Math.max(0, Math.min(1000, width ?? 0))}
                    aria-label={`Shape ${index + 1} width`}
                  />
                  <small>
                    {width?.toFixed(0) ?? "—"} units · left{" "}
                    {left?.toFixed(0) ?? "—"} · right {right?.toFixed(0) ?? "—"}
                  </small>
                </div>
              );
            })}
          </div>
        </details>
      )}
      <small>
        Measured from this saved set of approved outlines. Outline lean and ink
        widths describe geometry. Line rhythm and pressure are shown only when
        captured.
      </small>
    </div>
  );
}
