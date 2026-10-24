"use client";

import Link from "next/link";
import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import PaperThumbnail from "@/components/shared/PaperThumbnail";
import TypographyEndpoints from "@/components/shared/TypographyEndpoints";
import styles from "./FinishDemo.module.css";

type Ink = "#192e35" | "#7c4146" | "#3f6553";
type Paper = "cotton" | "graph";
type FinishStyle = {
  weight: number;
  spacing: number;
  slant: number;
  lineHeight: number;
  fontSize: number;
  ink: Ink;
  paper: Paper;
};

const defaultStyle: FinishStyle = {
  weight: 500,
  spacing: 0,
  slant: 0,
  lineHeight: 1.25,
  fontSize: 44,
  ink: "#192e35",
  paper: "cotton",
};
const presets: { label: string; style: FinishStyle }[] = [
  { label: "As written", style: defaultStyle },
  {
    label: "Bookish",
    style: {
      weight: 450,
      spacing: 0.6,
      slant: 3,
      lineHeight: 1.4,
      fontSize: 40,
      ink: "#3f6553",
      paper: "graph",
    },
  },
  {
    label: "Bold note",
    style: {
      weight: 700,
      spacing: 0.1,
      slant: -3,
      lineHeight: 1.15,
      fontSize: 52,
      ink: "#7c4146",
      paper: "cotton",
    },
  },
];
const inks: { label: string; value: Ink }[] = [
  { label: "Midnight", value: "#192e35" },
  { label: "Burgundy", value: "#7c4146" },
  { label: "Forest", value: "#3f6553" },
];

function sameStyle(a: FinishStyle, b: FinishStyle) {
  return (
    a.weight === b.weight &&
    a.spacing === b.spacing &&
    a.slant === b.slant &&
    a.lineHeight === b.lineHeight &&
    a.fontSize === b.fontSize &&
    a.ink === b.ink &&
    a.paper === b.paper
  );
}

type RangeControlProps = {
  kind: "weight" | "spacing" | "slant" | "size" | "lineHeight";
  name: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  output: string;
  valueText: string;
  onChange: (value: number) => void;
};

function RangeControl({
  kind,
  name,
  label,
  value,
  min,
  max,
  step = 1,
  output,
  valueText,
  onChange,
}: RangeControlProps) {
  const id = `finish-${name}`;
  return (
    <div className={styles.control}>
      <label htmlFor={id}>
        {label}
        <output htmlFor={id}>{output}</output>
      </label>
      <div className={styles.rangeTrack} data-kind={kind}>
        <TypographyEndpoints kind={kind} className={styles.endpoints} />
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          aria-valuetext={valueText}
        />
      </div>
    </div>
  );
}

export default function FinishDemo() {
  const proofText = useRef<HTMLTextAreaElement>(null);
  const [settings, setSettings] = useState<FinishStyle>(defaultStyle);
  const [words, setWords] = useState("A little more me.\nEvery word, my own.");
  const isDefault = sameStyle(settings, defaultStyle);

  useLayoutEffect(() => {
    const textarea = proofText.current;
    if (!textarea) return;
    let frame = 0;
    let disposed = false;
    let lastWidth = 0;

    function fitText() {
      if (disposed || !textarea) return;
      const computed = window.getComputedStyle(textarea);
      const minimum = parseFloat(computed.minHeight) || 128;
      const borders =
        (parseFloat(computed.borderTopWidth) || 0) +
        (parseFloat(computed.borderBottomWidth) || 0);
      // Collapse to the CSS minimum before measuring so removing text can also
      // shrink the proof. Larger edits remain scrollable within the paper.
      textarea.style.height = `${minimum}px`;
      const required = Math.ceil(textarea.scrollHeight + borders);
      textarea.style.height = `${Math.min(360, Math.max(minimum, required))}px`;
      if (required <= 360) textarea.scrollTop = 0;
    }

    function scheduleFit() {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(fitText);
    }

    fitText();
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (Math.abs(width - lastWidth) < 0.5) return;
      lastWidth = width;
      scheduleFit();
    });
    observer.observe(textarea.parentElement ?? textarea);
    window.addEventListener("resize", scheduleFit);
    document.fonts.addEventListener("loadingdone", scheduleFit);
    void document.fonts.ready.then(() => {
      if (!disposed) scheduleFit();
    });

    return () => {
      disposed = true;
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", scheduleFit);
      document.fonts.removeEventListener("loadingdone", scheduleFit);
    };
  }, [
    words,
    settings.weight,
    settings.spacing,
    settings.slant,
    settings.lineHeight,
    settings.fontSize,
  ]);

  function update<K extends keyof FinishStyle>(key: K, value: FinishStyle[K]) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  const writeHref = `/write?${new URLSearchParams({
    weight: String(settings.weight),
    spacing: settings.spacing.toFixed(1),
    slant: String(settings.slant),
    ink: settings.ink,
    text: words,
    paper: settings.paper,
    fontSize: String(settings.fontSize),
    lineHeight: settings.lineHeight.toFixed(2),
  }).toString()}`;

  return (
    <div className={styles.finish}>
      <div className={styles.headingBlock}>
        <h2 id="finish-title">
          Your handwriting.<span>Your way.</span>
        </h2>
      </div>

      <div className={styles.editor}>
        <div className={styles.toolbar}>
          <span className={styles.toolbarLabel}>Presets</span>
          <div
            className={styles.presets}
            role="group"
            aria-label="Style presets"
          >
            {presets.map((preset) => (
              <button
                type="button"
                key={preset.label}
                aria-pressed={sameStyle(settings, preset.style)}
                onClick={() => setSettings({ ...preset.style })}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        <div
          className={`${styles.proof} ${settings.paper === "graph" ? styles.graph : styles.cotton}`}
          aria-label="Sample handwriting style proof"
          style={
            {
              "--proof-weight": settings.weight,
              "--proof-spacing": `${settings.spacing}px`,
              "--proof-slant": `${-settings.slant}deg`,
              "--proof-leading": settings.lineHeight,
              "--proof-size": `${settings.fontSize}px`,
              "--proof-ink": settings.ink,
            } as CSSProperties
          }
        >
          <div className={styles.proofHeading}>
            <label htmlFor="finish-proof-text">Your words</label>
            <span aria-hidden="true">{words.length}/100</span>
          </div>
          <textarea
            ref={proofText}
            id="finish-proof-text"
            className={styles.proofText}
            value={words}
            onChange={(event) => setWords(event.target.value)}
            maxLength={100}
            rows={2}
            spellCheck={false}
            aria-describedby="finish-disclosure"
            placeholder="A little more you."
          />
        </div>

        <div
          className={styles.controls}
          role="group"
          aria-label="Style controls"
        >
          <RangeControl
            kind="weight"
            name="ink-weight"
            label="Ink weight"
            value={settings.weight}
            min={400}
            max={700}
            output={String(settings.weight)}
            valueText={`${settings.weight} font weight`}
            onChange={(value) => update("weight", value)}
          />
          <RangeControl
            kind="spacing"
            name="letter-spacing"
            label="Letter spacing"
            value={settings.spacing}
            min={-1}
            max={3}
            step={0.1}
            output={`${settings.spacing.toFixed(1)} px`}
            valueText={`${settings.spacing.toFixed(1)} pixels`}
            onChange={(value) => update("spacing", value)}
          />
          <RangeControl
            kind="slant"
            name="slant"
            label="Slant"
            value={settings.slant}
            min={-15}
            max={15}
            output={`${settings.slant}°`}
            valueText={`${settings.slant} degrees`}
            onChange={(value) => update("slant", value)}
          />
        </div>

        <div className={styles.options}>
          <div className={styles.choices}>
            <fieldset className={styles.choiceControl}>
              <legend>Ink</legend>
              <div className={styles.swatches}>
                {inks.map((ink) => (
                  <button
                    type="button"
                    key={ink.value}
                    className={styles.swatch}
                    style={{ "--swatch": ink.value } as CSSProperties}
                    aria-label={`${ink.label} ink`}
                    aria-pressed={settings.ink === ink.value}
                    title={ink.label}
                    onClick={() => update("ink", ink.value)}
                  >
                    {settings.ink === ink.value && (
                      <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                        <path
                          d="m4 8 2.5 2.5L12 5"
                          stroke="currentColor"
                          strokeWidth="1.6"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset className={styles.choiceControl}>
              <legend>Paper</legend>
              <div className={styles.paperChoices}>
                <button
                  type="button"
                  aria-label="Cream paper"
                  aria-pressed={settings.paper === "cotton"}
                  onClick={() => update("paper", "cotton")}
                >
                  <PaperThumbnail
                    paper="cotton"
                    format="note"
                    className={styles.paperThumbnail}
                  />
                  <span>Cream</span>
                </button>
                <button
                  type="button"
                  aria-label="Graph paper"
                  aria-pressed={settings.paper === "graph"}
                  onClick={() => update("paper", "graph")}
                >
                  <PaperThumbnail
                    paper="graph"
                    format="note"
                    className={styles.paperThumbnail}
                  />
                  <span>Graph</span>
                </button>
              </div>
            </fieldset>
          </div>

          <details className={styles.more}>
            <summary>
              More controls
              <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="m4 6 4 4 4-4"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </summary>
            <div className={styles.moreControls}>
              <RangeControl
                kind="size"
                name="font-size"
                label="Size"
                value={settings.fontSize}
                min={24}
                max={72}
                output={`${settings.fontSize} px`}
                valueText={`${settings.fontSize} pixels`}
                onChange={(value) => update("fontSize", value)}
              />
              <RangeControl
                kind="lineHeight"
                name="line-spacing"
                label="Line spacing"
                value={settings.lineHeight}
                min={1}
                max={1.8}
                step={0.05}
                output={`${settings.lineHeight.toFixed(2)}×`}
                valueText={`${settings.lineHeight.toFixed(2)} line height`}
                onChange={(value) => update("lineHeight", value)}
              />
            </div>
          </details>
        </div>

        <div className={styles.editorFooter}>
          <button
            className={styles.reset}
            type="button"
            disabled={isDefault}
            onClick={() => setSettings({ ...defaultStyle })}
            aria-label="Reset style controls; keep your words"
          >
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M4 7a6 6 0 1 1-.1 5M4 3v4h4"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Reset
          </button>
          <Link href={writeHref} prefetch={false} className={styles.writeLink}>
            Write with this style
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M5 15 15 5M5 5h10v10"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </Link>
        </div>
      </div>
      <p id="finish-disclosure" className={styles.disclosure}>
        Style preview · sample typeface.
      </p>
    </div>
  );
}
