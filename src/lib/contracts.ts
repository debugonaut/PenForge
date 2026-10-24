import type { components } from "../../packages/contracts/src/generated";

export type CreationStep =
  "capture" | "review" | "edit" | "style" | "preview" | "export";
export type GlyphStatus =
  "missing" | "detected" | "needs-review" | "edited" | "approved";
export type JobStatus =
  "queued" | "processing" | "needs-input" | "completed" | "failed";
export type ExportFormat = "ttf" | "otf" | "woff2";
export type Point = { x: number; y: number };
export type Stroke = Point[];
export type ProjectOrigin =
  | { kind: "demo" }
  | { kind: "account"; ownerUid: string; workspaceVersion: number };
export type CaptureReference = {
  sampleId: string;
  assetId: string;
  kind: "draw" | "image";
  createdAt: string;
};
export type ExportReference = {
  exportId: string;
  format: ExportFormat;
  status: "requested" | "processing" | "ready" | "failed";
  sha256?: string;
  createdAt: string;
};
export type Sample = {
  kind: "draw" | "upload" | "demo";
  strokes?: Stroke[];
  strokeDocument?: components["schemas"]["StrokeDocument"];
  captureStartedAtMs?: number;
  originalCaptureId?: string;
  /** Private account asset reference. Never a preview URL or bytes. */
  captureAssetId?: string;
  dataUrl?: string;
  fileName?: string;
};
export type Glyph = {
  id: string;
  character: string;
  status: GlyphStatus;
  revision: number;
  sourceCharacter: string;
  rotation: number;
  scale: number;
  crop: number;
  strokes: Stroke[];
};
export type FontStyle = {
  thickness: number;
  slant: number;
  spacing: number;
  smoothness: number;
  proportion: number;
};
export type FontProject = {
  id: string;
  name: string;
  updatedAt: string;
  step: CreationStep;
  glyphs: Glyph[];
  style: FontStyle;
  sample: Sample | null;
  previewText: string;
  scriptCode: "Latn" | "Deva";
  captures: CaptureReference[];
  exports: ExportReference[];
  assignments: { sampleId: string; codepoints: number[]; updatedAt: string }[];
  snapshots: { id: string; label: string; createdAt: string }[];
  activeSnapshotId: string | null;
  selectedExportId: string | null;
  /** Explicit origin. Demo drafts are never cast into account projects. */
  origin: ProjectOrigin;
};
export type ProjectChanges = Partial<
  Omit<FontProject, "id" | "updatedAt" | "origin">
>;
export type ProjectStorageInfo = {
  state: "ready" | "needs-recovery" | "unsupported" | "unavailable";
  message?: string;
  recoverableProjects: number;
  invalidProjects: number;
  recoveryAvailable: boolean;
};
export type ProcessingJob = {
  id: string;
  kind: "extract" | "style-suggest" | "generate-font";
  status: JobStatus;
  progress?: number;
  message?: string;
  errorCode?: string;
};
export interface ProjectService {
  list(): Promise<FontProject[]>;
  get(id: string): Promise<FontProject>;
  create(name: string): Promise<FontProject>;
  save(project: FontProject): Promise<FontProject>;
  update(id: string, changes: ProjectChanges): Promise<FontProject>;
  remove(id: string): Promise<void>;
  duplicate(id: string): Promise<FontProject>;
  storageInfo(): Promise<ProjectStorageInfo>;
  recoverLocalData(): Promise<FontProject[]>;
  recoveryBackup(): Promise<string | null>;
  resetLocalData(): Promise<void>;
}
export interface HandwritingService {
  submitSample(projectId: string, sample: Sample): Promise<ProcessingJob>;
  extract(projectId: string): Promise<ProcessingJob>;
  reviewGlyph(projectId: string, glyph: Glyph): Promise<Glyph>;
}
export interface FontService {
  suggestStyle(
    projectId: string,
    instruction: string,
  ): Promise<{ job: ProcessingJob; style: FontStyle }>;
  validate(
    projectId: string,
  ): Promise<{ missing: string[]; warnings: string[] }>;
  requestExport(
    projectId: string,
    formats: ExportFormat[],
  ): Promise<ProcessingJob>;
}
export const steps: { id: CreationStep; label: string }[] = [
  { id: "capture", label: "Capture" },
  { id: "review", label: "Review" },
  { id: "edit", label: "Edit" },
  { id: "style", label: "Style" },
  { id: "preview", label: "Preview" },
  { id: "export", label: "Export" },
];
