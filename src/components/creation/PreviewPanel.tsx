"use client";

import { useState } from "react";
import { sampleStyle } from "./specimen";
import { StepMascot, StepStage } from "./StepStage";
import { CardIcon, JournalIcon, LetterIcon } from "./WizardIcons";
import type { CreationPanelProps } from "./creation-state";

const surfaces = {
  letter: { label: "Letter", Icon: LetterIcon },
  journal: { label: "Journal", Icon: JournalIcon },
  card: { label: "Card", Icon: CardIcon },
} as const;

export function PreviewPanel({ project, update, go }: CreationPanelProps) {
  const [size, setSize] = useState(48);
  const [surface, setSurface] = useState<"letter" | "journal" | "card">(
    "letter",
  );
  const missing = project.glyphs.filter((g) => g.status === "missing");
  const unavailable = [...new Set([...project.previewText])].filter(
    (character) =>
      !/\s/.test(character) &&
      !project.glyphs.some(
        (g) => g.character === character && g.status !== "missing",
      ),
  );
  return (
    <div className="step-content">
      <div className="step-intro">
        <div className="step-intro-copy">
          <span className="eyebrow">05 / PREVIEW</span>
          <h1>Words out in the world.</h1>
          <p>
            Type anything and try a surface. This is a layout simulation with a
            demo typeface.
          </p>
        </div>
        <StepMascot character="pen" />
      </div>
      <StepStage tone="mint" number="05">
      <div className="preview-workspace">
        <div className="preview-controls">
          <label htmlFor="preview-text">Your words</label>
          <textarea
            id="preview-text"
            rows={4}
            value={project.previewText}
            onChange={(event) => update({ previewText: event.target.value })}
          />
          <label htmlFor="preview-size">
            Text size <b>{size}px</b>
          </label>
          <input
            id="preview-size"
            type="range"
            min="26"
            max="74"
            value={size}
            onChange={(event) => setSize(Number(event.target.value))}
          />
          <label htmlFor="preview-spacing">
            Letter spacing <b>{project.style.spacing}</b>
          </label>
          <input
            id="preview-spacing"
            type="range"
            min="20"
            max="80"
            value={project.style.spacing}
            onChange={(event) =>
              update({
                style: {
                  ...project.style,
                  spacing: Number(event.target.value),
                },
              })
            }
          />
          <span className="scene-number">CHOOSE A SURFACE</span>
          <div className="surface-buttons">
            {(["letter", "journal", "card"] as const).map((item) => {
              const { label, Icon } = surfaces[item];
              return (
                <button
                  className={surface === item ? "active" : ""}
                  aria-pressed={surface === item}
                  key={item}
                  onClick={() => setSurface(item)}
                >
                  <Icon />
                  <span>{label}</span>
                </button>
              );
            })}
          </div>
          <div className="coverage-box">
            <strong>Character coverage</strong>
            <span>
              {project.glyphs.length - missing.length} / {project.glyphs.length}{" "}
              demo characters present
            </span>
            <p>
              {missing.length
                ? `Missing examples: ${missing
                    .slice(0, 12)
                    .map((g) => g.character)
                    .join(" ")}`
                : "All demo characters present."}
            </p>
            {unavailable.length > 0 && (
              <p>Unavailable in your text: {unavailable.join(" ")}</p>
            )}
            <button onClick={() => go("review")}>Return to review →</button>
          </div>
        </div>
        <div className={`mockup-frame surface-${surface}`}>
          <div className="mockup-paper">
            <span className="mockup-kicker">
              {surface === "letter"
                ? "A LETTER TO KEEP"
                : surface === "journal"
                  ? "NOTES TO SELF"
                  : "JUST FOR YOU"}
            </span>
            <p
              style={{
                ...sampleStyle(project.style),
                fontSize: size,
                whiteSpace: "pre-wrap",
              }}
            >
              {project.previewText || "Your words go here."}
            </p>
            <span className="mockup-sign">with love, from my own hand</span>
          </div>
          <span className="mockup-disclaimer">
            Layout preview · demo typeface
          </span>
        </div>
      </div>
      <div className="step-actions">
        <button className="text-button" onClick={() => go("style")}>
          ← Back to style
        </button>
        <button className="button button-dark" onClick={() => go("export")}>
          Check export <span>→</span>
        </button>
      </div>
      </StepStage>
    </div>
  );
}
