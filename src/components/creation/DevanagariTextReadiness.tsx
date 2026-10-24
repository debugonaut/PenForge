"use client";
import { useState } from "react";
import { graphemes } from "../../../packages/contracts/src/unicode";
export function DevanagariTextReadiness({
  coverage,
}: {
  coverage: Record<string, unknown>;
}) {
  const [text, setText] = useState("");
  const known = (coverage.tested_clusters ?? []) as number[][],
    fallback = (coverage.fallback_only_clusters ?? []) as number[][];
  let message = "";
  try {
    const seq = graphemes(text).filter((s) => s.some((cp) => cp !== 32));
    const matches = (list: number[][], s: number[]) =>
      list.some((a) => a.join() === s.join());
    const unknown = seq.filter(
      (s) => !matches(known, s) && !matches(fallback, s),
    );
    message = unknown.length
      ? "Some characters or forms need capture, review or a shaping check."
      : seq.some((s) => matches(fallback, s))
        ? "This text uses a tested fallback form. Review its appearance."
        : seq.length
          ? "This text has passed the engineering shaping checks. Review its appearance."
          : "";
  } catch {
    message = "This text contains an unsupported Unicode sequence.";
  }
  return (
    <label>
      Text to check
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={2000}
        placeholder="कि क्ष हूँ"
      />
      <small role="status">
        {message || "Devanagari remains experimental."}
      </small>
    </label>
  );
}
