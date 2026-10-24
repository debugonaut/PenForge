"use client";

import { useState } from "react";
import { PenCharacter } from "@/components/brand/Characters";
import { GlyphShape } from "./GlyphShape";
import { StepMascot, StepStage } from "./StepStage";
import { AlertIcon, CheckIcon, EyeIcon, MissingIcon, RedoIcon, UndoIcon } from "./WizardIcons";
import { WizardAction } from "./WizardAction";
import type { AnnouncedPanelProps } from "./creation-state";
import type { Glyph } from "@/lib/contracts";
import { handwritingService, projectService } from "@/lib/mock-services";

export function ReviewPanel({
  project,
  update,
  go,
  announce,
}: AnnouncedPanelProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [assignment, setAssignment] = useState<string | null>(null);
  const [history, setHistory] = useState<Glyph[][]>([]);
  const [future, setFuture] = useState<Glyph[][]>([]);
  const selected =
    project.glyphs.find((glyph) => glyph.id === selectedId) ||
    project.glyphs[0];
  async function extract() {
    setBusy(true);
    setError("");
    announce("Preparing the demo alphabet.");
    try {
      const result = await handwritingService.extract(project.id);
      const fresh = await projectService.get(project.id);
      update({ glyphs: fresh.glyphs });
      announce(result.message || "Demo letters ready.");
    } catch {
      setError("Demo extraction failed. Try again.");
    } finally {
      setBusy(false);
    }
  }
  function commit(glyphs: Glyph[]) {
    setHistory((items) => [...items.slice(-99), project.glyphs]);
    setFuture([]);
    update({ glyphs });
    setError("");
  }
  function mutateGlyph(changes: Partial<Glyph>) {
    if (!selected) return;
    const next = { ...selected, ...changes, revision: selected.revision + 1 };
    commit(
      project.glyphs.map((item) => (item.id === selected.id ? next : item)),
    );
  }
  function applyAssignment() {
    if (!selected) return;
    const character = (assignment ?? selected.character).trim();
    if ([...character].length !== 1) {
      setError("Assign one visible character to this letter.");
      return;
    }
    if (
      project.glyphs.some(
        (glyph) => glyph.id !== selected.id && glyph.character === character,
      )
    ) {
      setError(
        `“${character}” is already assigned to another letter. Choose an unassigned character.`,
      );
      return;
    }
    if (character !== selected.character)
      mutateGlyph({ character, status: "edited" });
  }
  const counts = {
    ready: project.glyphs.filter(
      (g) => g.status !== "needs-review" && g.status !== "missing",
    ).length,
    review: project.glyphs.filter((g) => g.status === "needs-review").length,
    missing: project.glyphs.filter((g) => g.status === "missing").length,
  };
  const unapproved = project.glyphs.some(
    (g) => g.status === "detected" || g.status === "edited",
  );
  return (
    <div className="step-content">
      <div className="step-intro">
        <div className="step-intro-copy">
          <span className="eyebrow">02 / REVIEW</span>
          <h1>Meet your letters.</h1>
          <p>
            Inspect these demo suggestions, correct any assignment, and approve
            what is ready. Your handwriting has not been processed by a model.
          </p>
        </div>
        <StepMascot character="quill" />
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {!project.glyphs.length ? (
        <StepStage tone="mint" number="02">
          <div className="empty-state" aria-busy={busy}>
            <PenCharacter mood="happy" />
            <div>
              <span className="demo-tag">DEMO EXTRACTION</span>
              <h2>Ready to sort the alphabet?</h2>
              <p>
                We’ll create a sample glyph set so you can try the review and
                editing tools.
              </p>
              <button
                disabled={busy}
                className="button button-dark"
                onClick={() => void extract()}
              >
                {busy ? "Preparing demo letters…" : "Prepare demo letters"}{" "}
                <span>→</span>
              </button>
            </div>
          </div>
        </StepStage>
      ) : (
        <StepStage tone="mint" number="02">
          <div className="review-summary">
            <span>
              <CheckIcon />
              <b>{counts.ready}</b> ready
            </span>
            <span>
              <EyeIcon />
              <b>{counts.review}</b> need review
            </span>
            <span>
              <AlertIcon />
              <b>{counts.missing}</b> missing
            </span>
            <button
              disabled={!unapproved}
              onClick={() => {
                commit(
                  project.glyphs.map((g) =>
                    g.status === "detected" || g.status === "edited"
                      ? { ...g, status: "approved", revision: g.revision + 1 }
                      : g,
                  ),
                );
                announce(
                  "Ready letters approved. Missing letters and uncertain suggestions still need attention.",
                );
              }}
            >
              Approve ready letters
            </button>
          </div>
          <div className="review-workspace">
            <div className="glyph-grid" role="group" aria-label="Demo glyphs">
              {project.glyphs.map((glyph, index) => (
                <button
                  key={glyph.id}
                  style={{
                    animationDelay: `calc(var(--glyph-step, 14ms) * ${Math.min(index, 18)})`,
                  }}
                  className={`glyph-button status-${glyph.status} ${selected?.id === glyph.id ? "selected" : ""}`}
                  aria-pressed={selected?.id === glyph.id}
                  aria-label={`${glyph.character}, ${glyph.status.replace("-", " ")}`}
                  onClick={() => {
                    setSelectedId(glyph.id);
                    setAssignment(null);
                    setError("");
                  }}
                >
                  <span>{glyph.character}</span>
                  <small>
                    {glyph.status === "needs-review"
                      ? "Review"
                      : glyph.status === "missing"
                        ? "Missing"
                        : glyph.status === "approved"
                          ? "Approved"
                          : glyph.status === "edited"
                            ? "Edited"
                            : "Ready"}
                  </small>
                </button>
              ))}
            </div>
            {selected && (
              <aside className="glyph-inspector">
                <span className="scene-number">SELECTED LETTER</span>
                <div className="inspector-glyph">
                  <GlyphShape glyph={selected} />
                </div>
                <h3>
                  “{selected.character}”{" "}
                  <span className={`status-chip status-${selected.status}`}>
                    {selected.status.replace("-", " ")}
                  </span>
                </h3>
                <p>
                  Suggested from the demo set. Confirm the assignment or mark it
                  missing. Your decision is saved locally.
                </p>
                <label htmlFor="assignment">Assigned character</label>
                <div className="assignment-row">
                  <input
                    id="assignment"
                    maxLength={2}
                    value={assignment ?? selected.character}
                    onChange={(event) => setAssignment(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") applyAssignment();
                    }}
                  />
                  <button onClick={applyAssignment}>Apply</button>
                </div>
                <div className="inspector-actions">
                  <WizardAction
                    label="Approve"
                    hint="Approve this letter and its assignment"
                    icon={<CheckIcon />}
                    showLabel
                    disabled={selected.status === "approved"}
                    onClick={() => {
                      mutateGlyph({ status: "approved" });
                      announce(`“${selected.character}” approved.`);
                    }}
                  />
                  <WizardAction
                    label="Mark missing"
                    icon={<MissingIcon />}
                    showLabel
                    disabled={selected.status === "missing"}
                    onClick={() => mutateGlyph({ status: "missing" })}
                  />
                  <WizardAction
                    label="Undo"
                    hint="Undo the last review change"
                    icon={<UndoIcon />}
                    disabled={!history.length}
                    onClick={() => {
                      update({ glyphs: history[history.length - 1] });
                      setHistory(history.slice(0, -1));
                      setFuture((items) => [project.glyphs, ...items]);
                      setAssignment(null);
                    }}
                  />
                  <WizardAction
                    label="Redo"
                    hint="Redo the last undone review change"
                    icon={<RedoIcon />}
                    disabled={!future.length}
                    onClick={() => {
                      update({ glyphs: future[0] });
                      setFuture(future.slice(1));
                      setHistory((items) => [...items, project.glyphs]);
                      setAssignment(null);
                    }}
                  />
                </div>
              </aside>
            )}
          </div>
          <div className="step-actions">
            <button className="text-button" onClick={() => go("capture")}>
              ← Back to capture
            </button>
            <button className="button button-dark" onClick={() => go("edit")}>
              Edit letters <span>→</span>
            </button>
          </div>
        </StepStage>
      )}
    </div>
  );
}
