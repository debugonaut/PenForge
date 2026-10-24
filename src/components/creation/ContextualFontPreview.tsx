"use client";

import { useEffect, useRef, useState } from "react";
import type { ExportClient, PrivateExport } from "@/lib/export-client";
import styles from "./CloudReviewPanel.module.css";

export function ContextualFontPreview({
  client,
  row,
}: {
  client: ExportClient;
  row: PrivateExport;
}) {
  const [family, setFamily] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [text, setText] = useState("eeeeeeee hello wellness");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const face = useRef<FontFace | null>(null);
  const cancellation = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      cancellation.current?.abort();
      if (face.current) document.fonts.delete(face.current);
    },
    [row.id, client],
  );
  return (
    <div className={styles.fontPreview}>
      {!family && (
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            const controller = new AbortController();
            cancellation.current = controller;
            setBusy(true);
            setError("");
            try {
              const result = await client.font(row.id, controller.signal);
              const name = `PF${row.id.replaceAll("-", "")}`;
              const loaded = await new FontFace(
                name,
                await result.blob.arrayBuffer(),
              ).load();
              if (controller.signal.aborted) return;
              document.fonts.add(loaded);
              face.current = loaded;
              setFamily(name);
            } catch {
              if (!controller.signal.aborted)
                setError("The font could not be loaded. Try again.");
            } finally {
              if (!controller.signal.aborted) setBusy(false);
            }
          }}
        >
          {busy ? "Loading font…" : "Compare authentic alternates"}
        </button>
      )}
      {family && (
        <>
          <button
            type="button"
            aria-pressed={enabled}
            onClick={() => setEnabled((v) => !v)}
          >
            {enabled ? "Alternates on" : "Base shapes only"}
          </button>
          <label>
            Try your validated font
            <textarea
              value={text}
              maxLength={1000}
              onChange={(event) => setText(event.target.value)}
              style={{
                fontFamily: `"${family}"`,
                fontSize: 32,
                fontFeatureSettings: `"calt" ${enabled ? 1 : 0}, "liga" 0, "kern" 0`,
              }}
            />
          </label>
          <small>
            Uses this downloaded font. Missing characters use system fallback.
            Some apps turn contextual alternates off.
          </small>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
