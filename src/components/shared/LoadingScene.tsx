import { PenCharacter } from "@/components/brand/Characters";

type LoadingSceneProps = {
  /** Short on-topic status announced politely to assistive technology. */
  message?: string;
  /** Compact form for loading inside an existing page instead of a full route. */
  inline?: boolean;
};

/**
 * The one loading treatment for Penforge: cream paper, an ink-outlined sheet
 * writing a stroke, and the pen character waiting alongside. Used by route
 * fallbacks and by the states that wait for local project data.
 */
export function LoadingScene({
  message = "Opening your creative desk…",
  inline = false,
}: LoadingSceneProps) {
  return (
    <div
      className={`loading-scene${inline ? " loading-scene-inline" : ""}`}
      role="status"
    >
      <span className="loading-blob loading-blob-coral" aria-hidden="true" />
      <span className="loading-blob loading-blob-yellow" aria-hidden="true" />
      <div className="loading-core">
        <span className="loading-pen" aria-hidden="true">
          <PenCharacter mood="happy" />
        </span>
        <div className="loading-sheet" aria-hidden="true">
          <svg viewBox="0 0 480 200" role="presentation">
            <path
              className="loading-rules"
              d="M26 58 H454 M26 118 H454 M26 178 H454 M82 14 V192"
            />
            <path
              className="loading-stroke"
              pathLength={1}
              d="M100 150 C 118 92 152 84 156 116 C 160 148 128 158 130 122 C 132 84 182 66 214 92 C 246 118 216 156 206 128 C 196 100 246 62 288 82 C 322 98 302 150 286 132 C 270 114 318 70 356 84 C 392 98 380 140 400 128 C 420 116 438 100 452 92"
            />
          </svg>
        </div>
        <span className="loading-eyebrow">PENFORGE · LOADING</span>
        <p className="loading-message">{message}</p>
      </div>
    </div>
  );
}
