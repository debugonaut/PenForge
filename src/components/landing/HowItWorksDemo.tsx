"use client";

import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { useMotionPreference } from "@/components/shared/MotionPreference";
import styles from "./HowItWorksDemo.module.css";

type GVariant = "looped" | "open";

const glyphPaths = {
  looped:
    "M45 42C35 31 17 37 12 52C6 69 16 79 28 71C37 64 43 48 45 39L35 86C31 105 13 108 10 95C8 86 22 81 38 78",
  open: "M45 42C35 31 17 37 12 52C6 69 16 79 28 71C37 64 43 48 45 39L36 84C33 99 20 106 11 103",
};

function GlyphG({ variant }: { variant: GVariant }) {
  return <path d={glyphPaths[variant]} />;
}

function GoodStrokes({ variant }: { variant: GVariant }) {
  return (
    <>
      <GlyphG variant={variant} />
      <path d="M62 63C62 48 76 39 85 47C97 60 84 78 71 74C58 70 61 52 72 47C79 44 85 47 88 52M96 63C96 48 110 39 119 47C131 60 118 78 105 74C92 70 95 52 106 47C113 44 119 47 122 52M151 47C139 38 128 51 128 63C129 79 146 76 154 62L165 23C159 35 150 55 151 68C151 78 159 77 166 71" />
    </>
  );
}

function GoodWord({
  variant,
  raw = false,
  highlight = false,
}: {
  variant: GVariant;
  raw?: boolean;
  highlight?: boolean;
}) {
  return (
    <svg viewBox="0 0 178 110" fill="none" aria-hidden="true">
      {raw && (
        <g
          stroke="#817565"
          strokeWidth="1"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity=".22"
          transform="translate(.7 -.45)"
        >
          <GoodStrokes variant={variant} />
        </g>
      )}
      <g
        stroke={raw ? "#716a5d" : "#192e35"}
        strokeWidth={raw ? "1.5" : "2.7"}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={raw ? ".8" : "1"}
      >
        <GoodStrokes variant={variant} />
      </g>
      {highlight && (
        <ellipse
          cx="28"
          cy="55"
          rx="23"
          ry="24"
          stroke="#c36b54"
          strokeWidth="1.25"
          strokeDasharray="3 3"
        />
      )}
    </svg>
  );
}

function Phrase({ variant }: { variant: GVariant }) {
  return (
    <svg
      viewBox="0 0 278 90"
      role="img"
      aria-label={`good things. — ${variant === "looped" ? "looped" : "open-tail"} g`}
    >
      <g
        transform="translate(5 0) scale(.78)"
        fill="none"
        stroke="#192e35"
        strokeWidth="2.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <GoodStrokes variant={variant} />
      </g>
      <text x="148" y="56" className={styles.phraseText}>
        thin
      </text>
      <g
        transform="translate(210 11.4) scale(.62)"
        fill="none"
        stroke="#192e35"
        strokeWidth="3.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <GlyphG variant={variant} />
      </g>
      <text x="241" y="56" className={styles.phraseText}>
        s.
      </text>
    </svg>
  );
}

function StepIcon({ name }: { name: "write" | "refine" | "type" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {name === "write" ? (
        <>
          <path d="m4 16-1 5 5-1L21 7l-4-4ZM14 6l4 4M4 16l4 4" />
        </>
      ) : name === "refine" ? (
        <>
          <rect x="4" y="4" width="16" height="16" rx="2" />
          <path d="M9 4v16M15 4v16M4 9h16M4 15h16" />
        </>
      ) : (
        <>
          <rect x="2" y="5" width="20" height="14" rx="2" />
          <path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M10 13h.01M14 13h.01M18 13h.01M8 16h8" />
        </>
      )}
    </svg>
  );
}

/** An authored visual sample. Choices change the actual g paths, not a generated font. */
export default function HowItWorksDemo() {
  const phrase = useRef<HTMLDivElement>(null);
  const { reduced } = useMotionPreference();
  const [selected, setSelected] = useState("g");
  const [variant, setVariant] = useState<GVariant>("looped");
  const [reviewed, setReviewed] = useState(false);
  const [comparison, setComparison] = useState(50);
  const [status, setStatus] = useState("Try the two g shapes.");

  useEffect(() => {
    const scope = phrase.current;
    const sample = scope?.querySelector("svg");
    if (!scope || !sample) return;
    const context = gsap.context(() => {
      gsap.fromTo(
        sample,
        { opacity: 0.7, scale: reduced ? 1 : 0.975 },
        {
          opacity: 1,
          scale: 1,
          duration: reduced ? 0.18 : 0.28,
          ease: "power3.out",
        },
      );
    }, scope);
    return () => context.revert();
  }, [variant, reviewed, reduced]);

  function chooseLetter(letter: string) {
    setSelected(letter);
    setStatus(
      letter === "g"
        ? "Choose the g you would keep."
        : `${letter} looks ready. Try the marked g.`,
    );
  }

  function chooseShape(shape: GVariant) {
    setVariant(shape);
    setReviewed(true);
    setStatus(`${shape === "looped" ? "Looped" : "Open-tail"} g selected.`);
  }

  return (
    <div className={styles.grid}>
      <article className={`${styles.card} ${styles.writeCard}`}>
        <div className={styles.heading}>
          <span>01</span>
          <StepIcon name="write" />
        </div>
        <div className={styles.art}>
          <div className={`${styles.sourcePaper} forge-source-paper`}>
            <span>YOUR HANDWRITING</span>
            <p>Aa Bb Cc</p>
            <div className={styles.sourceWord}>
              <GoodWord variant="looped" raw />
            </div>
            <svg
              className={`${styles.processHand} forge-process-hand`}
              viewBox="0 0 1536 1024"
              aria-hidden="true"
            >
              <image
                href="/illustrations/writing-hand-v2.webp"
                width="1536"
                height="1024"
              />
            </svg>
          </div>
        </div>
        <h3>Write.</h3>
        <p>Draw, scan, or photograph.</p>
        <span className={styles.arrow} aria-hidden="true">
          →
        </span>
      </article>
      <article className={`${styles.card} ${styles.refineCard}`}>
        <div className={styles.heading}>
          <span>02</span>
          <StepIcon name="refine" />
        </div>
        <div className={`${styles.art} ${styles.refineArt}`}>
          <div
            className={styles.glyphGrid}
            role="group"
            aria-label="Select a sample letter"
          >
            {["a", "b", "c", "g", "h", "m"].map((letter) => (
              <button
                type="button"
                key={letter}
                className={`${styles.glyph} forge-glyph ${letter === "g" && !reviewed ? styles.uncertain : ""}`}
                aria-label={`Select letter ${letter}${letter === "g" && !reviewed ? ", sample needs review" : ""}`}
                aria-pressed={selected === letter}
                onClick={() => chooseLetter(letter)}
              >
                {letter === "g" ? (
                  <svg
                    viewBox="0 0 60 110"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <GlyphG variant={variant} />
                  </svg>
                ) : (
                  letter
                )}
                {letter === "g" && (
                  <i
                    key={reviewed ? "approved" : "uncertain"}
                    aria-hidden="true"
                  >
                    {reviewed ? "✓" : "?"}
                  </i>
                )}
              </button>
            ))}
          </div>
          <div className={styles.choiceArea}>
            {selected === "g" ? (
              <div
                className={styles.choices}
                role="group"
                aria-label="Choose a sample g shape"
              >
                {(
                  [
                    { id: "looped", name: "Looped" },
                    { id: "open", name: "Open tail" },
                  ] as const
                ).map((shape) => (
                  <button
                    type="button"
                    key={shape.id}
                    aria-pressed={variant === shape.id}
                    onClick={() => chooseShape(shape.id)}
                  >
                    <svg
                      viewBox="0 0 60 110"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="4.2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <GlyphG variant={shape.id} />
                    </svg>
                    {shape.name}
                  </button>
                ))}
              </div>
            ) : (
              <span className={styles.readyLetter}>
                {selected} <span>looks ready</span>
              </span>
            )}
            <p className={styles.status} role="status" aria-live="polite">
              {status}
            </p>
          </div>
        </div>
        <h3>Refine.</h3>
        <p>Choose the curves you keep.</p>
        <span className={styles.arrow} aria-hidden="true">
          →
        </span>
      </article>
      <article className={`${styles.card} ${styles.typeCard}`}>
        <div className={styles.heading}>
          <span>03</span>
          <StepIcon name="type" />
        </div>
        <div className={`${styles.art} ${styles.typeArt}`}>
          <div className={styles.browser}>
            <div className={styles.browserBar}>
              <span aria-hidden="true">•••</span>
              <span>ILLUSTRATED SAMPLE</span>
            </div>
            <div
              ref={phrase}
              className={`${styles.phrase} forge-composed-word`}
            >
              <Phrase variant={variant} />
            </div>
            <div className={styles.comparison}>
              <div className={styles.comparisonSurface}>
                <div className={styles.cleanSample}>
                  <GoodWord variant={variant} highlight />
                </div>
                <div
                  className={styles.rawSample}
                  style={{ clipPath: `inset(0 ${100 - comparison}% 0 0)` }}
                >
                  <GoodWord variant={variant} raw highlight />
                </div>
                <span
                  className={styles.comparisonDivider}
                  style={{ left: `${comparison}%` }}
                  aria-hidden="true"
                />
                <span className={styles.beforeLabel}>On paper</span>
                <span className={styles.afterLabel}>Typed</span>
              </div>
              <label className={styles.compareControl}>
                <span>Same loop</span>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={comparison}
                  aria-label="Sample comparison"
                  aria-valuetext={`${comparison}% paper, ${100 - comparison}% typed`}
                  onChange={(event) =>
                    setComparison(Number(event.target.value))
                  }
                />
              </label>
            </div>
          </div>
        </div>
        <h3>Type.</h3>
        <p>Same loops. A new place to write.</p>
      </article>
    </div>
  );
}
