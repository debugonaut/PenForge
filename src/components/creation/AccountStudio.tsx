"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { PenCharacter, QuillCharacter } from "@/components/brand/Characters";
import { DrawingCanvas } from "./DrawingCanvas";
import { StepMascot, StepStage } from "./StepStage";
import { CloudReviewPanel, CanonicalGlyph } from "./CloudReviewPanel";
import type { ReviewCoverage } from "@/lib/extraction-client";
import { CloudExportPanel } from "./CloudExportPanel";
import {
  captureCodepoints,
  graphemeStrings,
} from "../../../packages/contracts/src/unicode";
import {
  appendObservedStroke,
  CANVAS_BRUSH_WIDTH,
  type CaptureSize,
  type ObservedStroke,
} from "@/lib/stroke-capture";
import {
  captureErrorText,
  submitCapture,
  type CaptureProgress,
} from "@/lib/capture-service";
import { captureRecovery, type RawStrokeDocument } from "@/lib/capture-recovery";
import type { AnalysisAssignment, ReviewGlyph } from "@/lib/extraction-client";
import { useAccountBackend } from "@/lib/use-account-backend";
import { UploadIcon } from "./WizardIcons";
import type {
  CaptureReference,
  CreationStep,
  FontProject,
  ProjectChanges,
  Stroke,
} from "@/lib/contracts";
import type { PrivateExport } from "@/lib/export-client";
import styles from "./AccountStudio.module.css";

type Method = "draw" | "page";

const TONE: Record<CreationStep, "mint" | "yellow" | "coral"> = {
  capture: "coral",
  review: "mint",
  edit: "yellow",
  style: "coral",
  preview: "mint",
  export: "yellow",
};

const NUMBER: Record<CreationStep, string> = {
  capture: "01",
  review: "02",
  edit: "03",
  style: "04",
  preview: "05",
  export: "06",
};

const STAGE_COPY: Record<CreationStep, { eyebrow: string; title: string; body: string }> = {
  capture: {
    eyebrow: "01 / CAPTURE",
    title: "Add a letter or a page.",
    body: "Draw one letter and say what it is, or use a printed page. Everything you add is kept in your account.",
  },
  review: {
    eyebrow: "02 / REVIEW",
    title: "Meet your letters.",
    body: "Choose the truest shape for each letter, then approve it. Nothing becomes part of your font until you say so.",
  },
  edit: {
    eyebrow: "03 / EDIT",
    title: "Make small corrections.",
    body: "Adjust a letter’s position or size when it needs it. Changes wait as a pending revision until you approve them again.",
  },
  style: {
    eyebrow: "04 / STYLE",
    title: "Your hand, measured.",
    body: "Penforge reads the slant, weight and proportions you actually wrote. The font keeps those instead of guessing a style.",
  },
  preview: {
    eyebrow: "05 / PREVIEW",
    title: "Read it back in your hand.",
    body: "This specimen uses the real outlines you approved — not a sample typeface.",
  },
  export: {
    eyebrow: "06 / EXPORT",
    title: "Make the font yours.",
    body: "Compile the approved letters into installable TTF, OTF and WOFF2 files.",
  },
};

function delay(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

export function AccountStudio({
  project,
  step,
  go,
  update,
  announce,
}: {
  project: FontProject;
  step: CreationStep;
  go: (step: CreationStep) => void;
  update: (change: ProjectChanges) => void;
  announce: (message: string) => void;
}) {
  const backend = useAccountBackend(project.id, project.scriptCode);
  const [method, setMethod] = useState<Method | null>(null);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const capture = useRef<{ document: RawStrokeDocument; startedAtMs: number } | null>(null);
  const [letter, setLetter] = useState("");
  const [pageFile, setPageFile] = useState<File | null>(null);
  const [templateIndex, setTemplateIndex] = useState("");
  const [progress, setProgress] = useState<CaptureProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stageNote, setStageNote] = useState("");
  const [glyphs, setGlyphs] = useState<ReviewGlyph[]>([]);
  const [measured, setMeasured] = useState<{ slant: number; weight: number } | null>(
    null,
  );
  const fileInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      request.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (step !== "preview" || !backend.extraction) return;
    const controller = new AbortController();
    void backend.extraction
      .glyphs(project.id, controller.signal)
      .then((rows) => setGlyphs(rows))
      .catch(() => {});
    return () => controller.abort();
  }, [backend.extraction, project.id, step]);

  useEffect(() => {
    if (step !== "style" || !backend.extraction) return;
    const controller = new AbortController();
    void backend.extraction
      .styleProfile(project.id, controller.signal)
      .then((profile) => {
        const metrics = profile.metrics;
        const slant =
          typeof metrics.slant_degrees === "number"
            ? metrics.slant_degrees
            : typeof metrics.slant === "number"
              ? metrics.slant
              : 0;
        const weight =
          typeof metrics.weight === "number"
            ? metrics.weight
            : typeof metrics.stroke_weight === "number"
              ? metrics.stroke_weight
              : 0;
        setMeasured({ slant, weight });
      })
      .catch(() => setMeasured(null));
    return () => controller.abort();
  }, [backend.extraction, project.id, step]);

  function observed(stroke: ObservedStroke, size: CaptureSize) {
    try {
      const built = appendObservedStroke(
        capture.current?.document,
        stroke,
        size,
        capture.current?.startedAtMs,
      );
      capture.current = {
        document: built.document,
        startedAtMs: built.captureStartedAtMs,
      };
      setError("");
    } catch {
      setError(
        "This stroke could not keep its real timing. Clear the page and start this letter again.",
      );
    }
  }

  function resetCapture() {
    capture.current = null;
    setStrokes([]);
    setMethod(null);
    setLetter("");
    setPageFile(null);
    setTemplateIndex("");
    setProgress(null);
    setError("");
  }

  function pickPageFile(file?: File | null) {
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type)) {
      setError("Use a PNG, JPEG, or WebP image.");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      setError(
        "That image is over 20 MB. A smaller photo uploads faster and reads just as well.",
      );
      return;
    }
    setError("");
    setPageFile(file);
  }

  async function analyze(
    sampleId: string,
    assignments: AnalysisAssignment[],
    drawingMode?: "fixed_round_v1",
  ) {
    const extraction = backend.extraction;
    if (!extraction) throw new Error("connection");
    const controller = new AbortController();
    request.current = controller;
    let job = await extraction.analyze(
      project.id,
      [sampleId],
      assignments,
      crypto.randomUUID(),
      controller.signal,
      drawingMode,
    );
    while (!["succeeded", "failed", "canceled"].includes(job.state)) {
      setStageNote(
        job.stage ? job.stage.replaceAll("_", " ") : "Waiting for a worker",
      );
      await delay(1200, controller.signal);
      job = await extraction.job(project.id, job.job_id, controller.signal);
    }
    if (job.state !== "succeeded") throw new Error("analysis");
  }

  async function saveDrawing() {
    if (busy || !backend.capture || !backend.uid) return;
    setBusy(true);
    setError("");
    setStageNote("");
    try {
      const document = capture.current?.document;
      if (!document || !strokes.length)
        throw new Error("Draw at least one stroke first.");
      const codepoints = captureCodepoints(letter, project.scriptCode);
      const operation = await captureRecovery.prepare({
        ownerUid: backend.uid,
        localProjectId: project.id,
        projectId: project.id,
        strokeDocument: document,
      });
      const confirmed = await submitCapture(
        backend.capture,
        operation,
        backend.uid,
        setProgress,
      );
      if (!confirmed.sampleId || !confirmed.assetId)
        throw new Error("The private source was not confirmed.");
      const now = new Date().toISOString();
      const reference: CaptureReference = {
        sampleId: confirmed.sampleId,
        assetId: confirmed.assetId,
        kind: "draw",
        createdAt: now,
      };
      update({
        sample: {
          kind: "draw",
          strokes,
          strokeDocument: document,
          captureStartedAtMs: capture.current?.startedAtMs,
          captureAssetId: confirmed.assetId,
        },
        captures: [
          ...project.captures.filter((item) => item.sampleId !== confirmed.sampleId),
          reference,
        ],
        assignments: [
          ...project.assignments.filter(
            (item) => item.sampleId !== confirmed.sampleId,
          ),
          { sampleId: confirmed.sampleId, codepoints, updatedAt: now },
        ],
      });
      announce("Letter saved to your account. Reading its shape…");
      await analyze(
        confirmed.sampleId,
        [
          {
            sample_id: confirmed.sampleId,
            codepoints,
            capture_transform: {
              schema_version: 1,
              units_per_em: 1000,
              baseline_y: 0.75,
              units_per_px: 2,
              origin_x_px: 0,
              pen_width_px: CANVAS_BRUSH_WIDTH,
            },
          },
        ],
        project.scriptCode === "Latn" ? "fixed_round_v1" : undefined,
      );
      announce("Shape ready to review. Nothing has been approved yet.");
      resetCapture();
      await backend.refreshOperations();
      go("review");
    } catch (cause) {
      setError(
        cause instanceof Error && cause.message === "analysis"
          ? "The shape could not be read this time. Your source is saved; retry from Review."
          : cause instanceof Error && cause.message.startsWith("Draw")
            ? cause.message
            : captureErrorText(cause),
      );
    } finally {
      request.current = null;
      setBusy(false);
    }
  }

  async function savePage() {
    if (busy || !backend.capture || !backend.uid) return;
    if (!pageFile) {
      setError("Choose a photo or scan first.");
      return;
    }
    setBusy(true);
    setError("");
    setStageNote("");
    try {
      const choice = templateIndex !== "" ? backend.templates[Number(templateIndex)] : null;
      const original = await captureRecovery.preserve(project.id, pageFile);
      const operation = await captureRecovery.prepare({
        ownerUid: backend.uid,
        localProjectId: project.id,
        projectId: project.id,
        originalId: original.id,
        sourceType: choice ? "template_photo" : "freeform_photo",
        ...(choice
          ? {
              template: {
                template_id: choice.manifest.template_id,
                template_version: choice.manifest.template_version,
                manifest_sha256: choice.manifest_sha256,
                page_index: choice.manifest.page.index,
              },
            }
          : {}),
      });
      const confirmed = await submitCapture(
        backend.capture,
        operation,
        backend.uid,
        setProgress,
      );
      if (!confirmed.sampleId || !confirmed.assetId)
        throw new Error("The private source was not confirmed.");
      const now = new Date().toISOString();
      update({
        sample: {
          kind: "upload",
          fileName: pageFile.name,
          originalCaptureId: original.id,
          captureAssetId: confirmed.assetId,
        },
        captures: [
          ...project.captures.filter((item) => item.sampleId !== confirmed.sampleId),
          {
            sampleId: confirmed.sampleId,
            assetId: confirmed.assetId,
            kind: "image",
            createdAt: now,
          },
        ],
      });
      if (!choice) {
        announce(
          "Page stored privately. Natural-page reading is experimental, so this page won’t create letters yet.",
        );
        resetCapture();
        await backend.refreshOperations();
        return;
      }
      announce("Page stored. Reading the marked letters…");
      await analyze(confirmed.sampleId, []);
      announce("Letters are ready to review. Nothing has been approved yet.");
      resetCapture();
      await backend.refreshOperations();
      go("review");
    } catch (cause) {
      setError(
        cause instanceof Error && cause.message === "analysis"
          ? "The page could not be read this time. The original is stored; retry from Review."
          : captureErrorText(cause),
      );
    } finally {
      request.current = null;
      setBusy(false);
    }
  }

  function mergeGlyphs(next: ReviewGlyph[], coverage?: ReviewCoverage) {
    setGlyphs(next);
    const approved = new Set(coverage?.user_approved ?? []);
    const mapped = next.map((glyph) => {
      const revision = glyph.revisions.find(
        (item) => item.id === (glyph.active_revision_id ?? glyph.working_revision_id),
      );
      return {
        id: glyph.id,
        character: String.fromCodePoint(...glyph.codepoints),
        sourceCharacter: String.fromCodePoint(...glyph.codepoints),
        status:
          revision?.approval === "approved" ||
          glyph.codepoints.some((point) => approved.has(point))
            ? ("approved" as const)
            : glyph.active_outline
              ? ("detected" as const)
              : ("needs-review" as const),
        revision: glyph.row_version,
        rotation: 0,
        scale: 100,
        crop: 0,
        strokes: [],
      };
    });
    update({ glyphs: mapped });
  }

  function persistExports(rows: PrivateExport[]) {
    const mapped = rows.map((row) => ({
      exportId: row.id,
      format: row.format,
      status:
        row.status === "ready"
          ? ("ready" as const)
          : row.status === "failed"
            ? ("failed" as const)
            : ("processing" as const),
      ...(row.sha256 ? { sha256: row.sha256 } : {}),
      createdAt: row.created_at,
    }));
    const ready = mapped.find((item) => item.status === "ready");
    update({
      exports: mapped,
      ...(ready ? { selectedExportId: ready.exportId } : {}),
      activeSnapshotId: rows[0]?.snapshot_id ?? project.activeSnapshotId,
    });
  }

  const copy = STAGE_COPY[step];
  const tone = TONE[step];
  const lastCapture = project.captures.at(-1);

  return (
    <div className={styles.studio}>
      <StepStage tone={tone} number={NUMBER[step]} className={styles.stage}>
        <div className={styles.intro}>
          <div>
            <span className="eyebrow">{copy.eyebrow}</span>
            <h1>{copy.title}</h1>
            <p>{copy.body}</p>
          </div>
          <span className={styles.mascot} aria-hidden="true">
            {step === "capture" || step === "preview" ? (
              <PenCharacter mood="happy" />
            ) : (
              <QuillCharacter mood={step === "review" ? "happy" : "surprised"} />
            )}
          </span>
        </div>

        {!backend.ready && (
          <p className={styles.note} role="status">
            Opening your private tools…
          </p>
        )}
        {backend.error && (
          <p className="form-error" role="alert">
            {backend.error}{" "}
            <Link href="/account" className="text-button">
              Open account
            </Link>
          </p>
        )}

        {step === "capture" && (
          <div className={styles.captureArea}>
            {method === null && (
              <div className="capture-choice-grid">
                <button
                  className="capture-choice capture-choice-draw"
                  onClick={() => setMethod("draw")}
                  disabled={busy || !backend.capture}
                >
                  <span className="choice-art" aria-hidden="true">
                    <span className="choice-ghost">Aa</span>
                    <svg className="choice-scribble" viewBox="0 0 300 200">
                      <path d="M18 138 C 58 52, 104 174, 154 100 S 232 40, 282 116" />
                    </svg>
                    <span className="choice-paper">
                      <span>Aa</span>
                      <i />
                      <i />
                    </span>
                    <PenCharacter mood="happy" className="choice-character" />
                  </span>
                  <span className="choice-copy">
                    <strong>Draw one letter</strong>
                    <span className="choice-cue">↗</span>
                  </span>
                </button>
                <button
                  className="capture-choice capture-choice-upload"
                  onClick={() => setMethod("page")}
                  disabled={busy || !backend.capture}
                >
                  <span className="choice-art" aria-hidden="true">
                    <span className="choice-ghost">abc</span>
                    <span className="choice-sheet">
                      <i />
                      <i />
                      <i />
                    </span>
                    <QuillCharacter mood="happy" className="choice-character" />
                  </span>
                  <span className="choice-copy">
                    <strong>Use a printed page</strong>
                    <span className="choice-cue">↗</span>
                  </span>
                </button>
              </div>
            )}

            {method === "draw" && (
              <div className={styles.card}>
                <div className={styles.cardHead}>
                  <div>
                    <strong>Draw one letter</strong>
                    <span>Timing, pressure and tilt are kept when your pen shares them.</span>
                  </div>
                  <button className="text-button" onClick={resetCapture} disabled={busy}>
                    ← Change method
                  </button>
                </div>
                <label className={styles.field}>
                  Which letter is this?
                  <input
                    value={letter}
                    maxLength={16}
                    placeholder={project.scriptCode === "Deva" ? "क or क्ष" : "a"}
                    onChange={(event) => setLetter(event.target.value)}
                    disabled={busy}
                  />
                </label>
                <DrawingCanvas
                  strokes={strokes}
                  sourceSize={capture.current?.document.capture_size_px}
                  onChange={setStrokes}
                  onObservedStroke={observed}
                  label="Draw one isolated letter"
                  height={300}
                  readOnly={busy}
                />
                <div className={styles.actions}>
                  <button
                    className="button button-dark"
                    onClick={() => void saveDrawing()}
                    disabled={busy || !strokes.length || !letter.trim()}
                  >
                    {busy ? "Saving your letter…" : "Save and read this letter"}
                  </button>
                  <button
                    className="text-button"
                    onClick={() => {
                      capture.current = null;
                      setStrokes([]);
                    }}
                    disabled={busy || !strokes.length}
                  >
                    Clear page
                  </button>
                </div>
              </div>
            )}

            {method === "page" && (
              <div className={styles.card}>
                <div className={styles.cardHead}>
                  <div>
                    <strong>Use a printed page</strong>
                    <span>A clear photo or scan of your handwriting.</span>
                  </div>
                  <button className="text-button" onClick={resetCapture} disabled={busy}>
                    ← Change method
                  </button>
                </div>
                <div
                  className="upload-zone"
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault();
                    pickPageFile(event.dataTransfer.files?.[0]);
                  }}
                >
                  <span className="upload-icon" aria-hidden="true">
                    <span className="upload-icon-orbit" />
                    <span className="upload-icon-plate">
                      <UploadIcon />
                    </span>
                  </span>
                  <h2>Drop your handwriting here</h2>
                  <p>
                    {pageFile
                      ? `${pageFile.name} · PNG, JPEG or WebP · up to 20 MB.`
                      : "Or choose a clear photo or scan. PNG, JPEG, WebP · up to 20 MB."}
                  </p>
                  <button
                    className="button button-dark"
                    disabled={busy}
                    onClick={() => fileInput.current?.click()}
                  >
                    {pageFile ? "Choose a different image" : "Choose an image"}
                  </button>
                  <input
                    ref={fileInput}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    hidden
                    onChange={(event) => {
                      pickPageFile(event.target.files?.[0]);
                      event.target.value = "";
                    }}
                  />
                  <button
                    className="camera-label text-button"
                    disabled={busy}
                    onClick={() => cameraInput.current?.click()}
                  >
                    Use camera on mobile
                  </button>
                  <input
                    ref={cameraInput}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    capture="environment"
                    hidden
                    onChange={(event) => {
                      pickPageFile(event.target.files?.[0]);
                      event.target.value = "";
                    }}
                  />
                </div>
                <div className="capture-upload-tips" aria-label="Photo tips">
                  <span>
                    <b>01</b> Flat page
                  </span>
                  <span>
                    <b>02</b> Dark ink
                  </span>
                  <span>
                    <b>03</b> No shadows
                  </span>
                </div>
                <div className={styles.actions}>
                  <button
                    className="button button-dark"
                    onClick={() => void savePage()}
                    disabled={busy || !pageFile}
                  >
                    {busy ? "Saving your page…" : "Save this page"}
                  </button>
                  {pageFile && !busy && (
                    <button className="text-button" onClick={() => setPageFile(null)}>
                      Remove image
                    </button>
                  )}
                </div>
                <details className={styles.advanced}>
                  <summary>Use a printable practice page</summary>
                  <label className={styles.field}>
                    Page layout
                    <select
                      value={templateIndex}
                      onChange={(event) => setTemplateIndex(event.target.value)}
                      disabled={busy}
                    >
                      <option value="">Natural page · stored privately, not read</option>
                      {backend.templates
                        .filter(
                          (item) =>
                            item.manifest.script_code === project.scriptCode,
                        )
                        .map((item, index) => (
                          <option key={`${item.manifest.template_id}-${index}`} value={index}>
                            {item.manifest.template_id} · v
                            {item.manifest.template_version} · page{" "}
                            {item.manifest.page.index + 1}
                          </option>
                        ))}
                    </select>
                  </label>
                  {templateIndex !== "" && backend.templates[Number(templateIndex)] && (
                    <p className={styles.note}>
                      Print this page at 100% and keep every marker visible.{" "}
                      <a
                        href={backend.extraction?.publicTemplateUrl(
                          backend.templates[Number(templateIndex)].pdf_url,
                        )}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open the printable page ↗
                      </a>
                    </p>
                  )}
                </details>
              </div>
            )}

            {progress && (
              <div className={styles.status} role="status" aria-live="polite">
                <strong>
                  {progress.phase === "confirmed"
                    ? "Source saved to your account"
                    : progress.phase === "uploading"
                      ? "Uploading your original"
                      : progress.phase === "registering"
                        ? "Registering your handwriting"
                        : "Checking your original"}
                </strong>
                {progress.phase !== "confirmed" && (
                  <>
                    <progress
                      value={progress.total === null ? undefined : progress.completed}
                      max={progress.total ?? 1}
                      aria-label="Capture progress"
                    />
                    <span>
                      {progress.total === null
                        ? "Waiting for a worker · time estimate unavailable"
                        : `${progress.completed} / ${progress.total} ${progress.basis === "bytes" ? "bytes sent" : "checks"} · time estimate unavailable`}
                    </span>
                  </>
                )}
                {stageNote && <span>{stageNote}</span>}
              </div>
            )}

            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            {lastCapture && method === null && (
              <p className={styles.note}>
                {project.captures.length} capture
                {project.captures.length === 1 ? "" : "s"} saved to your account.
                Add another letter any time.
              </p>
            )}
            {strokes.length > 0 && !busy && (
              <p className={styles.note} role="status">
                Reading your marks. Save when the letter looks right.
              </p>
            )}
          </div>
        )}

        {(step === "review" || step === "edit") && backend.extraction && backend.uid && (
          <div className={styles.reviewWrap}>
            <CloudReviewPanel
              key={`${backend.uid}:${project.id}:${step}`}
              client={backend.extraction}
              jobClient={backend.jobs ?? undefined}
              projectId={project.id}
              ownerUid={backend.uid}
              scriptCode={project.scriptCode}
              operations={backend.operations}
              startOpen
              onChanged={(rows, counts) => mergeGlyphs(rows, counts)}
            />
          </div>
        )}
        {step === "edit" && project.glyphs.length === 0 && (
          <p className={styles.note}>
            Add a letter in Capture before editing its shape.
          </p>
        )}

        {step === "style" && (
          <div className={styles.card}>
            {measured ? (
              <div className={styles.measured}>
                <div>
                  <span>Slant</span>
                  <strong>{Math.round(measured.slant)}°</strong>
                </div>
                <div>
                  <span>Weight</span>
                  <strong>{Math.round(measured.weight)}</strong>
                </div>
                <div>
                  <span>Letters approved</span>
                  <strong>
                    {project.glyphs.filter((glyph) => glyph.status === "approved").length}
                  </strong>
                </div>
              </div>
            ) : (
              <p className={styles.note}>
                Measurements appear after you approve letters. Nothing is
                estimated from a sample typeface.
              </p>
            )}
            <p className={styles.note}>
              The accepted style controls from the demo do not rewrite approved
              outlines. Letter spacing is the only measured adjustment available
              and is chosen at export.
            </p>
            <div className={styles.actions}>
              <button className="button button-dark" onClick={() => go("preview")}>
                See the specimen <span aria-hidden="true">→</span>
              </button>
            </div>
          </div>
        )}

        {step === "preview" && (
          <div className={styles.card}>
            <div className={styles.specimen} aria-label="Specimen from approved outlines">
              {graphemeStrings(project.previewText || "Your words here").map(
                (character, index) => {
                  if (/^\s$/.test(character))
                    return <span key={`space-${index}`} className={styles.specimenSpace} />;
                  const codepoints = [...character].map((value) =>
                    value.codePointAt(0),
                  );
                  const glyph = glyphs.find((item) =>
                    codepoints.every((cp) => item.codepoints.includes(cp ?? -1)),
                  );
                  const outline = glyph?.active_outline ?? glyph?.outline;
                  return outline ? (
                    <CanonicalGlyph
                      key={`${character}-${index}`}
                      outline={outline}
                      label={character}
                    />
                  ) : (
                    <span key={`${character}-${index}`} className={styles.specimenMissing}>
                      {character}
                    </span>
                  );
                },
              )}
            </div>
            <div className={styles.actions}>
              {project.selectedExportId ? (
                <Link
                  className="button button-dark"
                  href={`/write?export=${encodeURIComponent(project.selectedExportId)}`}
                >
                  Write with this font <span aria-hidden="true">→</span>
                </Link>
              ) : (
                <button className="button button-dark" onClick={() => go("export")}>
                  Generate the font <span aria-hidden="true">→</span>
                </button>
              )}
              <button className="text-button" onClick={() => go("review")}>
                Review letters
              </button>
            </div>
            {project.selectedExportId && (
              <p className={styles.note}>
                This specimen is drawn from approved outlines. The Writing Desk
                uses the generated font file.
              </p>
            )}
          </div>
        )}

        {step === "export" && backend.exportClient && backend.uid && (
          <div className={styles.exportWrap}>
            <CloudExportPanel
              key={`${backend.uid}:${project.id}`}
              client={backend.exportClient}
              jobClient={backend.jobs ?? undefined}
              projectId={project.id}
              ownerUid={backend.uid}
              quiet
              defaultFamily={project.name}
              defaultSnapshotId={project.activeSnapshotId ?? ""}
              onReady={persistExports}
            />
            {project.selectedExportId && (
              <p className={styles.note}>
                Your latest font is saved with this project.{" "}
                <Link href={`/write?export=${project.selectedExportId}`}>
                  Write with it →
                </Link>
              </p>
            )}
          </div>
        )}
      </StepStage>
      <div className={styles.mascotFoot} aria-hidden="true">
        <StepMascot character={step === "capture" ? "pen" : "quill"} />
      </div>
    </div>
  );
}
