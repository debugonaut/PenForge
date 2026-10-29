"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { captureCodepoints } from "../../../packages/contracts/src/unicode";
import type { JobClient } from "@/lib/job-client";
import { IndicGeometryReview } from "./IndicGeometryReview";
import { JobMonitor } from "./JobMonitor";
import { MeasuredStyle } from "./MeasuredStyle";
import type { CanonicalOutline } from "@/lib/api-boundary";
import {
  outlinePath,
  outlineViewBox,
  transformOutline,
  fitOutlineSpacing,
} from "@/lib/canonical-geometry";
import {
  extractionErrorText,
  type AnalysisAssignment,
  type AnalysisJob,
  type ExtractionClient,
  type FollowUp,
  type ReviewCandidate,
  type ReviewCoverage,
  type ReviewGlyph,
  type ReviewSample,
  type EvidenceArtifact,
  type MeasuredStyle as StyleProfile,
  type ProcessingCapability,
  ExtractionError,
} from "@/lib/extraction-client";
import type { CaptureOperation } from "@/lib/capture-recovery";
import { CANVAS_BRUSH_WIDTH } from "@/lib/stroke-capture";
import styles from "./CloudReviewPanel.module.css";

export function CanonicalGlyph({
  outline,
  label,
}: {
  outline: CanonicalOutline;
  label: string;
}) {
  try {
    const path = outlinePath(outline);
    return (
      <svg
        className={styles.geometry}
        viewBox={outlineViewBox(outline)}
        role="img"
        aria-label={label}
      >
        <g transform="scale(1 -1)">
          <path
            d={`M ${-outline.units_per_em} 0 H ${outline.units_per_em * 2}`}
            className={styles.baseline}
          />
          <path d={path} fill="currentColor" fillRule="evenodd" />
        </g>
      </svg>
    );
  } catch {
    return (
      <p role="alert">
        This outline cannot be displayed safely. Reload its server geometry.
      </p>
    );
  }
}

function character(codepoints: number[]) {
  return codepoints.length === 1 && codepoints[0] === 32
    ? "Space"
    : String.fromCodePoint(...codepoints);
}
function description(origin: string) {
  const labels: Record<string, string> = {
    authentic_extraction: "Extracted from the captured image",
    manual_drawing: "Derived from your digital drawing",
    geometric_reconstruction: "Synthetic geometric proposal",
    style_conditioned_generation: "Synthetic generated proposal",
  };
  return labels[origin] ?? origin.replaceAll("_", " ");
}
function delay(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 1000);
    signal.addEventListener("abort", abort, { once: true });
  });
}

function PrivateEvidence({
  client,
  value,
  label,
}: {
  client: ExtractionClient;
  value: unknown;
  label: string;
}) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const cancellation = useRef<AbortController | null>(null);
  const objectUrl = useRef("");
  useEffect(
    () => () => {
      cancellation.current?.abort();
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    },
    [],
  );
  if (!value || typeof value !== "object" || !("artifact_id" in value))
    return null;
  return (
    <div className={styles.evidence}>
      <button
        type="button"
        disabled={busy || !!url}
        onClick={async () => {
          const controller = new AbortController();
          cancellation.current = controller;
          setBusy(true);
          setError("");
          try {
            const blob = await client.evidence(
              value as EvidenceArtifact,
              controller.signal,
            );
            if (!controller.signal.aborted) {
              objectUrl.current = URL.createObjectURL(blob);
              setUrl(objectUrl.current);
            }
          } catch {
            if (!controller.signal.aborted)
              setError(
                "Private evidence could not be verified. Retry the source review.",
              );
          } finally {
            if (!controller.signal.aborted) setBusy(false);
          }
        }}
      >
        {busy ? "Loading private evidence…" : `Inspect ${label}`}
      </button>
      {url && (
        <figure>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt={label} />
          <figcaption>{label} · immutable captured evidence</figcaption>
        </figure>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

const retakeGuidance: Record<string, string> = {
  too_blurry:
    "Hold the camera steady, focus on the writing and retake in brighter light.",
  low_resolution:
    "Move closer while keeping all four markers visible; upload the original resolution.",
  page_not_found:
    "Capture the full sheet with all four corner markers visible and unobstructed.",
  page_identity_unreadable: "Keep the public QR sharp and uncovered.",
  template_page_mismatch:
    "Choose the page/version that matches the captured sheet.",
  marker_order_mismatch:
    "Use an uncut sheet; keep each marker in its printed corner.",
  template_marker_mismatch:
    "Download the pinned template version and capture its matching page.",
  capture_dimensions_required:
    "Make a new isolated drawing with its original artboard dimensions.",
  isolated_samples_required:
    "Write isolated marks on the pinned sheet or draw one character at a time.",
  empty_ink: "Draw one visible isolated character and save a new source.",
};

/** Explicit cloud review stays separate from the wizard's local demo alphabet. */
export function CloudReviewPanel({
  client,
  projectId,
  ownerUid,
  scriptCode = "Latn",
  operations,
  jobClient,
  onFollowUp,
  onChanged,
  startOpen = false,
}: {
  client: ExtractionClient;
  jobClient?: JobClient;
  projectId: string;
  ownerUid: string;
  scriptCode?: string;
  operations: CaptureOperation[];
  onFollowUp?: (prompt: FollowUp) => void;
  onChanged?: (glyphs: ReviewGlyph[], coverage: ReviewCoverage) => void;
  startOpen?: boolean;
}) {
  const generation = useRef(0);
  const cancellation = useRef<AbortController | null>(null);
  const attempt = useRef<{
    fingerprint: string;
    key: string;
    jobId?: string;
  } | null>(null);
  const requestKeys = useRef(new Map<string, string>());
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const [opened, setOpened] = useState(startOpen);
  const [busy, setBusy] = useState(false);
  const [samples, setSamples] = useState<ReviewSample[]>([]);
  const [sampleId, setSampleId] = useState("");
  const [assignment, setAssignment] = useState("");
  const [baseline, setBaseline] = useState("0.75");
  const [unitsPerPx, setUnitsPerPx] = useState("2");
  const [penWidth, setPenWidth] = useState(String(CANVAS_BRUSH_WIDTH));
  const [glyphs, setGlyphs] = useState<ReviewGlyph[]>([]);
  const [detail, setDetail] = useState<{
    data: ReviewGlyph;
    etag: string | null;
  } | null>(null);
  const [candidates, setCandidates] = useState<ReviewCandidate[]>([]);
  const [recommendedId, setRecommendedId] = useState<string | null>(null);
  const [candidateCursor, setCandidateCursor] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<ReviewCoverage | null>(null);
  const [followUp, setFollowUp] = useState<FollowUp | null>(null);
  const [job, setJob] = useState<AnalysisJob | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [scale, setScale] = useState("100");
  const [dx, setDx] = useState("0");
  const [dy, setDy] = useState("0");
  const [draft, setDraft] = useState<CanonicalOutline | null>(null);
  const [rollbackId, setRollbackId] = useState("");
  const [snapshotLabel, setSnapshotLabel] = useState("");
  const [spaceAdvance, setSpaceAdvance] = useState("300");
  const [profile, setProfile] = useState<StyleProfile | null>(null);
  const [capabilities, setCapabilities] = useState<ProcessingCapability[]>([]);

  useEffect(() => {
    generation.current += 1;
    cancellation.current?.abort();
    attempt.current = null;
    requestKeys.current.clear();
    setOpened(startOpen);
    setSamples([]);
    setGlyphs([]);
    setDetail(null);
    setCandidates([]);
    setCoverage(null);
    setFollowUp(null);
    setJob(null);
    setBusy(false);
    setError("");
    setNotice("");
    setDraft(null);
    setProfile(null);
    setCapabilities([]);
    return () => {
      generation.current += 1;
      cancellation.current?.abort();
    };
  }, [client, projectId, ownerUid, startOpen]);

  function stableKey(operation: string, body: unknown) {
    const fingerprint = JSON.stringify({ operation, body });
    const previous = requestKeys.current.get(fingerprint);
    if (previous) return previous;
    const key = crypto.randomUUID();
    requestKeys.current.set(fingerprint, key);
    return key;
  }

  async function perform(
    action: (signal: AbortSignal, current: () => boolean) => Promise<void>,
  ) {
    const version = generation.current;
    const controller = new AbortController();
    cancellation.current?.abort();
    cancellation.current = controller;
    const current = () =>
      generation.current === version && !controller.signal.aborted;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action(controller.signal, current);
    } catch (cause) {
      if (current()) setError(extractionErrorText(cause));
    } finally {
      if (current()) setBusy(false);
    }
  }
  const refresh = useCallback(
    async (signal: AbortSignal, current: () => boolean) => {
      const [sourceRows, letters, counts, prompt, measured, available] =
        await Promise.all([
          client.samples(projectId, signal),
          client.glyphs(projectId, signal),
          client.coverage(projectId, signal),
          client.followUp(projectId, signal),
          client.styleProfile(projectId, signal).catch((cause) => {
            if (cause instanceof ExtractionError && cause.status === 404)
              return null;
            throw cause;
          }),
          client.capabilities(projectId, signal),
        ]);
      if (!current()) return;
      setSamples(sourceRows);
      setGlyphs(letters);
      setCoverage(counts);
      changed.current?.(letters, counts);
      setFollowUp(prompt);
      setProfile(measured);
      setCapabilities(available);
      setSampleId((previous) =>
        sourceRows.some((source) => source.sample_id === previous)
          ? previous
          : (sourceRows[0]?.sample_id ?? ""),
      );
    },
    [client, projectId],
  );
  useEffect(() => {
    if (!startOpen) return;
    const version = generation.current;
    const controller = new AbortController();
    cancellation.current = controller;
    const current = () =>
      generation.current === version && !controller.signal.aborted;
    setBusy(true);
    void refresh(controller.signal, current)
      .catch((cause) => {
        if (current()) setError(extractionErrorText(cause));
      })
      .finally(() => {
        if (current()) setBusy(false);
      });
    return () => controller.abort();
  }, [ownerUid, refresh, startOpen]);
  function openReview() {
    setOpened(true);
    void perform(refresh);
  }
  async function inspect(
    glyphId: string,
    signal: AbortSignal,
    current: () => boolean,
    retainDraft = false,
  ) {
    const [letter, alternatives] = await Promise.all([
      client.glyph(projectId, glyphId, signal),
      client.candidates(projectId, glyphId, signal),
    ]);
    if (!current()) return;
    setDetail(letter);
    setCandidates(alternatives.items);
    setRecommendedId(letter.data.recommended_candidate_id);
    setCandidateCursor(alternatives.next_cursor);
    setRollbackId("");
    if (!retainDraft) {
      setDraft(null);
      setScale("100");
      setDx("0");
      setDy("0");
    }
  }
  const source = samples.find((item) => item.sample_id === sampleId);
  const localSource = operations.find(
    (item) =>
      item.ownerUid === ownerUid &&
      item.projectId === projectId &&
      item.sampleId === sampleId,
  )?.strokeDocument;
  const captureSize = source?.capture_size_px ?? localSource?.capture_size_px;
  const isDrawing = source?.source_type === "canvas_drawing";
  const letter = detail?.data;
  const workingId =
    letter?.working_revision_id ?? letter?.active_revision_id ?? null;
  const revision = letter?.revisions.find((item) => item.id === workingId);
  const shownOutline = draft ?? letter?.outline;

  function analyze() {
    if (!source) return;
    let assignments: AnalysisAssignment[] = [];
    if (isDrawing) {
      let cps: number[];
      try {
        cps = captureCodepoints(assignment, scriptCode);
      } catch {
        setError(
          "Enter one supported character or cluster, exactly as written.",
        );
        return;
      }
      if (
        !captureSize ||
        !Number.isFinite(Number(baseline)) ||
        Number(baseline) <= 0 ||
        Number(baseline) >= 1 ||
        !Number.isFinite(Number(unitsPerPx)) ||
        Number(unitsPerPx) <= 0 ||
        !Number.isFinite(Number(penWidth)) ||
        Number(penWidth) <= 0
      ) {
        setError(
          "Enter a character or cluster and check the capture size, baseline, scale and pen width.",
        );
        return;
      }
      assignments = [
        {
          sample_id: source.sample_id,
          codepoints: cps,
          capture_transform: {
            schema_version: 1,
            units_per_em: 1000,
            baseline_y: Number(baseline),
            units_per_px: Number(unitsPerPx),
            origin_x_px: 0,
            pen_width_px: Number(penWidth),
          },
        },
      ];
    } else if (!source.template) {
      setError(
        "Natural pages and connected words are experimental. Write isolated letters on a pinned template, or draw one isolated character and assign it explicitly.",
      );
      return;
    }
    const fingerprint = JSON.stringify({
      sample_id: source.sample_id,
      assignments,
      drawing_mode:
        isDrawing && scriptCode === "Latn" ? "fixed_round_v1" : undefined,
    });
    if (attempt.current?.fingerprint !== fingerprint)
      attempt.current = { fingerprint, key: crypto.randomUUID() };
    const request = attempt.current;
    void perform(async (signal, current) => {
      let status = request.jobId
        ? await client.job(projectId, request.jobId, signal)
        : await client.analyze(
            projectId,
            [source.sample_id],
            assignments,
            request.key,
            signal,
            isDrawing && scriptCode === "Latn" ? "fixed_round_v1" : undefined,
          );
      request.jobId = status.job_id;
      while (current()) {
        setJob(status);
        if (["succeeded", "failed", "canceled"].includes(status.state)) break;
        await delay(signal);
        status = await client.job(projectId, status.job_id, signal);
      }
      if (!current()) return;
      await refresh(signal, current);
      if (current()) attempt.current = null;
      setNotice(
        status.state === "succeeded"
          ? "Assessment finished. Candidates still need your review and explicit approval."
          : "Analysis did not complete. Keep the source and inspect the reported outcome.",
      );
    });
  }
  function change(action: "approve" | "reject" | "edit" | "rollback") {
    if (!letter || !detail?.etag) return;
    void perform(async (signal, current) => {
      const result = await client.patch(
        projectId,
        letter.id,
        {
          base_revision_id: workingId,
          action,
          ...(action === "edit" && draft ? { outline: draft } : {}),
          ...(action === "rollback" ? { target_revision_id: rollbackId } : {}),
        },
        detail.etag!,
        signal,
      );
      if (!current()) return;
      setDetail(result);
      setDraft(null);
      setScale("100");
      setDx("0");
      setDy("0");
      await refresh(signal, current);
      if (current())
        setNotice(
          action === "rollback"
            ? "A new revision restores that history entry. Inspect and approve it explicitly."
            : action === "approve"
              ? "This revision is approved. Future extraction cannot replace your selection."
              : action === "edit"
                ? "A new pending outline revision is saved. Inspect it before approval."
                : "The pending revision was rejected; approved history is preserved.",
        );
    });
  }
  function previewEdit() {
    if (!revision || !letter?.outline) return;
    try {
      setDraft(
        transformOutline(letter.outline, {
          scale: Number(scale) / 100,
          dx: Number(dx),
          dy: Number(dy),
        }),
      );
      setError("");
    } catch {
      setError(
        "Use a positive uniform scale up to 400% and finite font-unit shifts within the geometry limits.",
      );
    }
  }

  function previewSpacing() {
    if (!revision || !letter?.outline) return;
    try {
      setDraft(fitOutlineSpacing(draft ?? letter.outline));
      setError("");
      setNotice(
        "Side spacing balanced in this preview. Save the pending revision, then approve it if the spacing looks right.",
      );
    } catch {
      setError(
        "Spacing can be adjusted only for an editable drawn outline. The current source and approved version are unchanged.",
      );
    }
  }

  return (
    <section
      className={styles.panel}
      aria-label="Private extraction and review"
    >
      <div className={styles.heading}>
        <strong>Extract and review your shapes</strong>
        <span>PRIVATE · EXPERIMENTAL LOCAL EXTRACTION</span>
      </div>
      <p>
        Capture a character or cluster. Check its shape, approve it, then try
        your font.
      </p>
      <button type="button" onClick={openReview} disabled={busy}>
        {opened
          ? "Refresh private sources and coverage"
          : "Open private extraction and review"}
      </button>
      {opened && (
        <>
          <label>
            Stored private source
            <select
              value={sampleId}
              disabled={busy}
              onChange={(event) => {
                setSampleId(event.target.value);
                setJob(null);
              }}
            >
              {!samples.length && (
                <option value="">Save a private source first</option>
              )}
              {samples.map((item, index) => (
                <option key={item.sample_id} value={item.sample_id}>
                  Source {index + 1} · {item.source_type.replaceAll("_", " ")} ·{" "}
                  {(item.state ?? "pending").replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          {source?.quality_reasons?.length ? (
            <p role="status">
              Retake guidance:{" "}
              {source.quality_reasons
                .map(
                  (reason) =>
                    retakeGuidance[reason] ?? reason.replaceAll("_", " "),
                )
                .join("; ")}
              . Keep every marker visible, use dark ink and even light, and
              leave space around each letter.
            </p>
          ) : null}
          {isDrawing && (
            <fieldset disabled={busy} className={styles.fields}>
              <legend>What did you write?</legend>
              <p>
                Write one character or cluster. Review its shape before
                approving it. Keep the same scale across captures.
              </p>
              {captureSize ? (
                <small>
                  Immutable artboard: {captureSize.width} × {captureSize.height}{" "}
                  px. Baseline is a fraction of its height from the top; shifts
                  use font units.
                </small>
              ) : (
                <p>
                  Capture dimensions are unavailable. Make a new drawing to
                  extract this source.
                </p>
              )}
              <label>
                Character or cluster
                <input
                  value={assignment}
                  maxLength={64}
                  onChange={(event) => setAssignment(event.target.value)}
                  autoComplete="off"
                />
              </label>
              <label>
                Baseline from top
                <input
                  type="number"
                  min="0.01"
                  max="0.99"
                  step="0.01"
                  value={baseline}
                  onChange={(event) => setBaseline(event.target.value)}
                />
              </label>
              <label>
                Font units per artboard pixel
                <input
                  type="number"
                  min="0.01"
                  step="0.1"
                  value={unitsPerPx}
                  onChange={(event) => setUnitsPerPx(event.target.value)}
                />
              </label>
              <label>
                Fallback pen width in pixels
                <input
                  type="number"
                  min="0.1"
                  step="0.1"
                  value={penWidth}
                  onChange={(event) => setPenWidth(event.target.value)}
                />
              </label>
            </fieldset>
          )}
          {source && !isDrawing && !source.template && (
            <p>
              Natural/cursive segmentation is experimental and unavailable here.
              Save a pinned template or make an isolated drawing.
            </p>
          )}
          <button
            type="button"
            disabled={
              busy || !source || (isDrawing ? !captureSize : !source.template)
            }
            onClick={analyze}
          >
            Analyze this stored source
          </button>
          {job && jobClient && (
            <JobMonitor client={jobClient} jobId={job.job_id} />
          )}
          {job && !jobClient && (
            <div role="status" aria-live="polite" className={styles.notice}>
              <strong>
                {job.state.replaceAll("_", " ")} ·{" "}
                {job.stage?.replaceAll("_", " ") ?? "Waiting for a worker"}
              </strong>
              <progress
                max={job.progress.total_units ?? 1}
                value={
                  job.progress.total_units === null
                    ? undefined
                    : job.progress.completed_units
                }
                aria-label="Measured analysis work"
              />
              <span>
                {job.progress.total_units === null
                  ? "Total work is not yet known"
                  : `${job.progress.completed_units} / ${job.progress.total_units} work units`}{" "}
                · Time estimate unavailable
              </span>
              {job.error_code && (
                <span>Outcome: {job.error_code.replaceAll("_", " ")}</span>
              )}
            </div>
          )}
          {coverage && (
            <div
              className={styles.coverage}
              aria-label="Approved Unicode coverage"
            >
              <strong>
                {coverage.script_code === "Deva" ? (
                  `${coverage.user_approved.length} approved characters · marks and words need separate checks`
                ) : (
                  <>
                    {coverage.core_approved} / {coverage.core_required} Latin
                    core · {coverage.ascii_approved} / {coverage.ascii_required}{" "}
                    printable ASCII
                  </>
                )}
              </strong>
              <p>
                {coverage.script_code === "Deva" ? (
                  "Devanagari is experimental. Check the text you want to type after exporting; missing forms need another capture or review."
                ) : (
                  <>
                    Only approved mappings count. Space uses an approved advance
                    metric; .notdef is excluded.{" "}
                    {coverage.complete_font_validated
                      ? "Export validation recorded."
                      : "Font export is not validated; this is not a complete font."}
                  </>
                )}
              </p>
            </div>
          )}
          {followUp?.prompt_text && (
            <div className={styles.notice}>
              <strong>Targeted follow-up</strong>
              <p>{followUp.prompt_text}</p>
              <small>
                Prompt text is context, never proof of the marks you wrote. Use
                isolated samples when connected writing is ambiguous.
              </small>
              {onFollowUp && (
                <button
                  type="button"
                  onClick={() => {
                    onFollowUp(followUp);
                    setNotice("");
                  }}
                >
                  Use this follow-up for my next capture
                </button>
              )}
            </div>
          )}
          <div className={styles.workspace}>
            <div
              className={styles.glyphs}
              role="group"
              aria-label="Private glyph review list"
            >
              {glyphs.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  disabled={busy}
                  aria-pressed={letter?.id === item.id}
                  onClick={() =>
                    void perform((signal, current) =>
                      inspect(item.id, signal, current),
                    )
                  }
                >
                  <strong>{character(item.codepoints)}</strong>
                  <small>
                    {item.variant_index ? "Authentic alternate · " : ""}
                    {item.coverage_status.replaceAll("_", " ")}
                  </small>
                </button>
              ))}
            </div>
            {letter && (
              <div className={styles.inspector}>
                <h3>
                  {character(letter.codepoints)} ·{" "}
                  {letter.coverage_status.replaceAll("_", " ")}
                </h3>
                {shownOutline ? (
                  <CanonicalGlyph
                    outline={shownOutline}
                    label={`Actual ${draft ? "edited" : "revision"} outline for ${character(letter.codepoints)}`}
                  />
                ) : (
                  <p>No selected revision. Inspect a candidate below.</p>
                )}
                <p>
                  The line marks the font baseline. Geometry is rendered from
                  the canonical result, with holes and descenders preserved.
                </p>
                {shownOutline && (
                  <p>
                    Advance {shownOutline.metrics.advance_width} font units ·
                    left bearing {shownOutline.metrics.left_side_bearing} ·{" "}
                    {shownOutline.units_per_em} units per em.{" "}
                    {shownOutline.codepoints[0] === 32
                      ? "Space contains metrics only, with no drawn contour."
                      : ""}
                  </p>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void perform((signal, current) =>
                      inspect(letter.id, signal, current, true),
                    )
                  }
                >
                  Reload current letter; keep local outline edit
                </button>
                <div className={styles.actions}>
                  {letter.variant_index > 0 && letter.active_revision_id && (
                    <button
                      type="button"
                      disabled={busy || !detail?.etag}
                      onClick={() =>
                        void perform(async (signal, current) => {
                          const result = await client.enableAlternate(
                            projectId,
                            letter.id,
                            letter.coverage_status === "excluded",
                            detail!.etag!,
                            signal,
                          );
                          if (!current()) return;
                          setDetail(result);
                          await refresh(signal, current);
                        })
                      }
                    >
                      {letter.coverage_status === "excluded"
                        ? "Use this alternate"
                        : "Pause this alternate"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={
                      busy ||
                      !revision ||
                      revision.approval !== "pending" ||
                      !detail?.etag ||
                      !!draft
                    }
                    onClick={() => change("approve")}
                  >
                    Approve selected revision
                  </button>
                  <button
                    type="button"
                    disabled={
                      busy ||
                      !revision ||
                      revision.approval !== "pending" ||
                      !detail?.etag
                    }
                    onClick={() => change("reject")}
                  >
                    Reject pending revision
                  </button>
                </div>
                {scriptCode === "Deva" && shownOutline && (
                  <IndicGeometryReview
                    key={revision?.id}
                    outline={shownOutline}
                    disabled={busy || !letter.outline_edit_supported}
                    onChange={setDraft}
                  />
                )}
                <fieldset
                  disabled={busy || !revision || !letter.outline_edit_supported}
                  className={styles.fields}
                >
                  <legend>Edit the actual outline</legend>
                  <label>
                    Uniform scale %
                    <input
                      type="number"
                      min="1"
                      max="400"
                      value={scale}
                      onChange={(event) => setScale(event.target.value)}
                    />
                  </label>
                  <label>
                    Horizontal shift
                    <input
                      type="number"
                      value={dx}
                      onChange={(event) => setDx(event.target.value)}
                    />
                  </label>
                  <label>
                    Vertical shift
                    <input
                      type="number"
                      value={dy}
                      onChange={(event) => setDy(event.target.value)}
                    />
                  </label>
                  <div className={styles.actions}>
                    <button type="button" onClick={previewEdit}>
                      Preview outline edit
                    </button>
                    {scriptCode === "Latn" &&
                      (letter.outline?.contours.length ?? 0) > 0 && (
                        <button type="button" onClick={previewSpacing}>
                          Preview balanced side spacing
                        </button>
                      )}
                    <button
                      type="button"
                      disabled={!draft || !detail?.etag}
                      onClick={() => change("edit")}
                    >
                      Save pending outline revision
                    </button>
                    <button
                      type="button"
                      disabled={!draft}
                      onClick={() => setDraft(null)}
                    >
                      Discard local edit
                    </button>
                  </div>
                  {letter.outline && !letter.outline_edit_supported && (
                    <p>
                      This detailed outline exceeds the supported edit size. It
                      remains available for selection, review and approval; save
                      a new source to replace its geometry.
                    </p>
                  )}
                  <p>
                    Scale acts uniformly about the baseline origin. Rotation,
                    crop, erase and freehand replacement are unavailable for
                    these backend outlines. Capture a new isolated source to
                    replace a shape.
                  </p>
                </fieldset>
                <h4>Candidates and alternatives</h4>
                <p>
                  Template assignments and ranks are recommendations. Confirm
                  the visible shape; no recognition accuracy is claimed.
                </p>
                <div className={styles.candidates}>
                  {candidates.map((candidate) => (
                    <article key={candidate.id}>
                      <CanonicalGlyph
                        outline={candidate.outline}
                        label={`Candidate ${candidate.rank} for ${character(letter.codepoints)}`}
                      />
                      <strong>{description(candidate.origin)}</strong>
                      <span>
                        {candidate.id === recommendedId
                          ? "Recommended · not approved"
                          : `Alternative ${candidate.rank}`}{" "}
                        · {candidate.review_status.replaceAll("_", " ")}
                      </span>
                      {Array.isArray(candidate.evidence.flags) && (
                        <p>
                          Review flags:{" "}
                          {candidate.evidence.flags
                            .map(String)
                            .map((flag) => flag.replaceAll("_", " "))
                            .join("; ")}
                        </p>
                      )}
                      <PrivateEvidence
                        key={`${candidate.id}:crop`}
                        client={client}
                        value={candidate.evidence.crop_artifact}
                        label="captured cell"
                      />
                      <PrivateEvidence
                        key={`${candidate.id}:mask`}
                        client={client}
                        value={candidate.evidence.mask_artifact}
                        label="extracted ink mask"
                      />
                      <small>
                        Source {candidate.source_sample_id.slice(0, 8)} · Result{" "}
                        {candidate.result_artifact_id.slice(0, 8)}
                      </small>
                      <button
                        type="button"
                        disabled={
                          busy ||
                          !detail?.etag ||
                          candidate.review_status === "rejected"
                        }
                        onClick={() =>
                          void perform(async (signal, current) => {
                            const result = await client.select(
                              projectId,
                              letter.id,
                              {
                                candidate_id: candidate.id,
                                base_revision_id: workingId,
                              },
                              detail.etag!,
                              stableKey("select", {
                                glyph_id: letter.id,
                                candidate_id: candidate.id,
                                base_revision_id: workingId,
                                etag: detail.etag,
                              }),
                              signal,
                            );
                            if (!current()) return;
                            setDetail(result);
                            setDraft(null);
                            await refresh(signal, current);
                            if (current())
                              setNotice(
                                "Candidate selected as a pending revision. Inspect its actual geometry, then approve it explicitly.",
                              );
                          })
                        }
                      >
                        Select for review
                      </button>
                    </article>
                  ))}
                </div>
                {candidateCursor && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void perform(async (signal, current) => {
                        const page = await client.candidates(
                          projectId,
                          letter.id,
                          signal,
                          candidateCursor,
                        );
                        if (current()) {
                          setCandidates((previous) => [
                            ...previous,
                            ...page.items,
                          ]);
                          setCandidateCursor(page.next_cursor);
                        }
                      })
                    }
                  >
                    Load more alternatives
                  </button>
                )}
                <label>
                  Immutable revision history
                  <select
                    value={rollbackId}
                    onChange={(event) => setRollbackId(event.target.value)}
                    disabled={busy}
                  >
                    <option value="">Choose a revision to restore</option>
                    {letter.revisions.map((item) => (
                      <option key={item.id} value={item.id}>
                        Revision {item.seq} · {item.approval} ·{" "}
                        {description(item.origin)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={busy || !rollbackId || !detail?.etag}
                  onClick={() => change("rollback")}
                >
                  Restore as a new revision
                </button>
                {letter.variant_index === 0 && (
                  <button
                    type="button"
                    disabled={
                      busy ||
                      !detail?.etag ||
                      !rollbackId ||
                      !letter.revisions.some(
                        (r) =>
                          r.id === rollbackId &&
                          r.approval === "approved" &&
                          r.id !== letter.active_revision_id &&
                          r.origin !== "ml_generated",
                      )
                    }
                    onClick={() =>
                      void perform(async (signal, current) => {
                        const result = await client.addAlternate(
                          projectId,
                          letter.id,
                          rollbackId,
                          workingId,
                          detail!.etag!,
                          stableKey("alternate", {
                            glyph: letter.id,
                            source: rollbackId,
                            base: workingId,
                            etag: detail!.etag,
                          }),
                          signal,
                        );
                        if (!current()) return;
                        setDetail(result);
                        setCandidates([]);
                        setDraft(null);
                        await refresh(signal, current);
                        if (current())
                          setNotice(
                            "Authentic alternate saved for review. Approve it to include it in future fonts.",
                          );
                      })
                    }
                  >
                    Keep this approved earlier sample as an alternate
                  </button>
                )}
              </div>
            )}
          </div>
          <fieldset disabled={busy} className={styles.fields}>
            <legend>Space and milestone</legend>
            <label>
              Space advance in font units
              <input
                type="number"
                min="1"
                max="8000"
                value={spaceAdvance}
                onChange={(event) => setSpaceAdvance(event.target.value)}
              />
            </label>
            <button
              type="button"
              onClick={() =>
                void perform(async (signal, current) => {
                  await client.space(
                    projectId,
                    Number(spaceAdvance),
                    stableKey("space", { advance_width: Number(spaceAdvance) }),
                    signal,
                  );
                  await refresh(signal, current);
                  if (current())
                    setNotice(
                      "Space metrics are pending. Select Space in the glyph list and approve them explicitly.",
                    );
                })
              }
            >
              Propose space metrics
            </button>
            <label>
              Milestone label
              <input
                value={snapshotLabel}
                maxLength={120}
                onChange={(event) => setSnapshotLabel(event.target.value)}
              />
            </label>
            <button
              type="button"
              onClick={() =>
                void perform(async (signal, current) => {
                  const result = await client.snapshot(
                    projectId,
                    snapshotLabel,
                    stableKey("snapshot", {
                      label: snapshotLabel,
                      glyphs: glyphs.map((item) => [
                        item.id,
                        item.active_revision_id,
                      ]),
                    }),
                    signal,
                  );
                  if (current())
                    setNotice(
                      `Immutable milestone recorded: ${result.id}. It freezes approved revisions; no font file was created.`,
                    );
                })
              }
            >
              Save approved milestone snapshot
            </button>
          </fieldset>
          <div className={styles.notice}>
            <strong>Measure your approved style</strong>
            <p>Save a measurement of the shapes you have approved.</p>
            <button
              type="button"
              disabled={busy || !coverage?.user_approved.length}
              onClick={() =>
                void perform(async (signal, current) => {
                  const snapshot = await client.snapshot(
                    projectId,
                    "Style measurement",
                    stableKey(
                      "style-snapshot",
                      glyphs.map((g) => [
                        g.id,
                        g.active_revision_id,
                        g.coverage_status,
                      ]),
                    ),
                    signal,
                  );
                  const result = await client.measureStyle(
                    projectId,
                    snapshot.id,
                    stableKey("style-measure", snapshot.id),
                    signal,
                  );
                  if (current()) setProfile(result);
                })
              }
            >
              Measure approved shapes
            </button>
          </div>
          {profile && <MeasuredStyle profile={profile} />}
          <details>
            <summary>Connected writing and generation</summary>
            {capabilities
              .filter(
                (c) =>
                  c.style === "print" &&
                  ["freeform_alignment", "glyph_generation"].includes(c.task),
              )
              .map((c) => (
                <p key={c.task}>
                  <strong>
                    {c.task === "freeform_alignment"
                      ? "Connected writing · Research only"
                      : "Glyph generation · Unavailable"}
                  </strong>
                  <br />
                  {c.reason}
                </p>
              ))}
          </details>
        </>
      )}
      {busy && (
        <button
          type="button"
          onClick={() => {
            cancellation.current?.abort();
            setBusy(false);
          }}
        >
          Stop checking; keep server work
        </button>
      )}
      {notice && (
        <p role="status" aria-live="polite" className={styles.notice}>
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </section>
  );
}
