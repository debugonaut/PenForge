"use client";

import { useRef, useState } from "react";
import { presets, sampleStyle } from "./specimen";
import { StepMascot, StepStage } from "./StepStage";
import { WizardAction } from "./WizardAction";
import { ResetIcon, UndoIcon } from "./WizardIcons";
import type { AnnouncedPanelProps } from "./creation-state";
import type { FontStyle } from "@/lib/contracts";
import { fontService } from "@/lib/mock-services";

export function StylePanel({
  project,
  update,
  go,
  announce,
}: AnnouncedPanelProps) {
  const [instruction, setInstruction] = useState("");
  const [previous, setPrevious] = useState<FontStyle | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const adjustment = useRef<{ style: FontStyle; recorded: boolean } | null>(
    null,
  );
  function apply(style: FontStyle) {
    setPrevious(project.style);
    update({ style });
  }
  async function describe() {
    if (!instruction.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await fontService.suggestStyle(project.id, instruction);
      apply(result.style);
      announce(result.job.message || "Demo style applied.");
    } catch {
      setError("Could not apply this local suggestion. Try again.");
    } finally {
      setBusy(false);
    }
  }
  function beginAdjustment() {
    if (!adjustment.current)
      adjustment.current = { style: project.style, recorded: false };
  }
  function adjust(key: keyof FontStyle, value: number) {
    if (!adjustment.current) setPrevious(project.style);
    else if (!adjustment.current.recorded) {
      setPrevious(adjustment.current.style);
      adjustment.current.recorded = true;
    }
    update({ style: { ...project.style, [key]: value } });
  }
  const sliders: {
    key: keyof FontStyle;
    name: string;
    min: number;
    max: number;
  }[] = [
    { key: "thickness", name: "Ink weight", min: 20, max: 85 },
    { key: "slant", name: "Slant", min: -20, max: 25 },
    { key: "spacing", name: "Letter spacing", min: 20, max: 80 },
    { key: "smoothness", name: "Smoothness", min: 20, max: 90 },
    { key: "proportion", name: "Proportion", min: 25, max: 75 },
  ];
  return (
    <div className="step-content">
      <div className="step-intro">
        <div className="step-intro-copy">
          <span className="eyebrow">04 / STYLE</span>
          <h1>Find its voice.</h1>
          <p>
            Try a direction or move a control. Every change is reversible; this
            specimen uses a demo typeface.
          </p>
        </div>
        <StepMascot character="quill" />
      </div>
      <StepStage tone="coral" number="04">
      <div className="style-workspace">
        <div className="style-panel">
          <span className="scene-number">QUICK STARTS</span>
          <div className="preset-grid">
            {presets.map((preset) => (
              <button
                key={preset.name}
                disabled={busy}
                aria-pressed={
                  JSON.stringify(project.style) === JSON.stringify(preset.style)
                }
                className={
                  JSON.stringify(project.style) === JSON.stringify(preset.style)
                    ? "active"
                    : ""
                }
                onClick={() => apply(preset.style)}
              >
                <span
                  className="preset-sample"
                  style={sampleStyle(preset.style)}
                  aria-hidden="true"
                >
                  Aa
                </span>
                <span className="preset-copy">
                  <strong>{preset.name}</strong>
                  <small>{preset.description}</small>
                </span>
              </button>
            ))}
          </div>
          <span className="scene-number control-heading">MAKE IT YOURS</span>
          {sliders.map((slider) => (
            <div className="control-row" key={slider.key}>
              <label htmlFor={`style-${slider.key}`}>
                {slider.name}
                <b>{project.style[slider.key]}</b>
              </label>
              <input
                id={`style-${slider.key}`}
                type="range"
                min={slider.min}
                max={slider.max}
                value={project.style[slider.key]}
                disabled={busy}
                onChange={(event) =>
                  adjust(slider.key, Number(event.target.value))
                }
                onPointerDown={beginAdjustment}
                onPointerUp={() => {
                  adjustment.current = null;
                }}
                onPointerCancel={() => {
                  adjustment.current = null;
                }}
                onKeyDown={(event) => {
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
                }}
                onKeyUp={() => {
                  adjustment.current = null;
                }}
                onBlur={() => {
                  adjustment.current = null;
                }}
              />
            </div>
          ))}
          <div className="describe-control">
            <label htmlFor="describe-style">
              Describe a change <span>LOCAL DEMO</span>
            </label>
            <div>
              <input
                id="describe-style"
                value={instruction}
                disabled={busy}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void describe();
                }}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder="Make it bolder and a little slanted"
              />
              <button
                disabled={busy || !instruction.trim()}
                onClick={() => void describe()}
              >
                Apply
              </button>
            </div>
            <small>
              Simple words map to local settings. No AI model is called.
            </small>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
          </div>
        </div>
        <div className="style-preview">
          <div className="preview-topline">
            <span>LIVE SPECIMEN · DEMO TYPEFACE</span>
            <WizardAction
              label="Undo change"
              hint="Restore your previous style settings"
              icon={<UndoIcon />}
              showLabel
              onClick={() => previous && apply(previous)}
              disabled={!previous || busy}
            />
          </div>
          <div className="specimen-art" style={sampleStyle(project.style)}>
            The shape
            <br />
            of <em>your</em> words.
          </div>
          <div className="specimen-alphabet" style={sampleStyle(project.style)}>
            Aa Bb Cc Dd Ee Ff Gg Hh Ii Jj Kk
          </div>
          <div className="preview-footline">
            <span>YOUR STYLE SETTINGS</span>
            <WizardAction
              label="Reset to natural"
              icon={<ResetIcon />}
              showLabel
              disabled={busy}
              onClick={() => apply(presets[0].style)}
            />
          </div>
        </div>
      </div>
      <div className="step-actions">
        <button className="text-button" onClick={() => go("edit")}>
          ← Back to edit
        </button>
        <button className="button button-dark" onClick={() => go("preview")}>
          See it in use <span>→</span>
        </button>
      </div>
      </StepStage>
    </div>
  );
}
