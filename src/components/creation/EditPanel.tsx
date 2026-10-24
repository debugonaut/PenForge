"use client";

import { useRef, useState } from "react";
import { DrawingCanvas } from "./DrawingCanvas";
import { GlyphShape } from "./GlyphShape";
import { StepMascot, StepStage } from "./StepStage";
import { WizardAction } from "./WizardAction";
import {
  EraserIcon, EyeIcon, PenIcon, RedoIcon, ResetIcon,
  RotateLeftIcon, RotateRightIcon, UndoIcon,
} from "./WizardIcons";
import type { CreationPanelProps, GlyphHistoryStore } from "./creation-state";
import type { Glyph } from "@/lib/contracts";

export function EditPanel({
  project,
  update,
  go,
  histories,
}: CreationPanelProps & { histories: GlyphHistoryStore }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawing, setDrawing] = useState(false);
  const adjustment = useRef<{ glyph: Glyph; recorded: boolean } | null>(null);
  const selected =
    project.glyphs.find((g) => g.id === selectedId) || project.glyphs[0];
  const historyKey = selected ? `${project.id}:${selected.id}` : "";
  const history = histories.current[historyKey] || { past: [], future: [] };
  function record(glyph: Glyph) {
    histories.current[`${project.id}:${glyph.id}`] = {
      past: [...history.past.slice(-99), glyph],
      future: [],
    };
  }
  function updateGlyph(changes: Partial<Glyph>, remember = true) {
    if (!selected) return;
    if (remember) record(selected);
    const next: Glyph = {
      ...selected,
      ...changes,
      revision: selected.revision + 1,
      status: "edited",
    };
    update({
      glyphs: project.glyphs.map((g) => (g.id === selected.id ? next : g)),
    });
  }
  function restore(glyph: Glyph) {
    update({
      glyphs: project.glyphs.map((g) =>
        g.id === glyph.id ? { ...glyph, revision: g.revision + 1 } : g,
      ),
    });
  }
  function undo() {
    if (!history.past.length || !selected) return;
    adjustment.current = null;
    histories.current[historyKey] = {
      past: history.past.slice(0, -1),
      future: [selected, ...history.future],
    };
    restore(history.past[history.past.length - 1]);
  }
  function redo() {
    if (!history.future.length || !selected) return;
    adjustment.current = null;
    histories.current[historyKey] = {
      past: [...history.past, selected],
      future: history.future.slice(1),
    };
    restore(history.future[0]);
  }
  function beginAdjustment() {
    if (selected && !adjustment.current)
      adjustment.current = { glyph: selected, recorded: false };
  }
  function adjust(changes: Partial<Glyph>) {
    if (!selected) return;
    if (!adjustment.current) {
      updateGlyph(changes);
      return;
    }
    if (!adjustment.current.recorded) {
      record(adjustment.current.glyph);
      adjustment.current.recorded = true;
    }
    updateGlyph(changes, false);
  }
  function shortcut(event: React.KeyboardEvent<HTMLDivElement>) {
    if (
      !(event.metaKey || event.ctrlKey) ||
      event.altKey ||
      (event.target as HTMLElement).closest(
        "input,textarea,select,[contenteditable=true]",
      )
    )
      return;
    if (event.key.toLowerCase() === "z") {
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    } else if (event.key.toLowerCase() === "y") {
      event.preventDefault();
      redo();
    }
  }
  const sliderEvents = {
    onPointerDown: beginAdjustment,
    onPointerUp: () => {
      adjustment.current = null;
    },
    onPointerCancel: () => {
      adjustment.current = null;
    },
    onBlur: () => {
      adjustment.current = null;
    },
    onKeyDown: (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (
        [
          "ArrowLeft",
          "ArrowRight",
          "ArrowUp",
          "ArrowDown",
          "Home",
          "End",
          "PageUp",
          "PageDown",
        ].includes(event.key)
      )
        beginAdjustment();
    },
    onKeyUp: () => {
      adjustment.current = null;
    },
  };
  return (
    <div className="step-content" onKeyDown={shortcut}>
      <div className="step-intro">
        <div className="step-intro-copy">
          <span className="eyebrow">03 / EDIT</span>
          <h1>Keep the good quirks.</h1>
          <p>
            Pick a letter, adjust its shape, and undo anything. The original demo
            source stays available.
          </p>
        </div>
        <StepMascot character="pen" />
      </div>
      <StepStage tone="yellow" number="03">
      {!selected ? (
        <div className="empty-state">
          <p>No glyphs yet. Prepare demo letters in Review first.</p>
          <button onClick={() => go("review")}>Go to review</button>
        </div>
      ) : (
        <div className="editor-workspace">
          <div className="editor-side">
            <span className="scene-number">YOUR ALPHABET</span>
            <div
              className="editor-glyph-list"
              role="group"
              aria-label="Choose a letter to edit"
            >
              {project.glyphs.map((g) => (
                <button
                  key={g.id}
                  aria-label={`Edit ${g.character}`}
                  aria-pressed={selected.id === g.id}
                  className={selected.id === g.id ? "active" : ""}
                  onClick={() => {
                    setSelectedId(g.id);
                    setDrawing(false);
                    adjustment.current = null;
                  }}
                >
                  {g.character}
                </button>
              ))}
            </div>
          </div>
          <div className="editor-main">
            <div className="editor-toolbar" role="group" aria-label="Letter editing tools">
              <WizardAction
                label={drawing ? "Show preview" : "Draw / replace"}
                hint={drawing ? "Show the letter preview" : "Draw a replacement for this letter"}
                icon={drawing ? <EyeIcon /> : <PenIcon />}
                showLabel
                onClick={() => setDrawing(!drawing)}
                aria-pressed={drawing}
              />
              <WizardAction
                label="Erase last mark"
                icon={<EraserIcon />}
                onClick={() =>
                  updateGlyph({ strokes: selected.strokes.slice(0, -1) })
                }
                disabled={!selected.strokes.length}
              />
              <WizardAction
                label="Rotate selected letter counterclockwise 15 degrees"
                hint="Rotate left · 15°"
                icon={<RotateLeftIcon />}
                onClick={() =>
                  updateGlyph({ rotation: selected.rotation - 15 })
                }
              />
              <WizardAction
                label="Rotate selected letter clockwise 15 degrees"
                hint="Rotate right · 15°"
                icon={<RotateRightIcon />}
                onClick={() =>
                  updateGlyph({ rotation: selected.rotation + 15 })
                }
              />
              <WizardAction
                label="Undo"
                hint="Undo · ⌘/Ctrl Z"
                icon={<UndoIcon />}
                onClick={undo}
                disabled={!history.past.length}
              />
              <WizardAction
                label="Redo"
                hint="Redo · ⌘/Ctrl Shift Z"
                icon={<RedoIcon />}
                onClick={redo}
                disabled={!history.future.length}
              />
            </div>
            <div className="editor-canvas" key={selected.id}>
              {drawing ? (
                <DrawingCanvas
                  key={selected.id}
                  label={`Draw replacement for ${selected.character}`}
                  strokes={selected.strokes}
                  onChange={(strokes) => updateGlyph({ strokes })}
                  height={340}
                />
              ) : (
                <GlyphShape glyph={selected} />
              )}
            </div>
            <div className="editor-bottom">
              <div>
                <strong>Editing “{selected.character}”</strong>
                <small>Original source stays available</small>
              </div>
              <WizardAction
                label="Revert this letter"
                icon={<ResetIcon />}
                showLabel
                onClick={() =>
                  updateGlyph({
                    character: selected.sourceCharacter,
                    strokes: [],
                    rotation: 0,
                    scale: 100,
                    crop: 0,
                  })
                }
              />
            </div>
          </div>
          <aside className="editor-controls">
            <span className="scene-number">FINE TUNE</span>
            <label htmlFor="glyph-size">
              Size <b>{selected.scale}%</b>
            </label>
            <input
              id="glyph-size"
              type="range"
              min="60"
              max="140"
              value={selected.scale}
              onChange={(event) =>
                adjust({ scale: Number(event.target.value) })
              }
              {...sliderEvents}
            />
            <label htmlFor="glyph-crop">
              Crop <b>{selected.crop}%</b>
            </label>
            <input
              id="glyph-crop"
              type="range"
              min="0"
              max="30"
              value={selected.crop}
              onChange={(event) => adjust({ crop: Number(event.target.value) })}
              {...sliderEvents}
            />
            <div className="original-box">
              <span>ORIGINAL / DEMO SOURCE</span>
              <strong>{selected.sourceCharacter}</strong>
            </div>
            <p>
              These edits affect a visual prototype. Actual vector outlines and
              font generation need a future backend.
            </p>
          </aside>
        </div>
      )}
      <div className="step-actions">
        <button className="text-button" onClick={() => go("review")}>
          ← Back to review
        </button>            <button
              className="button button-dark"
              disabled={!selected}
              onClick={() => go("style")}
            >
              Style the set <span>→</span>
            </button>
          </div>
      </StepStage>
    </div>
  );
}
