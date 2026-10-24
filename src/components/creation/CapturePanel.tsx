"use client";

import Image from "next/image";
import { useRef, useState } from "react";
import { PenCharacter } from "@/components/brand/Characters";
import { useConfirmation } from "@/components/shared/ConfirmationDialog";
import { DrawingCanvas } from "./DrawingCanvas";
import { hasSample } from "./creation-state";
import type { AnnouncedPanelProps } from "./creation-state";
import type { Sample, Stroke } from "@/lib/contracts";
import { handwritingService } from "@/lib/mock-services";
import { captureRecovery } from "@/lib/capture-recovery";
import { appendObservedStroke, type ObservedStroke, type CaptureSize } from "@/lib/stroke-capture";
import { CaptureCloudPanel } from "./CaptureCloudPanel";
import { StepMascot, StepStage } from "./StepStage";
import { RedoIcon, TrashIcon, UndoIcon, UploadIcon } from "./WizardIcons";
import { WizardAction } from "./WizardAction";

export function CapturePanel({
  project,
  update,
  go,
  announce,
}: AnnouncedPanelProps) {
  const { confirm, confirmationDialog } = useConfirmation();
  const [mode, setMode] = useState<
    "choose" | "draw" | "upload" | "sample"
  >(project.sample ? "sample" : "choose");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [past, setPast] = useState<Array<Sample | null>>([]);
  const [future, setFuture] = useState<Array<Sample | null>>([]);
  const observed = useRef<{ stroke: ObservedStroke; size: CaptureSize } | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const firstChoiceRef = useRef<HTMLButtonElement>(null);
  const uploadButtonRef = useRef<HTMLButtonElement>(null);
  const reviewButtonRef = useRef<HTMLButtonElement>(null);
  const canvasStageRef = useRef<HTMLDivElement>(null);
  const approvedCapture = useRef<"draw" | "upload" | null>(null);
  const strokes =
    project.sample?.kind === "draw" ? project.sample.strokes || [] : [];
  async function mayReplace(kind: Sample["kind"]) {
    if (!hasSample(project) || project.sample?.kind === kind) return true;
    return confirm({
      title: "Replace your sample?",
      message:
        "The previous capture sample will be removed. Your existing letter edits will stay available.",
      confirmLabel: "Replace sample",
      destructive: true,
    });
  }
  async function changeStrokes(next: Stroke[]) {
    const observation = observed.current;
    observed.current = null;
    const approved = approvedCapture.current;
    approvedCapture.current = null;
    if (approved !== "draw" && !(await mayReplace("draw"))) return;
    setError("");
    let sample: Sample = { kind: "draw", strokes: next };
    if (observation && next.length === strokes.length + 1 && (!strokes.length || project.sample?.strokeDocument)) {
      try {
        const capture = appendObservedStroke(project.sample?.strokeDocument, observation.stroke, observation.size, project.sample?.captureStartedAtMs);
        sample = { ...sample, strokeDocument: capture.document, captureStartedAtMs: capture.captureStartedAtMs };
      } catch {
        setError("This stroke could not retain its observed timing. Your previous drawing is intact. Clear the page to start a new capture.");
        return;
      }
    }
    setPast((items) => [...items.slice(-99), project.sample]);
    setFuture([]);
    update({ sample });
  }
  function focusWorkspace(next: "draw" | "upload" | "sample") {
    requestAnimationFrame(() => {
      if (next === "draw")
        canvasStageRef.current
          ?.querySelector<HTMLCanvasElement>(".drawing-canvas")
          ?.focus({ preventScroll: true });
      else if (next === "upload")
        uploadButtonRef.current?.focus({ preventScroll: true });
      else reviewButtonRef.current?.focus({ preventScroll: true });
    });
  }
  function backToChooser() {
    approvedCapture.current = null;
    setMode("choose");
    requestAnimationFrame(() =>
      firstChoiceRef.current?.focus({ preventScroll: true }),
    );
  }
  async function removeImage() {
    if (
      !(await confirm({
        title: "Remove this image?",
        message:
          "The uploaded capture sample will be removed. Your existing letter edits will stay available.",
        confirmLabel: "Remove image",
        destructive: true,
      }))
    )
      return;
    approvedCapture.current = null;
    update({ sample: null });
    setPast([]);
    setFuture([]);
    backToChooser();
  }
  async function selectDemoSample() {
    if (!(await mayReplace("demo"))) return;
    approvedCapture.current = null;
    update({ sample: { kind: "demo" } });
    setPast([]);
    setFuture([]);
    setMode("sample");
    setError("");
    window.scrollTo({ top: 0, behavior: "auto" });
    focusWorkspace("sample");
  }
  function undo() {
    if (!past.length) return;
    const previous = past[past.length - 1];
    setPast(past.slice(0, -1));
    setFuture((items) => [project.sample, ...items]);
    update({ sample: previous });
  }
  function redo() {
    if (!future.length) return;
    const next = future[0];
    setFuture(future.slice(1));
    setPast((items) => [...items, project.sample]);
    update({ sample: next });
  }
  async function loadFile(file?: File) {
    if (!file || busy) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type)) {
      setError("Use a PNG, JPEG, or WebP image.");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      setError("Choose an image under 20 MB.");
      return;
    }
    const approved = approvedCapture.current;
    approvedCapture.current = null;
    if (
      approved !== "upload" &&
      hasSample(project) &&
      !(await confirm({
        title: "Replace your sample with this image?",
        message:
          "The current handwriting sample will be removed. Your existing letter edits will stay available.",
        confirmLabel: "Replace sample",
        destructive: true,
      }))
    )
      return;
    setError("");
    setBusy(true);
    let bitmap: ImageBitmap | null = null;
    try {
      const original = await captureRecovery.preserve(project.id, file);
      bitmap = await createImageBitmap(file);
      const scale = Math.min(1, 1000 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Image preview unavailable");
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      update({
        sample: {
          kind: "upload",
          dataUrl: canvas.toDataURL("image/jpeg", 0.72),
          fileName: file.name,
          originalCaptureId: original.id,
        },
      });
      setPast([]);
      setFuture([]);
      setMode("sample");
      window.scrollTo({ top: 0, behavior: "auto" });
      focusWorkspace("sample");
      announce("Original image bytes backed up locally. Review the smaller preview before continuing.");
    } catch {
      setError("This image could not be opened. Try another file.");
    } finally {
      bitmap?.close();
      setBusy(false);
    }
  }
  async function continueToReview() {
    if (!hasSample(project)) {
      setError(
        "Draw a mark, upload an image, or choose the demo sample first.",
      );
      return;
    }
    setBusy(true);
    try {
      await handwritingService.submitSample(project.id, project.sample!);
      setError("");
      go("review");
    } catch {
      setError("The sample could not be saved locally. Try a smaller image.");
    } finally {
      setBusy(false);
    }
  }
  async function chooseMode(next: "draw" | "upload") {
    if (!(await mayReplace(next))) return;
    approvedCapture.current = next;
    setError("");
    setMode(next);
    focusWorkspace(next);
  }
  const hasStrokeSample =
    project.sample?.kind === "draw" && strokes.length > 0;
  const sampleLabel =
    project.sample?.kind === "upload"
      ? project.sample.fileName || "Uploaded image"
      : project.sample?.kind === "demo"
        ? "Demo sample"
        : hasStrokeSample
          ? `${strokes.length} stroke${strokes.length === 1 ? "" : "s"} on paper`
          : "No sample yet";
  return (
    <div
      className={`step-content capture-step ${mode === "choose" ? "is-choosing" : "is-working"}`}
    >
      <div className="step-intro capture-intro">
        <div className="step-intro-copy">
          <span className="eyebrow">01 / CAPTURE</span>
          <h1>Start with one sample.</h1>
        </div>
        {mode !== "choose" && <StepMascot character="pen" />}
      </div>

      {mode === "choose" ? (
        <div className="capture-chooser capture-desk">
          <span className="desk-blob desk-blob-coral" aria-hidden="true" />
          <span className="desk-blob desk-blob-mint" aria-hidden="true" />
          <span className="desk-blob desk-blob-yellow" aria-hidden="true" />
          <span
            className="desk-blob desk-blob-coral desk-blob-coral-low"
            aria-hidden="true"
          />
          <svg
            className="desk-squiggle"
            viewBox="0 0 1200 240"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path d="M0 154 C 120 70, 230 216, 360 142 S 600 64, 730 150 S 980 218, 1200 120" />
          </svg>
          <svg
            className="desk-squiggle desk-squiggle-top"
            viewBox="0 0 1200 240"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path d="M0 84 C 170 190, 300 22, 470 108 S 770 214, 960 80 S 1110 36, 1200 100" />
          </svg>
          <div className="capture-choice-grid">
            <button
              ref={firstChoiceRef}
              className="capture-choice capture-choice-draw"
              disabled={busy}
              onClick={() => void chooseMode("draw")}
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
                <strong>Draw your handwriting</strong>
                <span className="choice-cue">↗</span>
              </span>
            </button>
            <button
              className="capture-choice capture-choice-upload"
              disabled={busy}
              onClick={() => void chooseMode("upload")}
            >
              <span className="choice-art" aria-hidden="true">
                <span className="choice-ghost">abc</span>
                <svg className="choice-scribble choice-scribble-upload" viewBox="0 0 300 200">
                  <path d="M18 108 C 74 24, 112 164, 170 92 S 242 58, 286 128" />
                </svg>
                <span className="choice-sheet">
                  <i />
                  <i />
                  <i />
                  <svg
                    className="choice-sheet-mark"
                    viewBox="0 0 100 40"
                  >
                    <path d="M8 28 C 20 8, 32 34, 44 18 S 66 10, 78 24 S 90 30, 94 20" />
                  </svg>
                </span>
                <span className="choice-arrow">
                  <UploadIcon />
                </span>
              </span>
              <span className="choice-copy">
                <strong>Upload your handwriting</strong>
                <span className="choice-cue">↗</span>
              </span>
            </button>
          </div>
        </div>
      ) : (
        <StepStage
          tone={
            mode === "upload" ? "yellow" : mode === "sample" ? "mint" : "coral"
          }
          number="01"
          className="capture-work-stage"
        >
          <div className="capture-mode-head">
            <div>
              <span className="capture-mode-label">
                {mode === "draw"
                  ? "Draw or write"
                  : mode === "upload"
                    ? "Upload a page"
                    : "Your sample"}
              </span>
              <span className="capture-sample-state">{sampleLabel}</span>
            </div>
            <button
              className="text-button"
              disabled={busy}
              onClick={backToChooser}
            >
              ← Change method
            </button>
          </div>

          {mode === "draw" && (
            <div className="capture-panel" aria-busy={busy}>
              <div
                className="capture-toolbar"
                role="toolbar"
                aria-label="Drawing tools"
              >
                <WizardAction
                  label="Undo"
                  hint={past.length ? "Undo the last drawing change" : "Undo · no changes yet"}
                  icon={<UndoIcon />}
                  onClick={undo}
                  disabled={!past.length}
                />
                <WizardAction
                  label="Redo"
                  hint={future.length ? "Redo the last undone change" : "Redo · nothing to redo"}
                  icon={<RedoIcon />}
                  onClick={redo}
                  disabled={!future.length}
                />
                <WizardAction
                  label="Clear page"
                  hint="Clear every stroke · Undo can restore your drawing"
                  icon={<TrashIcon />}
                  showLabel
                  onClick={() => void changeStrokes([])}
                  disabled={!strokes.length}
                />
                <span className="capture-strokes">
                  {strokes.length} stroke{strokes.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="canvas-stage" ref={canvasStageRef}>
                <DrawingCanvas
                  strokes={strokes}
                  onChange={(next) => void changeStrokes(next)}
                  onObservedStroke={(stroke, size) => {
                    observed.current = { stroke, size };
                  }}
                  label="Drawing area for handwriting sample"
                  height="clamp(300px, 42vh, 420px)"
                />
                {!strokes.length && (
                  <div className="canvas-hint" aria-hidden="true">
                    <span>Aa</span>
                    <small>Write your name or a short sentence</small>
                  </div>
                )}
              </div>
              <div className="capture-actions">
                <span className="capture-hint">
                  {hasStrokeSample
                    ? "Sample ready. Review the letters when you are."
                    : "Make at least one mark to continue."}
                </span>
                <button
                  className="text-button capture-demo-button"
                  disabled={busy}
                  onClick={() => void selectDemoSample()}
                >
                  Try a demo
                </button>
                <button
                  className="button button-dark"
                  disabled={busy || !hasStrokeSample}
                  onClick={() => void continueToReview()}
                >
                  Review letters <span>→</span>
                </button>
              </div>
            </div>
          )}

          {mode === "upload" && (
            <div className="capture-panel" aria-busy={busy}>
              <div
                className="upload-zone"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  void loadFile(event.dataTransfer.files[0]);
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
                  Or choose a clear photo or scan. PNG, JPEG, WebP · up to 20 MB.
                </p>
                <button
                  ref={uploadButtonRef}
                  className="button button-dark"
                  disabled={busy}
                  onClick={() => uploadRef.current?.click()}
                >
                  {busy ? "Opening image…" : "Choose an image"}
                </button>
                <input
                  ref={uploadRef}
                  aria-label="Choose handwriting image"
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => {
                    void loadFile(event.target.files?.[0]);
                    event.target.value = "";
                  }}
                  hidden
                />
                <button
                  className="camera-label text-button"
                  disabled={busy}
                  onClick={() => cameraRef.current?.click()}
                >
                  Use camera on mobile
                </button>
                <input
                  ref={cameraRef}
                  aria-label="Photograph handwriting"
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  capture="environment"
                  onChange={(event) => {
                    void loadFile(event.target.files?.[0]);
                    event.target.value = "";
                  }}
                  hidden
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
              <div className="capture-actions">
                <span className="capture-hint">
                  A clear, flat photo works better than a shadowed one.
                </span>
                <button
                  className="text-button capture-demo-button"
                  disabled={busy}
                  onClick={() => void selectDemoSample()}
                >
                  Try a demo
                </button>
              </div>
            </div>
          )}

          {mode === "sample" && (
            <div className="capture-panel" aria-busy={busy}>
              {project.sample?.kind === "upload" ? (
                <div className="sample-preview">
                  <Image
                    src={project.sample.dataUrl || ""}
                    alt={`Uploaded handwriting sample ${project.sample.fileName || ""}`}
                    width={1000}
                    height={1000}
                    unoptimized
                    style={{ width: "auto", height: "auto" }}
                  />
                  <div>
                    <strong>{project.sample.fileName}</strong>
                    <button onClick={() => void removeImage()}>
                      Remove image
                    </button>
                  </div>
                </div>
              ) : project.sample?.kind === "draw" && strokes.length ? (
                <div className="sample-preview">
                  <DrawingCanvas
                    strokes={strokes}
                    onChange={(next) => void changeStrokes(next)}
                    label="Your current handwriting sample"
                    height={260}
                    readOnly
                  />
                </div>
              ) : project.sample?.kind === "demo" ? (
                <div className="sample-preview demo-written">
                  the quick brown fox
                  <br />
                  jumps over a little moon.
                  <small>Demo sample, not your handwriting</small>
                </div>
              ) : (
                <div className="empty-capture">
                  Nothing on the page yet. Draw, upload, or try the sample.
                </div>
              )}
              <div className="capture-actions">
                {project.sample?.kind === "draw" ? (
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => {
                      setMode("draw");
                      focusWorkspace("draw");
                    }}
                  >
                    Add more strokes
                  </button>
                ) : project.sample?.kind === "upload" ? (
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => {
                      setMode("upload");
                      focusWorkspace("upload");
                    }}
                  >
                    Replace image
                  </button>
                ) : (
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={backToChooser}
                  >
                    Choose your own
                  </button>
                )}
                <button
                  ref={reviewButtonRef}
                  className="button button-dark"
                  disabled={busy}
                  onClick={() => void continueToReview()}
                >
                  Review letters <span>→</span>
                </button>
              </div>
            </div>
          )}
        </StepStage>
      )}

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {mode !== "choose" && (
        <CaptureCloudPanel
          localProjectId={project.id}
          sample={project.sample}
        />
      )}
      {confirmationDialog}
    </div>
  );
}
