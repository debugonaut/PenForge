import type { ReactNode } from "react";
import { PenCharacter, QuillCharacter } from "@/components/brand/Characters";

type Tone = "mint" | "yellow" | "coral" | "tan";

export function StepStage({
  tone,
  number,
  children,
  className = "",
}: {
  tone: Tone;
  number: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`step-stage step-stage-${tone} ${className}`.trim()}
      aria-label={`Step ${number} workspace`}
    >
      <span className="stage-blob stage-blob-coral" aria-hidden="true" />
      <span className="stage-blob stage-blob-mint" aria-hidden="true" />
      <span className="stage-blob stage-blob-yellow" aria-hidden="true" />
      <svg
        className="stage-squiggle"
        viewBox="0 0 1200 200"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path d="M0 138 C 140 54, 262 196, 420 122 S 690 44, 840 132 S 1080 196, 1200 96" />
      </svg>
      <span className="stage-number" aria-hidden="true">
        {number}
      </span>
      <div className="stage-inner">{children}</div>
    </section>
  );
}

export function StepMascot({ character }: { character: "pen" | "quill" }) {
  return (
    <span
      className={`step-mascot step-mascot-${character}`}
      aria-hidden="true"
    >
      <span className="step-mascot-blob" />
      {character === "pen" ? (
        <PenCharacter mood="happy" />
      ) : (
        <QuillCharacter mood="surprised" />
      )}
    </span>
  );
}
