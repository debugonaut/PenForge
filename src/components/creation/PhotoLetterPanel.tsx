"use client";

import { useEffect, useRef, useState } from "react";
import type { CaptureClient } from "@/lib/capture-client";
import {
  extractionErrorText,
  ExtractionError,
  type AnalysisJob,
  type ExtractionClient,
  type ReviewSample,
} from "@/lib/extraction-client";
import {
  photoAssignments,
  photoBatchOutcome,
  photoInkMask,
  photoPoint,
  photoRectangle,
  validatePhotoFrame,
  validatePhotoRect,
  validatePhotoSize,
  type PhotoRegionDraft,
  type PhotoRect,
  type PhotoSize,
} from "@/lib/photo-regions";
import {
  photoRecoveryKey,
  readPhotoRecovery,
  savePhotoRecovery,
  type PhotoAttempt,
} from "@/lib/photo-recovery";
import styles from "./PhotoLetterPanel.module.css";

const issueText: Record<string, string> = {
  empty_ink: "No ink survived. Adjust the box or ink threshold.",
  crop_touches_ink:
    "Ink touches an edge. Include the whole stroke and a white margin.",
  outline_requires_review:
    "This shape needs a clearer selection before it can be extracted.",
};

function wait(signal: AbortSignal) {
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

export function PhotoLetterPanel({
  capture,
  extraction,
  projectId,
  ownerUid,
  sample,
  onReady,
  onClose,
}: {
  capture: CaptureClient;
  extraction: ExtractionClient;
  projectId: string;
  ownerUid: string;
  sample: ReviewSample;
  onReady: () => void | Promise<void>;
  onClose: () => void;
}) {
  const {
    asset_id: assetId,
    sample_id: sampleId,
    source_sha256: digest,
  } = sample;
  const source = useRef<HTMLCanvasElement | null>(null);
  const crop = useRef<HTMLCanvasElement>(null);
  const mask = useRef<HTMLCanvasElement>(null);
  const pointer = useRef<{
    id: number;
    start: { x: number; y: number };
  } | null>(null);
  const labelInput = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController | null>(null);
  const loadedScope = useRef<string | null>(null);
  const persistenceVersion = useRef(0);
  const renderedVersion = persistenceVersion.current;
  const [frame, setFrame] = useState<PhotoSize | null>(null);
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rect, setRect] = useState<PhotoRect | null>(null);
  const [character, setCharacter] = useState("");
  const [baseline, setBaseline] = useState(0);
  const [capitalHeight, setCapitalHeight] = useState(100);
  const [threshold, setThreshold] = useState(180);
  const [regions, setRegions] = useState<PhotoRegionDraft[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [previewNote, setPreviewNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState<PhotoAttempt | null>(null);
  const [job, setJob] = useState<AnalysisJob | null>(null);
  const [selecting, setSelecting] = useState(true);
  const [hydrated, setHydrated] = useState(false);
  const [recoveryNote, setRecoveryNote] = useState("");
  const [readyCount, setReadyCount] = useState(0);
  const [issues, setIssues] = useState<Record<string, string>>({});
  const locked = busy || attempt !== null;

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setLoading(true);
    persistenceVersion.current += 1;
    loadedScope.current = null;
    setHydrated(false);
    setRecoveryNote("");
    setReadyCount(0);
    setIssues({});
    setThreshold(180);
    setSelecting(true);
    setError("");
    setFrame(null);
    setUrl("");
    setRegions([]);
    setAttempt(null);
    setJob(null);
    setEditing(null);
    setCharacter("");
    setBusy(false);
    pointer.current = null;
    source.current = null;
    const load = async () => {
      let asset = await capture.asset(assetId, controller.signal);
      if (asset.sha256 !== digest || asset.project_id !== projectId)
        throw new Error(
          "The saved image no longer matches this page. Reopen Capture.",
        );
      if ((!asset.width_px || !asset.height_px) && asset.duplicate_of) {
        const canonical = await capture.asset(
          asset.duplicate_of,
          controller.signal,
        );
        if (canonical.sha256 !== digest)
          throw new Error("The saved image dimensions could not be verified.");
        asset = canonical;
      }
      if (!asset.width_px || !asset.height_px)
        throw new Error(
          "The saved image dimensions are not ready. Reopen this page after verification.",
        );
      validatePhotoSize({ width: asset.width_px, height: asset.height_px });
      const blob = await capture.preview(assetId, controller.signal);
      if (controller.signal.aborted) return;
      const bitmap = await createImageBitmap(blob, {
        imageOrientation: "from-image",
        colorSpaceConversion: "none",
      });
      try {
        if (controller.signal.aborted) return;
        const size = { width: bitmap.width, height: bitmap.height };
        validatePhotoFrame(
          { width: asset.width_px, height: asset.height_px },
          size,
        );
        const canvas = document.createElement("canvas");
        canvas.width = size.width;
        canvas.height = size.height;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context)
          throw new Error("This browser could not open the image workspace.");
        // Keep source alpha for the same integer white-composite rule as extraction.
        context.drawImage(bitmap, 0, 0);
        source.current = canvas;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
        setFrame(size);
        const initial = {
          x: 0,
          y: 0,
          width: Math.min(100, size.width),
          height: Math.min(100, size.height),
        };
        setRect(initial);
        setBaseline(initial.height);
        setCapitalHeight(Math.min(100, size.height));
        const scope = { ownerUid, projectId, sampleId, digest };
        loadedScope.current = photoRecoveryKey(scope);
        try {
          const recovered = readPhotoRecovery(localStorage, scope, size);
          if (recovered.state === "ready") {
            setRegions(recovered.draft.regions);
            setCapitalHeight(recovered.draft.capitalHeight);
            setThreshold(recovered.draft.threshold);
            setAttempt(recovered.draft.attempt);
            setReadyCount(recovered.draft.readyCount);
            setIssues(recovered.draft.issues);
            setRecoveryNote(
              "Your saved letter selections and any unfinished batch have been restored.",
            );
          } else if (recovered.state === "invalid") {
            setRecoveryNote(
              "Earlier unreadable selection data is retained in this browser. Start a new selection from the unchanged original.",
            );
          }
        } catch {
          setRecoveryNote(
            "This browser cannot save selection recovery. Keep this page open while working; the original photo remains saved.",
          );
        }
        setHydrated(true);
      } finally {
        bitmap.close();
      }
    };
    void load()
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "The saved page could not be opened.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      request.current?.abort();
      pointer.current = null;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (source.current) {
        source.current.width = 1;
        source.current.height = 1;
      }
      source.current = null;
    };
  }, [assetId, capture, digest, ownerUid, projectId, sampleId]);

  useEffect(() => {
    const scope = { ownerUid, projectId, sampleId, digest };
    if (
      !hydrated ||
      !frame ||
      loadedScope.current !== photoRecoveryKey(scope) ||
      renderedVersion !== persistenceVersion.current
    )
      return;
    try {
      savePhotoRecovery(localStorage, scope, frame, {
        regions,
        capitalHeight,
        threshold,
        attempt,
        readyCount,
        issues,
      });
    } catch {
      setRecoveryNote(
        "These latest selections could not be saved in this browser. Keep the page open; your original photo remains saved.",
      );
    }
  }, [
    attempt,
    capitalHeight,
    digest,
    frame,
    hydrated,
    issues,
    ownerUid,
    projectId,
    readyCount,
    regions,
    renderedVersion,
    sampleId,
    threshold,
  ]);

  useEffect(() => {
    if (
      !frame ||
      !rect ||
      !source.current ||
      !crop.current ||
      !mask.current ||
      pointer.current
    )
      return;
    try {
      validatePhotoRect(rect, frame);
      const original = source.current.getContext("2d", {
        willReadFrequently: true,
      });
      if (!original) throw new Error("The original preview is unavailable.");
      const data = original.getImageData(
        rect.x,
        rect.y,
        rect.width,
        rect.height,
      );
      const ink = photoInkMask(data.data, rect.width, rect.height, threshold);
      const scale = Math.min(1, 420 / rect.width, 240 / rect.height);
      const previewSize = {
        width: Math.max(1, Math.round(rect.width * scale)),
        height: Math.max(1, Math.round(rect.height * scale)),
      };
      const temporary = document.createElement("canvas");
      temporary.width = rect.width;
      temporary.height = rect.height;
      const binary = temporary.getContext("2d");
      if (!binary) throw new Error("The ink preview is unavailable.");
      binary.putImageData(
        new ImageData(ink.pixels, rect.width, rect.height),
        0,
        0,
      );
      for (const canvas of [crop.current, mask.current]) {
        canvas.width = previewSize.width;
        canvas.height = previewSize.height;
      }
      const cropContext = crop.current.getContext("2d");
      const maskContext = mask.current.getContext("2d");
      if (!cropContext || !maskContext)
        throw new Error("The letter preview is unavailable.");
      cropContext.fillStyle = "white";
      cropContext.fillRect(0, 0, crop.current.width, crop.current.height);
      cropContext.drawImage(
        source.current,
        rect.x,
        rect.y,
        rect.width,
        rect.height,
        0,
        0,
        crop.current.width,
        crop.current.height,
      );
      maskContext.imageSmoothingEnabled = false;
      maskContext.drawImage(
        temporary,
        0,
        0,
        mask.current.width,
        mask.current.height,
      );
      temporary.width = 1;
      temporary.height = 1;
      setPreviewNote(
        !ink.ink
          ? "No ink survives this setting. Adjust the box or threshold."
          : ink.borderInk
            ? "Ink touches the box edge. Add a small white margin so strokes are not clipped."
            : "The black preview shows the ink selected for extraction. Include one character and its complete strokes.",
      );
    } catch (cause) {
      setPreviewNote(
        cause instanceof Error
          ? cause.message
          : "Adjust the letter box to preview it.",
      );
      for (const canvas of [crop.current, mask.current]) {
        canvas.width = 1;
        canvas.height = 1;
      }
    }
  }, [frame, rect, threshold]);

  function location(event: React.PointerEvent<SVGSVGElement>) {
    if (!frame) throw new Error("The page is still opening.");
    return photoPoint(
      event.clientX,
      event.clientY,
      event.currentTarget.getBoundingClientRect(),
      frame,
    );
  }
  function start(event: React.PointerEvent<SVGSVGElement>) {
    if (
      locked ||
      !selecting ||
      !frame ||
      !event.isPrimary ||
      event.button !== 0 ||
      pointer.current
    )
      return;
    event.preventDefault();
    pointer.current = { id: event.pointerId, start: location(event) };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function move(event: React.PointerEvent<SVGSVGElement>) {
    if (!frame || pointer.current?.id !== event.pointerId) return;
    try {
      setRect(photoRectangle(pointer.current.start, location(event), frame));
    } catch {
      /* Wait for a non-empty drag. */
    }
  }
  function finish(event: React.PointerEvent<SVGSVGElement>) {
    if (!frame || pointer.current?.id !== event.pointerId) return;
    try {
      const next = photoRectangle(
        pointer.current.start,
        location(event),
        frame,
      );
      setRect(next);
      setBaseline(next.y + next.height);
      setError("");
      if (!regions.length) setCapitalHeight(Math.max(7, next.height));
      labelInput.current?.focus({ preventScroll: true });
    } catch {
      setError(
        "Drag a box around the full letter, or enter its coordinates below.",
      );
    }
    pointer.current = null;
  }
  function cancelSelection() {
    if (!pointer.current) return;
    pointer.current = null;
    // A canceled gesture still needs a fresh mask for the rectangle left on screen.
    setRect((current) => (current ? { ...current } : null));
  }
  function add() {
    if (!frame || !rect || locked) return;
    try {
      const draft = {
        id: editing ?? crypto.randomUUID(),
        character,
        rect: { ...rect },
        baseline,
      };
      const next = editing
        ? regions.map((region) => (region.id === editing ? draft : region))
        : [...regions, draft];
      photoAssignments(sampleId, digest, frame, next, capitalHeight, threshold);
      setRegions(next);
      if (editing)
        setIssues((current) => {
          const next = { ...current };
          delete next[editing];
          return next;
        });
      setEditing(null);
      setCharacter("");
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Check this letter before adding it.",
      );
    }
  }
  async function process() {
    if (busy || !frame) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const saved = attempt ?? {
        key: crypto.randomUUID(),
        regions: photoAssignments(
          sampleId,
          digest,
          frame,
          regions,
          capitalHeight,
          threshold,
        ),
      };
      persistenceVersion.current += 1;
      if (!saved.jobId) {
        try {
          savePhotoRecovery(
            localStorage,
            { ownerUid, projectId, sampleId, digest },
            frame,
            {
              regions,
              capitalHeight,
              threshold,
              attempt: saved,
              readyCount,
              issues,
            },
          );
        } catch {
          setRecoveryNote(
            "This browser could not preserve the exact batch request. No new processing was started.",
          );
          throw new Error(
            "Free some browser storage and try again. The batch must be saved before processing can start safely.",
          );
        }
      }
      setAttempt(saved);
      let current = saved.jobId
        ? await extraction.job(projectId, saved.jobId, controller.signal)
        : await extraction.analyzeImageRegions(
            projectId,
            sampleId,
            saved.regions,
            saved.key,
            controller.signal,
          );
      if (controller.signal.aborted) return;
      setAttempt({ ...saved, jobId: current.job_id });
      setJob(current);
      while (!["succeeded", "failed", "canceled"].includes(current.state)) {
        await wait(controller.signal);
        current = await extraction.job(
          projectId,
          current.job_id,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setAttempt({ ...saved, jobId: current.job_id });
        setJob(current);
      }
      if (current.state !== "succeeded") {
        setAttempt(null);
        throw new Error(
          "These letters could not be extracted. Keep a white margin around each complete character and check the ink preview before retrying.",
        );
      }
      const outcome = photoBatchOutcome(current.quality, saved.regions);
      persistenceVersion.current += 1;
      const available = saved.completed
        ? readyCount
        : readyCount + outcome.ready.length;
      setReadyCount(available);
      setIssues(outcome.issues);
      const rejected = new Set(Object.keys(outcome.issues));
      if (rejected.size) {
        const remaining = regions.filter((region) => rejected.has(region.id));
        setAttempt(null);
        setRegions(remaining);
        try {
          savePhotoRecovery(
            localStorage,
            { ownerUid, projectId, sampleId, digest },
            frame,
            {
              regions: remaining,
              capitalHeight,
              threshold,
              attempt: null,
              readyCount: available,
              issues: outcome.issues,
            },
          );
        } catch {
          setRecoveryNote(
            "Your previous batch receipt is kept, but the latest corrections could not be saved. Keep this page open while correcting them.",
          );
        }
        setError(
          `${rejected.size} letter box${rejected.size === 1 ? " needs" : "es need"} changes. ${available} letter${available === 1 ? " is" : "s are"} ready for Review. Edit the remaining boxes; only those will be processed again.`,
        );
        return;
      }
      const completed = {
        ...saved,
        jobId: current.job_id,
        completed: true as const,
      };
      setAttempt(completed);
      try {
        savePhotoRecovery(
          localStorage,
          { ownerUid, projectId, sampleId, digest },
          frame,
          {
            regions,
            capitalHeight,
            threshold,
            attempt: completed,
            readyCount: available,
            issues: {},
          },
        );
      } catch {
        /* Server results remain authoritative if browser storage is full. */
      }
      await onReady();
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(
          cause instanceof ExtractionError
            ? extractionErrorText(cause)
            : cause instanceof Error
              ? cause.message
              : "The letter batch could not be processed.",
        );
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function openReview() {
    setBusy(true);
    setError("");
    try {
      if (frame)
        savePhotoRecovery(
          localStorage,
          { ownerUid, projectId, sampleId, digest },
          frame,
          { regions, capitalHeight, threshold, attempt, readyCount, issues },
        );
      await onReady();
    } catch {
      setError(
        "Your processed letters remain saved. Review could not be opened; try the Review button again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className={styles.panel}
      aria-label="Choose letters from your photo"
    >
      <div className={styles.heading}>
        <div>
          <h2>Choose the letters you wrote.</h2>
          <p>
            Use this existing photo. Box one complete character, confirm its
            label, then add it to the batch. Every extracted shape still needs
            your approval in Review.
          </p>
        </div>
        <button
          type="button"
          className="text-button"
          onClick={onClose}
          disabled={busy}
        >
          ← Back to Capture
        </button>
      </div>
      {loading && <p role="status">Opening your verified original…</p>}
      {recoveryNote && (
        <p className={styles.note} role="status">
          {recoveryNote}
        </p>
      )}
      {frame && url && (
        <>
          <p className={styles.note}>
            Select a box on the image or enter its coordinates below. Leave a
            small white margin. Notebook lines and connected neighboring letters
            remain in the preview; adjust the selection when they interfere.
          </p>
          <button
            type="button"
            className="text-button"
            onClick={() => setSelecting((value) => !value)}
            disabled={locked}
            aria-pressed={!selecting}
          >
            {selecting
              ? "Switch to scrolling the photo"
              : "Switch to selecting letter boxes"}
          </button>
          <div className={styles.workspace}>
            <div className={styles.imageScroll}>
              <div className={styles.imageStage}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={url}
                  alt="Your saved handwriting in its original oriented pixel frame"
                  draggable={false}
                />
                <svg
                  style={{
                    pointerEvents: locked || !selecting ? "none" : "auto",
                  }}
                  viewBox={`0 0 ${frame.width} ${frame.height}`}
                  aria-label="Draw a rectangle around a letter; numeric controls are below"
                  onPointerDown={start}
                  onPointerMove={move}
                  onPointerUp={finish}
                  onPointerCancel={cancelSelection}
                  onLostPointerCapture={cancelSelection}
                >
                  {regions.map((region) => (
                    <rect
                      key={region.id}
                      {...region.rect}
                      className={styles.savedRect}
                    />
                  ))}
                  {rect && (
                    <>
                      <rect {...rect} className={styles.currentRect} />
                      <path
                        d={`M ${rect.x} ${baseline} H ${rect.x + rect.width}`}
                        className={styles.baseline}
                      />
                    </>
                  )}
                </svg>
              </div>
            </div>
            <div className={styles.controls}>
              <fieldset disabled={locked}>
                <legend>Selected letter</legend>
                <label>
                  Which character is this?
                  <input
                    ref={labelInput}
                    value={character}
                    maxLength={2}
                    onChange={(event) => setCharacter(event.target.value)}
                    placeholder="A, a, 7 or ?"
                    autoComplete="off"
                  />
                </label>
                <div className={styles.coordinates}>
                  {(["x", "y", "width", "height"] as const).map((key) => (
                    <label key={key}>
                      {key === "x"
                        ? "Left"
                        : key === "y"
                          ? "Top"
                          : key === "width"
                            ? "Width"
                            : "Height"}
                      <input
                        type="number"
                        min={key === "width" || key === "height" ? 1 : 0}
                        step={1}
                        value={rect?.[key] ?? 0}
                        onChange={(event) =>
                          setRect((current) => ({
                            ...(current ?? { x: 0, y: 0, width: 1, height: 1 }),
                            [key]: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                  ))}
                </div>
                <label>
                  Baseline from the image top (pixels)
                  <input
                    type="number"
                    min={0}
                    max={frame.height}
                    step={0.5}
                    value={baseline}
                    onChange={(event) =>
                      setBaseline(Number(event.target.value))
                    }
                  />
                </label>
                <small>
                  Place the baseline where ordinary letters sit. Let descenders
                  such as g and y extend below it.
                </small>
                <button
                  type="button"
                  className="button button-dark"
                  onClick={add}
                  disabled={!rect || !character}
                >
                  {editing ? "Update selected letter" : "Add letter to batch"}
                </button>
              </fieldset>
              <fieldset disabled={locked}>
                <legend>Shared size and ink</legend>
                <label>
                  Capital letter height (pixels)
                  <input
                    type="number"
                    min={7}
                    max={frame.height}
                    step={1}
                    value={capitalHeight}
                    onChange={(event) =>
                      setCapitalHeight(Number(event.target.value))
                    }
                  />
                </label>
                <small>
                  Use the same measured capital height for every box on this
                  page. Lowercase letters and descenders keep their relative
                  size.
                </small>
                <label>
                  Ink threshold: {threshold}
                  <input
                    type="range"
                    min={0}
                    max={255}
                    step={1}
                    value={threshold}
                    onChange={(event) =>
                      setThreshold(Number(event.target.value))
                    }
                  />
                </label>
                <small>
                  Check the black preview below; a higher setting keeps lighter
                  marks.
                </small>
              </fieldset>
            </div>
          </div>
          <div className={styles.previews}>
            <figure>
              <figcaption>Selected original</figcaption>
              <canvas ref={crop} aria-label="Original letter crop" />
            </figure>
            <figure>
              <figcaption>Ink selected for extraction</figcaption>
              <canvas ref={mask} aria-label="Thresholded letter mask" />
            </figure>
          </div>
          <p className={styles.note} role="status">
            {previewNote}
          </p>
          <div className={styles.batch}>
            <h3>{regions.length} / 96 letters in this batch</h3>
            <ul>
              {regions.map((region) => (
                <li key={region.id}>
                  <span>
                    {region.character} · {region.rect.x}, {region.rect.y} ·{" "}
                    {region.rect.width} × {region.rect.height}
                    {issues[region.id] && (
                      <>
                        <br />
                        <strong>{issueText[issues[region.id]]}</strong>
                      </>
                    )}
                  </span>
                  <button
                    type="button"
                    className="text-button"
                    disabled={locked}
                    onClick={() => {
                      setEditing(region.id);
                      setRect({ ...region.rect });
                      setCharacter(region.character);
                      setBaseline(region.baseline);
                    }}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="text-button"
                    disabled={locked}
                    onClick={() => {
                      setRegions((current) =>
                        current.filter((item) => item.id !== region.id),
                      );
                      setIssues((current) => {
                        const next = { ...current };
                        delete next[region.id];
                        return next;
                      });
                      if (editing === region.id) setEditing(null);
                    }}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
            <div className={styles.actions}>
              <button
                type="button"
                className="button button-dark"
                disabled={busy || !regions.length}
                onClick={() => void process()}
              >
                {busy
                  ? "Processing selected letters…"
                  : attempt
                    ? "Resume this letter batch"
                    : `Process ${regions.length || "selected"} letter${regions.length === 1 ? "" : "s"} together`}
              </button>
              {readyCount > 0 && !busy && (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => void openReview()}
                >
                  Review {readyCount} ready letter{readyCount === 1 ? "" : "s"}
                </button>
              )}
              {attempt?.completed && !busy && (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    persistenceVersion.current += 1;
                    setAttempt(null);
                    setRegions([]);
                    setIssues({});
                    setReadyCount(0);
                    setJob(null);
                    setEditing(null);
                    setCharacter("");
                    setError("");
                  }}
                >
                  Add more letters from this page
                </button>
              )}
              {busy && (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    request.current?.abort();
                    setBusy(false);
                  }}
                >
                  Stop checking; keep server work
                </button>
              )}
            </div>
            {job && (
              <p role="status">
                {job.stage?.replaceAll("_", " ") || job.state} ·{" "}
                {job.progress.completed_units} / {job.progress.total_units}{" "}
                measured checks
              </p>
            )}
            <p className={styles.note}>
              One batch creates pending outlines from your chosen ink. Confirm
              each shape in Review before building the font. Missing characters
              can be added later.
            </p>
          </div>
        </>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
