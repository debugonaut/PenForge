import type { Glyph } from "@/lib/contracts";

export function GlyphShape({ glyph }: { glyph: Glyph }) {
  return (
    <div
      className="glyph-shape"
      style={{
        transform: `rotate(${glyph.rotation}deg) scale(${glyph.scale / 100})`,
        clipPath: `inset(${glyph.crop}% round 4px)`,
      }}
    >
      {glyph.strokes.length ? (
        <svg
          viewBox="0 0 100 100"
          aria-label={`Edited drawing for ${glyph.character}`}
        >
          <g
            fill="none"
            stroke="currentColor"
            strokeWidth="2.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {glyph.strokes.map((stroke, index) =>
              stroke.length === 1 ? (
                <circle
                  key={index}
                  cx={stroke[0].x * 100}
                  cy={stroke[0].y * 100}
                  r="1.35"
                  fill="currentColor"
                  stroke="none"
                />
              ) : (
                <polyline
                  key={index}
                  points={stroke
                    .map((point) => `${point.x * 100},${point.y * 100}`)
                    .join(" ")}
                />
              ),
            )}
          </g>
        </svg>
      ) : (
        glyph.character
      )}
    </div>
  );
}
