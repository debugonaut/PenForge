import type {
  CreationStep,
  FontProject,
  Glyph,
  ProjectChanges,
} from "@/lib/contracts";

export type CreationPanelProps = {
  project: FontProject;
  update: (change: ProjectChanges) => void;
  go: (step: CreationStep) => void;
};

export type AnnouncedPanelProps = CreationPanelProps & {
  announce: (message: string) => void;
};

export function hasSample(project: FontProject) {
  return (
    project.sample?.kind === "demo" ||
    (project.sample?.kind === "draw" && !!project.sample.strokes?.length) ||
    (project.sample?.kind === "upload" && !!project.sample.dataUrl)
  );
}

export function availableStep(
  project: FontProject,
  requested: CreationStep,
): CreationStep {
  if (requested === "capture") return requested;
  if (!project.glyphs.length && !hasSample(project)) return "capture";
  if (requested !== "review" && !project.glyphs.length) return "review";
  return requested;
}

export type GlyphHistoryStore = {
  current: Record<string, { past: Glyph[]; future: Glyph[] }>;
};
