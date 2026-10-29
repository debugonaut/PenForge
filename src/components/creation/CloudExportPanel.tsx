"use client";
import { DevanagariTextReadiness } from "./DevanagariTextReadiness";

import Link from "next/link";
import type { JobClient } from "@/lib/job-client";
import { JobMonitor } from "./JobMonitor";
import { ContextualFontPreview } from "./ContextualFontPreview";
import { useEffect, useRef, useState } from "react";
import {
  createExportClient,
  exportErrorText,
  type ExportClient,
  type ExportReceipt,
  type ExportRequest,
  type PrivateExport,
} from "@/lib/export-client";
import { downloadWritingBlob } from "@/lib/writing";
import styles from "./CloudReviewPanel.module.css";

export { createExportClient };
import {
  exportRecoveryPrefix,
  readExportRecoveries,
  saveExportRecovery,
  completeExportRecovery,
} from "@/lib/export-recovery";
type Recovery = { key: string; body: ExportRequest; receipt?: ExportReceipt };

export function CloudExportPanel({
  client,
  projectId,
  ownerUid,
  jobClient,
  quiet = false,
  defaultFamily = "My Handwriting",
  defaultSnapshotId = "",
  onReady,
}: {
  client: ExportClient;
  jobClient?: JobClient;
  projectId: string;
  ownerUid: string;
  quiet?: boolean;
  defaultFamily?: string;
  defaultSnapshotId?: string;
  onReady?: (rows: PrivateExport[]) => void;
}) {
  const [opened, setOpened] = useState(quiet);
  const [family, setFamily] = useState(defaultFamily);
  const [snapshot, setSnapshot] = useState(quiet ? "" : defaultSnapshotId);
  const [realism, setRealism] = useState(0);
  const [spacing, setSpacing] = useState(0);
  const [formats, setFormats] = useState<Array<"ttf" | "otf" | "woff2">>([
    "ttf",
    "otf",
    "woff2",
  ]);
  const [rows, setRows] = useState<PrivateExport[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Recovery | null>(null);
  const controller = useRef<AbortController | null>(null);
  const storageKey = exportRecoveryPrefix(ownerUid, projectId);
  useEffect(() => {
    if (quiet || !defaultSnapshotId) return;
    setSnapshot((current) => current || defaultSnapshotId);
  }, [defaultSnapshotId, quiet]);
  useEffect(() => {
    try {
      const saved = readExportRecoveries(localStorage, storageKey)[0];
      if (saved) setPending(saved);
    } catch {
      /* Retain malformed recovery bytes. */
    }
    return () => controller.current?.abort();
  }, [storageKey]);
  async function perform(action: (signal: AbortSignal) => Promise<void>) {
    controller.current?.abort();
    const cancellation = new AbortController();
    controller.current = cancellation;
    setBusy(true);
    setError("");
    try {
      await action(cancellation.signal);
    } catch (cause) {
      if (!cancellation.signal.aborted) setError(exportErrorText(cause));
    } finally {
      if (!cancellation.signal.aborted) setBusy(false);
    }
  }
  async function check(receipt: ExportReceipt, signal: AbortSignal) {
    const result: PrivateExport[] = [];
    for (const id of receipt.export_ids)
      result.push(await client.get(id, signal));
    if (!signal.aborted) setRows(result);
    const finished =
      result.length > 0 && result.every((row) => row.status !== "pending");
    if (finished && !signal.aborted) onReady?.(result);
    return finished;
  }
  function build() {
    void perform(async (signal) => {
      const options = {
        ...(realism ? { realism } : {}),
        ...(spacing ? { spacing } : {}),
      };
      const attempt: Recovery = pending ?? {
        key: crypto.randomUUID(),
        body: {
          formats,
          family_name: family,
          ...(Object.keys(options).length ? { options } : {}),
          ...(!quiet && snapshot.trim()
            ? { snapshot_id: snapshot.trim() }
            : {}),
        },
      };
      // Independent owner/project keys preserve the exact unknown-commit request.
      saveExportRecovery(localStorage, storageKey, attempt);
      setPending(attempt);
      const receipt =
        attempt.receipt ??
        (await client.create(projectId, attempt.body, attempt.key, signal));
      const saved = { ...attempt, receipt };
      saveExportRecovery(localStorage, storageKey, saved);
      if (signal.aborted) return;
      setPending(saved);
      while (!signal.aborted) {
        if (await check(receipt, signal)) break;
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, 1000);
          signal.addEventListener(
            "abort",
            () => {
              clearTimeout(timer);
              resolve();
            },
            { once: true },
          );
        });
      }
      if (!signal.aborted) {
        completeExportRecovery(localStorage, storageKey, saved);
        setPending(readExportRecoveries(localStorage, storageKey)[0] ?? null);
      }
    });
  }
  return (
    <section className={styles.panel} aria-label="Private font export">
      {!quiet && (
        <>
          <div className={styles.heading}>
            <strong>Build your approved font</strong>
            <span>PRIVATE · REAL FONT EXPORT</span>
          </div>
          <p>
            Approved immutable shapes compile into installable fonts. Partial
            coverage is allowed; technical validation does not measure
            handwriting accuracy.
          </p>
          <button
            type="button"
            onClick={() => setOpened((value) => !value)}
            aria-expanded={opened}
          >
            {opened ? "Close private font export" : "Open private font export"}
          </button>
        </>
      )}
      {quiet && (
        <p className={styles.quietNote}>
          Your approved letters become a real installable font. Missing letters
          use a fallback; nothing is marked complete that isn’t valid.
        </p>
      )}
      {opened && (
        <>
          <fieldset
            className={quiet ? styles.quietFields : styles.fields}
            disabled={busy || !!pending}
          >
            <legend>{quiet ? "Choose formats" : "Frozen build inputs"}</legend>
            {!quiet && (
              <>
                <label>
                  Font family
                  <input
                    value={family}
                    maxLength={80}
                    onChange={(event) => setFamily(event.target.value)}
                  />
                </label>
                <label>
                  Owned milestone snapshot ID (optional)
                  <input
                    value={snapshot}
                    onChange={(event) => setSnapshot(event.target.value)}
                    placeholder="Create an export snapshot when blank"
                  />
                </label>
              </>
            )}
            <div className={styles.actions}>
              {(["ttf", "otf", "woff2"] as const).map((format) => (
                <label key={format}>
                  <input
                    type="checkbox"
                    checked={formats.includes(format)}
                    onChange={(event) =>
                      setFormats((previous) =>
                        event.target.checked
                          ? [...previous, format]
                          : previous.filter((item) => item !== format),
                      )
                    }
                  />
                  {format.toUpperCase()}
                </label>
              ))}
            </div>
            {!quiet && (
              <>
                <label>
                  Authentic variation
                  <select
                    value={realism}
                    onChange={(event) => setRealism(Number(event.target.value))}
                  >
                    <option value={0}>Base shapes only</option>
                    <option value={50}>Up to two approved forms</option>
                    <option value={100}>Up to three approved forms</option>
                  </select>
                </label>
                <small>
                  Uses distinct approved samples. Letters without alternatives
                  keep their base shape.
                </small>
              </>
            )}
            <label>
              Letter spacing
              <select
                value={spacing}
                onChange={(event) => setSpacing(Number(event.target.value))}
              >
                <option value={0}>As written</option>
                <option value={50}>Evens the widest gaps</option>
                <option value={100}>Even spacing</option>
              </select>
            </label>
            <small>
              Measures the gaps between your approved letters and evens out the
              largest ones, by at most 2% of the letter height. Apps that ignore
              kerning keep the original spacing.
            </small>
            {!quiet && (
              <small>
                1000 units/em · ascent 1000 · descent −300 · 1.5-unit conversion
                tolerance · installable embedding. Geometry outside these bounds
                needs review before export.
              </small>
            )}
          </fieldset>
          {pending && (
            <p>
              Saved request: {pending.body.family_name} ·{" "}
              {pending.body.formats.join(", ")} · key {pending.key}. Retrying
              preserves these exact inputs.
            </p>
          )}
          <div className={styles.actions}>
            <button
              type="button"
              disabled={busy || !formats.length || !family.trim()}
              onClick={build}
            >
              {pending
                ? "Resume saved export request"
                : "Build approved snapshot"}
            </button>
            {busy && (
              <button
                type="button"
                onClick={() => {
                  controller.current?.abort();
                  setBusy(false);
                }}
              >
                Stop checking; keep server build
              </button>
            )}
          </div>
          {pending?.receipt && jobClient && (
            <JobMonitor client={jobClient} jobId={pending.receipt.job_id} />
          )}
          <div aria-live="polite">
            {rows.map((row) => (
              <article className={styles.notice} key={row.id}>
                <strong>
                  {row.format.toUpperCase()} · {row.stage}
                </strong>
                {row.status === "pending" && (
                  <>
                    <progress
                      value={row.progress.completed_units}
                      max={row.progress.total_units || 1}
                      aria-label={`${row.format} measured work`}
                    />
                    <span>
                      {row.progress.completed_units} /{" "}
                      {row.progress.total_units}{" "}
                      {row.progress.basis.replaceAll("_", " ")} · time estimate
                      unavailable. Counts restart for each format and stage.
                    </span>
                  </>
                )}
                {row.coverage_report && (
                  <p>
                    {String(row.coverage_report.mapped)} mapped characters ·{" "}
                    {Array.isArray(row.coverage_report.missing)
                      ? row.coverage_report.missing.length
                      : "Unknown"}{" "}
                    missing{" "}
                    {row.coverage_report.script_code === "Deva"
                      ? "characters"
                      : "ASCII characters"}{" "}
                    ·{" "}
                    {row.coverage_report.script_code === "Deva"
                      ? "Devanagari is experimental. Only tested words have shaping checks."
                      : row.coverage_report.complete_english
                        ? "Complete English inventory and validation"
                        : "Partial font — missing characters use fallback"}
                    .
                  </p>
                )}
                {row.coverage_report?.script_code === "Deva" && (
                  <DevanagariTextReadiness coverage={row.coverage_report} />
                )}
                {row.validation_status && (
                  <p>
                    Validation: {row.validation_status.replaceAll("_", " ")}.{" "}
                    {Array.isArray(row.validation_report?.warnings)
                      ? row.validation_report.warnings
                          .join(", ")
                          .replaceAll("_", " ")
                      : ""}
                  </p>
                )}
                {row.status === "failed" && (
                  <p role="alert">
                    This export failed and has no font download. Review the
                    geometry or validator report and create a new build.
                  </p>
                )}
                {row.validation_report && !quiet && (
                  <details>
                    <summary>Validation layers and coverage report</summary>
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                        fontSize: 11,
                      }}
                    >
                      {JSON.stringify(row.validation_report, null, 2)}
                    </pre>
                  </details>
                )}
                {row.status === "ready" && (
                  <div className={styles.actions}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void perform(async (signal) => {
                          const result = await client.font(row.id, signal);
                          if (!signal.aborted)
                            downloadWritingBlob(
                              result.blob,
                              `penforge-${row.id}.${row.format}`,
                            );
                        })
                      }
                    >
                      Download {row.format.toUpperCase()}
                    </button>
                    <Link href={`/write?export=${encodeURIComponent(row.id)}`}>
                      Use generated font in Writing Desk
                    </Link>
                  </div>
                )}
                {row.status === "ready" &&
                row.build_manifest?.options?.realism &&
                Array.isArray(
                  (
                    row.coverage_report?.contextual_alternates as
                      { enabled_codepoints?: unknown } | undefined
                  )?.enabled_codepoints,
                ) &&
                (
                  row.coverage_report?.contextual_alternates as {
                    enabled_codepoints: number[];
                  }
                ).enabled_codepoints.length > 0 ? (
                  <ContextualFontPreview client={client} row={row} />
                ) : null}
              </article>
            ))}
          </div>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
        </>
      )}
    </section>
  );
}
