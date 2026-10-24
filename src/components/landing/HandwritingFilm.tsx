"use client";

import { useEffect, useId, useRef, useState } from "react";
import gsap from "gsap";
import styles from "./HandwritingFilm.module.css";

const phases = ["Write", "Capture", "Digitize", "Type"];

function ReplayIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path
        d="M4 7a6.5 6.5 0 1 1-.2 5M4 3v4h4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** A repeating story, with a gentle animated variant and local transport controls. */
export default function HandwritingFilm({
  active,
  gentle = false,
}: {
  active: boolean;
  gentle?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const timeline = useRef<gsap.core.Timeline | null>(null);
  const fontsReady = useRef(false);
  const activeRef = useRef(active);
  const manuallyPaused = useRef(false);
  const [phase, setPhase] = useState(3);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [transportStatus, setTransportStatus] = useState("");
  const id = useId().replace(/:/g, "");
  const graph = `${id}-graph`;
  const sourceClip = `${id}-source-clip`;
  activeRef.current = active;

  useEffect(() => {
    if (!root.current) return;
    fontsReady.current = false;
    let cancelled = false;
    setPhase(0);
    setReady(false);
    setPlaying(
      activeRef.current && !manuallyPaused.current && !document.hidden,
    );
    const context = gsap.context(() => {
      const q = gsap.utils.selector(root.current);
      const scene = (name: string) => q(`[data-film="${name}"]`);
      const stage = q(`.${styles.canvas} > svg`);
      gsap.set(stage, { opacity: 0 });
      gsap.set(scene("source"), {
        opacity: 1,
        scale: 1,
        x: 0,
        y: 0,
        svgOrigin: "350 220",
      });
      // The hand's local origin is its nib tip: translations are writing contact points.
      gsap.set(scene("hand"), { opacity: gentle ? 0 : 1, x: 147, y: 235 });
      gsap.set(scene("source-reveal"), { attr: { width: 0 } });
      gsap.set(scene("phone"), {
        opacity: 0,
        x: gentle ? -155 : 100,
        y: gentle ? 0 : 35,
        rotation: gentle ? 0 : 7,
        svgOrigin: "505 224",
      });
      gsap.set(scene("browser"), {
        opacity: 0,
        x: gentle ? 0 : 72,
        scale: gentle ? 1 : 0.8,
        svgOrigin: "350 220",
      });
      gsap.set(scene("photo"), {
        opacity: 0,
        x: gentle ? 0 : -218,
        y: gentle ? 0 : 15,
        scale: gentle ? 1 : 0.28,
        svgOrigin: "350 220",
      });
      gsap.set(scene("flash"), { opacity: 0 });
      gsap.set(scene("scan"), { opacity: 0, y: gentle ? 0 : -68 });
      gsap.set(scene("guides"), { opacity: 0 });
      gsap.set(scene("paper-grid"), { opacity: 1 });
      gsap.set(scene("typed-letter"), { opacity: 0 });
      gsap.set(scene("note-details"), { opacity: 0, y: gentle ? 0 : 8 });
      gsap.set(scene("caret"), { opacity: 0, x: 0 });
      gsap.set(scene("result-stamp"), { opacity: 0, y: gentle ? 0 : 6 });

      const mark = (index: number) => () => setPhase(index);
      const film = gsap.timeline({
        paused: true,
        repeat: -1,
        defaults: { ease: "power2.out" },
        onStart: () => {
          setPlaying(true);
        },
        onRepeat: mark(0),
      });
      film
        .call(mark(0), [], 0)
        .fromTo(
          stage,
          { opacity: 0 },
          { opacity: 1, duration: gentle ? 0.35 : 0.25 },
          0,
        )
        .to(
          scene("hand"),
          {
            opacity: 0,
            x: gentle ? 147 : 555,
            y: gentle ? 235 : 190,
            duration: 0.25,
            ease: "power2.in",
          },
          2.27,
        )
        .call(mark(1), [], 2.2)
        .to(
          scene("source"),
          gentle
            ? { opacity: 0, duration: 0.3 }
            : {
                scale: 0.67,
                x: -114,
                y: 2,
                duration: 0.8,
                ease: "power2.inOut",
              },
          2.25,
        )
        .to(
          scene("phone"),
          {
            opacity: 1,
            x: gentle ? -155 : 0,
            y: 0,
            rotation: 0,
            duration: gentle ? 0.3 : 0.65,
          },
          2.55,
        )
        .to(
          scene("flash"),
          {
            opacity: gentle ? 0.16 : 0.65,
            duration: gentle ? 0.2 : 0.12,
            ease: "power2.out",
          },
          3.6,
        )
        .to(scene("flash"), { opacity: 0, duration: 0.25 }, gentle ? 3.8 : 3.72)
        .to(
          scene("phone"),
          gentle
            ? { opacity: 0, duration: 0.3 }
            : { x: -365, scale: 0.9, duration: 0.7, svgOrigin: "505 224" },
          4.08,
        )
        .to(
          scene("source"),
          {
            opacity: 0,
            scale: gentle ? 1 : 0.6,
            x: gentle ? 0 : -145,
            duration: 0.3,
          },
          4.1,
        )
        .to(
          scene("browser"),
          {
            opacity: 1,
            x: gentle ? 0 : 68,
            scale: gentle ? 1 : 0.85,
            duration: gentle ? 0.3 : 0.6,
          },
          4.25,
        )
        .set(scene("photo"), { opacity: 1 }, 4.82)
        .to(
          scene("photo"),
          {
            x: gentle ? 0 : 68,
            y: 0,
            scale: gentle ? 1 : 0.85,
            duration: gentle ? 0.3 : 0.75,
            ease: "power2.inOut",
          },
          4.82,
        )
        .call(mark(2), [], 5.55)
        .to(
          scene("phone"),
          { opacity: 0, x: gentle ? -155 : -400, duration: 0.3 },
          5.7,
        )
        .to(
          scene("browser"),
          { x: 0, scale: 1, duration: gentle ? 0.3 : 0.55 },
          5.85,
        )
        .to(
          scene("photo"),
          { x: 0, scale: 1, duration: gentle ? 0.3 : 0.55 },
          5.85,
        )
        .to(scene("scan"), { opacity: 1, duration: 0.15 }, 6.2)
        .to(
          scene("scan"),
          { y: gentle ? 0 : 74, duration: 0.7, ease: "sine.inOut" },
          6.2,
        )
        .to(scene("guides"), { opacity: 0.75, duration: 0.25 }, 6.4)
        .to(scene("paper-grid"), { opacity: 0, duration: 0.55 }, 6.55)
        .to(scene("scan"), { opacity: 0, duration: 0.15 }, 6.92)
        .to(scene("guides"), { opacity: 0, duration: 0.3 }, 7.02)
        .to(scene("photo"), { opacity: 0, duration: 0.3 }, 7.15)
        .call(mark(3), [], 7.45)
        .to(scene("caret"), { opacity: 0, duration: 0.15 }, 9.08)
        .to(scene("note-details"), { opacity: 1, y: 0, duration: 0.32 }, 8.95)
        .to(scene("result-stamp"), { opacity: 1, y: 0, duration: 0.35 }, 9.3)
        .to({}, { duration: 3 }, 9.65)
        .to(
          stage,
          { opacity: 0, duration: gentle ? 0.35 : 0.25, ease: "power2.in" },
          12.65,
        );
      const typedText = root.current?.querySelector<SVGTextElement>(
        '[data-film="typed-text"]',
      );
      scene("typed-letter").forEach((letter, index) => {
        const at = 7.5 + index * 0.109;
        if (index === 0) film.set(scene("caret"), { opacity: 1 }, at);
        film.set(letter, { opacity: 1 }, at);
        film.set(
          scene("caret"),
          {
            x: () => {
              if (!typedText) return 0;
              const character = letter.textContent ?? "";
              const bounds = typedText.getExtentOfChar(index);
              const rightEdge =
                character === " "
                  ? typedText.getEndPositionOfChar(index).x
                  : bounds.x + bounds.width;
              return rightEdge - 150 + 3;
            },
          },
          at,
        );
      });
      timeline.current = film;
      // Start after both the handwriting font and the transparent hand have decoded.
      // Each complete tspan appears instantly; the caret follows visible glyph bounds,
      // not the extra advance space that can make Caveat look like it skipped letters.
      const handImage = new window.Image();
      handImage.src = "/illustrations/writing-hand-v2.webp";
      void Promise.all([
        document.fonts.ready,
        handImage.decode().catch(() => undefined),
      ]).then(() => {
        if (cancelled) return;
        fontsReady.current = true;
        setReady(true);
        const sourceText = root.current?.querySelector<SVGTextElement>(
          '[data-film="source-text"]',
        );
        if (sourceText) {
          const phrase = "hello, world.";
          const letterDuration = 2.05 / phrase.length;
          Array.from(phrase).forEach((letter, index) => {
            const start = sourceText.getStartPositionOfChar(index);
            const end = sourceText.getEndPositionOfChar(index);
            const extent = sourceText.getExtentOfChar(index);
            const width = Math.max(2, end.x - start.x);
            const maximumLift = "hld".includes(letter) ? 12 : 6;
            const lift =
              letter === " "
                ? 6
                : letter === ","
                  ? 3
                  : letter === "."
                    ? 2
                    : Math.min(maximumLift, Math.max(5, 235 - extent.y - 9));
            const at = 0.2 + index * letterDuration;
            if (!gentle)
              film.to(
                scene("hand"),
                {
                  keyframes: [
                    {
                      x: start.x + width * 0.26,
                      y: 235 - lift * 0.35,
                      duration: letterDuration * 0.22,
                    },
                    {
                      x: start.x + width * 0.53,
                      y: 235 - lift,
                      duration: letterDuration * 0.32,
                    },
                    {
                      x: end.x,
                      y: letter === "," ? 242 : 235,
                      duration: letterDuration * 0.46,
                    },
                  ],
                  ease: "sine.inOut",
                },
                at,
              );
            film.to(
              scene("source-reveal"),
              {
                attr: { width: end.x - 146 + 2 },
                duration: letterDuration,
                ease: "none",
              },
              at,
            );
          });
        }
        if (activeRef.current && !manuallyPaused.current && !document.hidden)
          film.play();
      });
    }, root);
    return () => {
      cancelled = true;
      timeline.current = null;
      context.revert();
    };
  }, [gentle]);

  useEffect(() => {
    const film = timeline.current;
    if (!film) return;
    function syncPlayback() {
      if (!film) return;
      if (
        !active ||
        document.hidden ||
        manuallyPaused.current ||
        !fontsReady.current
      ) {
        film.pause();
        setPlaying(false);
      } else {
        setPlaying(true);
        film.play();
      }
    }
    syncPlayback();
    document.addEventListener("visibilitychange", syncPlayback);
    return () => document.removeEventListener("visibilitychange", syncPlayback);
  }, [active, gentle]);

  function replay() {
    if (!timeline.current || !fontsReady.current) return;
    manuallyPaused.current = false;
    setPhase(0);
    const canPlay = activeRef.current && !document.hidden;
    setPlaying(canPlay);
    setTransportStatus("Animation restarted.");
    timeline.current.restart();
    if (!canPlay) timeline.current.pause();
  }

  function togglePlayback() {
    const film = timeline.current;
    if (!film || !fontsReady.current) return;
    if (film.paused()) {
      manuallyPaused.current = false;
      const canPlay = activeRef.current && !document.hidden;
      setPlaying(canPlay);
      setTransportStatus("Animation resumed.");
      if (canPlay) film.play();
    } else {
      manuallyPaused.current = true;
      setPlaying(false);
      setTransportStatus("Animation paused.");
      film.pause();
    }
  }

  return (
    <div ref={root} className={styles.film}>
      <div className={styles.canvas}>
        <svg
          viewBox="35 25 630 385"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Your handwriting, from paper to the page."
          aria-describedby={`${id}-description`}
        >
          <desc id={`${id}-description`}>
            A pen writes hello, world. A phone photographs the page, the image
            moves into a digital workspace, and the same lettering becomes a
            typed note. This is an illustrated concept preview.
          </desc>
          <defs>
            <pattern
              id={graph}
              width="22"
              height="22"
              patternUnits="userSpaceOnUse"
            >
              <path
                d="M22 0H0V22"
                fill="none"
                stroke="#86a697"
                strokeWidth="0.8"
                opacity="0.42"
              />
            </pattern>
            <clipPath id={sourceClip}>
              <rect
                data-film="source-reveal"
                x="146"
                y="150"
                width="390"
                height="110"
              />
            </clipPath>
          </defs>

          <g data-film="source" className={styles.temporary}>
            <rect
              x="80"
              y="53"
              width="548"
              height="334"
              rx="3"
              fill="#192e35"
              opacity="0.07"
            />
            <rect
              x="73"
              y="45"
              width="548"
              height="334"
              rx="3"
              fill="#fffcf3"
              stroke="#192e35"
              strokeWidth="1.8"
            />
            <rect
              x="92"
              y="64"
              width="510"
              height="296"
              fill={`url(#${graph})`}
            />
            <text x="112" y="95" className={styles.microLabel}>
              A LITTLE OF YOUR HANDWRITING
            </text>
            <g clipPath={`url(#${sourceClip})`}>
              <text
                data-film="source-text"
                x="147"
                y="235"
                textLength="355"
                lengthAdjust="spacing"
                className={styles.handwriting}
              >
                hello, world.
              </text>
            </g>
            <path
              d="M112 316H213"
              stroke="#192e35"
              strokeWidth="1"
              opacity="0.2"
            />
          </g>

          <g data-film="hand" className={styles.temporary}>
            {/* PNG nib pixel (105, 469), at 0.22 scale, lands at local (0, 0). */}
            <image
              href="/illustrations/writing-hand-v2.webp"
              x="-23.1"
              y="-103.18"
              width="337.92"
              height="225.28"
              preserveAspectRatio="xMidYMid meet"
              aria-hidden="true"
            />
          </g>

          <g data-film="phone" className={styles.temporary}>
            <rect
              x="424"
              y="65"
              width="174"
              height="316"
              rx="26"
              fill="#192e35"
              opacity="0.1"
            />
            <rect
              x="418"
              y="58"
              width="174"
              height="316"
              rx="26"
              fill="#192e35"
              stroke="#192e35"
              strokeWidth="2"
            />
            <rect
              x="427"
              y="88"
              width="156"
              height="227"
              rx="5"
              fill="#f8f1e3"
            />
            <rect x="444" y="105" width="122" height="193" fill="#fffcf3" />
            <rect
              x="444"
              y="105"
              width="122"
              height="193"
              fill={`url(#${graph})`}
            />
            <rect
              x="480"
              y="69"
              width="42"
              height="5"
              rx="2.5"
              fill="#87a39e"
            />
            <circle cx="534" cy="72" r="3" fill="#87a39e" />
            <text
              x="451"
              y="206"
              textLength="114"
              lengthAdjust="spacing"
              className={styles.phoneHandwriting}
            >
              hello, world.
            </text>
            <path
              d="M438 129v-14h14m106 0h14v14m0 144v14h-14m-106 0h-14v-14"
              fill="none"
              stroke="#d88975"
              strokeWidth="2.5"
              strokeLinecap="round"
            />
            <circle
              cx="505"
              cy="343"
              r="16"
              fill="none"
              stroke="#fffcf3"
              strokeWidth="2"
            />
            <circle cx="505" cy="343" r="11" fill="#fffcf3" />
            <rect
              data-film="flash"
              x="427"
              y="88"
              width="156"
              height="227"
              rx="5"
              fill="#fffef7"
              opacity="0"
            />
          </g>

          <g data-film="browser">
            <rect
              x="96"
              y="64"
              width="520"
              height="328"
              rx="12"
              fill="#192e35"
              opacity="0.08"
            />
            <rect
              x="89"
              y="56"
              width="520"
              height="328"
              rx="12"
              fill="#fffcf5"
              stroke="#192e35"
              strokeWidth="1.8"
            />
            <path d="M89 100h520" stroke="#192e35" strokeWidth="1.4" />
            <circle cx="112" cy="78" r="3.5" fill="#f0a18c" />
            <circle cx="126" cy="78" r="3.5" fill="#f4d16e" />
            <circle cx="140" cy="78" r="3.5" fill="#a3c9b4" />
            <text x="166" y="82" className={styles.windowLabel}>
              My handwriting
            </text>
            <path
              d="M568 76v8m-4-4h8"
              stroke="#192e35"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
            <text x="150" y="144" className={styles.microLabel}>
              A NOTE, IN YOUR OWN HAND
            </text>
            <text
              data-film="typed-text"
              xmlSpace="preserve"
              x="150"
              y="235"
              className={styles.handwriting}
            >
              {Array.from("hello, world.").map((letter, index) => (
                <tspan data-film="typed-letter" key={index}>
                  {letter}
                </tspan>
              ))}
            </text>
            <path
              data-film="caret"
              className={styles.hidden}
              d="M151 174v66"
              stroke="#d88975"
              strokeWidth="2"
            />
            <g data-film="note-details">
              <text x="154" y="278" className={styles.noteText}>
                A little more you.
              </text>
              <path
                d="M152 309h242"
                stroke="#192e35"
                strokeWidth="1"
                opacity="0.16"
              />
              <path
                d="M152 326h177"
                stroke="#192e35"
                strokeWidth="1"
                opacity="0.16"
              />
            </g>
            <g data-film="result-stamp">
              <rect
                x="452"
                y="325"
                width="132"
                height="31"
                rx="15.5"
                fill="#a3c9b4"
              />
              <path
                d="m466 340 4 4 7-8"
                fill="none"
                stroke="#192e35"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <text x="486" y="345" className={styles.stampLabel}>
                Ready to type
              </text>
            </g>
          </g>

          <g data-film="photo" className={styles.temporary}>
            <rect
              x="122"
              y="126"
              width="454"
              height="191"
              rx="3"
              fill="#fffcf3"
              stroke="#192e35"
              strokeWidth="1"
              strokeOpacity="0.12"
            />
            <rect
              data-film="paper-grid"
              x="129"
              y="132"
              width="440"
              height="178"
              fill={`url(#${graph})`}
            />
            <text
              x="150"
              y="235"
              textLength="355"
              lengthAdjust="spacing"
              className={styles.handwriting}
            >
              hello, world.
            </text>
            <g data-film="guides" opacity="0">
              <path
                d="M144 190h389M144 238h389M144 254h389"
                stroke="#d88975"
                strokeWidth="1"
                strokeDasharray="4 5"
              />
              <path
                d="M156 178v75m80-75v75m67-75v75m89-75v75m73-75v75"
                stroke="#d88975"
                strokeWidth="0.8"
                strokeDasharray="3 5"
              />
            </g>
            <g data-film="scan" opacity="0">
              <path d="M138 218h419" stroke="#d88975" strokeWidth="1.7" />
              <circle cx="138" cy="218" r="3" fill="#d88975" />
              <circle cx="557" cy="218" r="3" fill="#d88975" />
            </g>
          </g>
        </svg>
      </div>
      <div className={styles.footer}>
        <p className={styles.phase}>
          <span>Concept demo</span>
          <span aria-hidden="true">·</span>
          {phases[phase]}
        </p>
        <div className={styles.controls}>
          <button
            type="button"
            onClick={togglePlayback}
            disabled={!ready}
            aria-label={
              playing ? "Pause hero animation" : "Resume hero animation"
            }
          >
            {playing ? (
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path
                  d="M7 5v10m6-10v10"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                />
              </svg>
            ) : (
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path d="m7 4 9 6-9 6Z" fill="currentColor" />
              </svg>
            )}
          </button>
          <button
            type="button"
            onClick={replay}
            disabled={!ready}
            aria-label="Replay hero animation"
          >
            <ReplayIcon />
            <span>Replay</span>
          </button>
        </div>
      </div>
      <span className={styles.transportStatus} role="status" aria-live="polite">
        {transportStatus}
      </span>
    </div>
  );
}
