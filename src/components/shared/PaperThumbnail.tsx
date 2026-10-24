import styles from "./PaperThumbnail.module.css";

type Paper = "cotton" | "ruled" | "graph" | "kraft";
type Format = "letter" | "note" | "card";

/** The same proportions and paper treatments used by the writing preview. */
export default function PaperThumbnail({
  paper,
  format = "note",
  accent = "#d7836f",
  className = "",
}: {
  paper: Paper;
  format?: Format;
  accent?: string;
  className?: string;
}) {
  const [width, height] =
    format === "letter" ? [60, 80] : format === "card" ? [64, 42] : [60, 58];
  const inset = format === "card" ? 4 : 1;
  const right = width - inset;
  const bottom = height - inset;
  return (
    <span className={`${styles.thumbnail} ${className}`} aria-hidden="true">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className={`${styles.sheet} ${styles[paper]}`}
        style={{ color: /^#[\da-f]{6}$/i.test(accent) ? accent : "#d7836f" }}
      >
        {format === "card" && (
          <rect
            x="1"
            y="1"
            width={width - 2}
            height={height - 2}
            rx="2"
            className={styles.accentPanel}
          />
        )}
        <rect
          x={inset}
          y={inset}
          width={width - inset * 2}
          height={height - inset * 2}
          rx="2"
          className={styles.page}
        />
        <g className={styles.fibers}>
          <path d="m8 8 1 .8m29 4 .7-1m12 18-1 .8M13 34l1-1M42 32l1 1" />
        </g>
        {(paper === "ruled" || paper === "graph") && (
          <g className={styles.rules}>
            {Array.from(
              { length: Math.floor((bottom - inset - 6) / 7) + 1 },
              (_, i) => (
                <path
                  key={`h${i}`}
                  d={`M${inset + 2} ${inset + 6 + i * 7}H${right - 2}`}
                />
              ),
            )}
            {paper === "graph" &&
              Array.from(
                { length: Math.floor((right - inset - 6) / 7) + 1 },
                (_, i) => (
                  <path
                    key={`v${i}`}
                    d={`M${inset + 6 + i * 7} ${inset + 2}V${bottom - 2}`}
                  />
                ),
              )}
            {paper === "ruled" && (
              <path
                d={`M${inset + 9} ${inset + 2}V${bottom - 2}`}
                className={styles.margin}
              />
            )}
          </g>
        )}
        {format === "letter" && (
          <>
            <g className={styles.headerDetail}>
              <path d="M8 10H24m12 0H52M8 73H52" />
              <circle cx="30" cy="10" r="3.5" />
              <path d="m28 12 2-5 2 5m-3-1h2" />
            </g>
            <g className={`${styles.writing} ${styles.letterWriting}`}>
              <text x="9" y="28">
                a little
              </text>
              <text x="9" y="40">
                more you.
              </text>
            </g>
            <path
              d="M9 49h34m-34 5h29m-29 8h12m-12 5h21"
              className={styles.signature}
            />
          </>
        )}
        {format === "note" && (
          <>
            <rect
              x="1"
              y="1"
              width={width - 2}
              height="5"
              className={styles.memoBand}
            />
            <path
              d={`M${width - 11} 1V11H${width - 1}Z`}
              className={styles.fold}
            />
            <path
              d={`m${width - 11} 1 10 10V1Z`}
              className={styles.foldShade}
            />
            <g className={styles.writing}>
              <text x="8" y="23" className={styles.noteTitle}>
                a little
              </text>
              <text x="8" y="36" className={styles.noteMessage}>
                more you.
              </text>
            </g>
            <path d="M8 44h24m-24 5h17" className={styles.signature} />
          </>
        )}
        {format === "card" && (
          <>
            <rect
              x="6"
              y="6"
              width={width - 12}
              height={height - 12}
              rx=".5"
              className={styles.cardBorder}
            />
            <g className={styles.cardOrnament}>
              <path d="M9 14Q9 9 14 9M50 33q5 0 5-5M5 7V35" />
            </g>
            <g className={styles.writing} textAnchor="middle">
              <text x={width / 2} y="21" className={styles.cardTitle}>
                for you.
              </text>
              <text x={width / 2} y="30" className={styles.cardMessage}>
                more you.
              </text>
            </g>
          </>
        )}
      </svg>
    </span>
  );
}
