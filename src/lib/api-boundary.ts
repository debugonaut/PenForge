/** Explicit boundary only. Phase 00 does not switch the UI or upload local drafts. */
import type { components } from "../../packages/contracts/src/generated";
import type { FontProject, Point, Stroke } from "./contracts";

export type ProjectDTO = components["schemas"]["ProjectDTO"];
export type JobDTO = components["schemas"]["JobDTO"];
export type CanonicalOutline = components["schemas"]["GlyphOutline"];
export type CanonicalStrokeDocument = components["schemas"]["StrokeDocument"];

export type CloudProjectView = {
  mode: "cloud";
  id: string;
  name: string;
  updatedAt: string;
  scriptCode: string;
  serverState: ProjectDTO["status"];
  rowVersion: number;
  activeSnapshotId: string | null;
  styleControls: Record<string, number>;
  fontAvailability: "unavailable";
};
export type ProjectView =
  { mode: "demo"; project: FontProject } | CloudProjectView;

export function mapProject(dto: ProjectDTO): CloudProjectView {
  return {
    mode: "cloud",
    id: dto.id,
    name: dto.name,
    updatedAt: dto.updated_at,
    scriptCode: dto.script_code,
    serverState: dto.status,
    rowVersion: dto.row_version,
    activeSnapshotId: dto.active_snapshot_id ?? null,
    styleControls: dto.style_controls ?? {},
    // Metadata readiness alone does not prove that any usable font exists.
    fontAvailability: "unavailable",
  };
}

export type CloudJobView = {
  mode: "cloud";
  id: string;
  kind: JobDTO["job_type"];
  status:
    | "queued"
    | "processing"
    | "cancel-requested"
    | "completed"
    | "failed"
    | "canceled";
  stage: string | null;
  progress: number | null;
  completedUnits: number | null;
  totalUnits: number | null;
  planRevision: number | null;
  denominatorChangeReason: string | null;
  etaRangeSeconds: [number, number] | null;
  needsReview: boolean;
  errorCode: string | null;
  resultArtifactId: string | null;
};

export function mapJob(dto: JobDTO): CloudJobView {
  const statuses: Record<JobDTO["state"], CloudJobView["status"]> = {
    queued: "queued",
    running: "processing",
    cancel_requested: "cancel-requested",
    succeeded: "completed",
    failed: "failed",
    canceled: "canceled",
  };
  const work = dto.progress;
  const eta = dto.eta;
  return {
    mode: "cloud",
    id: dto.id,
    kind: dto.job_type,
    status: statuses[dto.state],
    stage: dto.stage ?? null,
    progress: work?.total_units
      ? work.completed_units / work.total_units
      : null,
    completedUnits: work?.completed_units ?? null,
    totalUnits: work?.total_units ?? null,
    planRevision: work?.plan_revision ?? null,
    denominatorChangeReason: work?.denominator_change_reason ?? null,
    etaRangeSeconds:
      eta?.lower_seconds != null &&
      eta.upper_seconds != null &&
      eta.benchmark_profile_id
        ? [eta.lower_seconds, eta.upper_seconds]
        : null,
    needsReview: dto.requires_user_action ?? false,
    errorCode: dto.error_code ?? null,
    resultArtifactId: dto.result_artifact_id ?? null,
  };
}

/** Preserve missing capture data. Conversion is opt-in and returns a new value. */
export function preserveLegacyStrokes(
  strokes: Stroke[],
): CanonicalStrokeDocument {
  if (!strokes.length || strokes.length > 2000)
    throw new Error("invalid_legacy_strokes");
  let total = 0;
  for (const stroke of strokes) {
    if (!stroke.length || stroke.length > 20000)
      throw new Error("invalid_legacy_strokes");
    total += stroke.length;
    for (const point of stroke) {
      if (
        !Number.isFinite(point.x) ||
        !Number.isFinite(point.y) ||
        point.x < 0 ||
        point.x > 1 ||
        point.y < 0 ||
        point.y > 1
      )
        throw new Error("invalid_legacy_strokes");
    }
  }
  if (total > 100000) throw new Error("stroke_point_budget_exceeded");
  return {
    schema_version: 1,
    coordinate_system: {
      origin: "top_left",
      x_positive: "right",
      y_positive: "down",
      normalized: true,
    },
    capture_size_px: null,
    timing: "unavailable_legacy",
    strokes: strokes.map((stroke, index) => ({
      stroke_id: `legacy-${index}`,
      pointer_type: null,
      points: stroke.map(({ x, y }) => ({
        x,
        y,
        t_ms: null,
        pressure: null,
        tilt_x: null,
        tilt_y: null,
        twist: null,
      })),
    })),
    parent_source_hash: null,
    operations: [],
  };
}

/** All measurements are supplied; no per-glyph autoscale or guessed baseline. */
export type CaptureToFontTransform = {
  captureWidthPx: number;
  captureHeightPx: number;
  baselinePx: number;
  originXPx: number;
  fontUnitsPerPixel: number;
  unitsPerEm: number;
};

function assertTransform(transform: CaptureToFontTransform) {
  if (
    !Object.values(transform).every(Number.isFinite) ||
    transform.captureWidthPx <= 0 ||
    transform.captureHeightPx <= 0 ||
    transform.fontUnitsPerPixel <= 0 ||
    !Number.isInteger(transform.unitsPerEm) ||
    transform.unitsPerEm < 16 ||
    transform.unitsPerEm > 16384
  )
    throw new Error("invalid_coordinate_transform");
}

export function capturePointToFont(
  point: Point,
  transform: CaptureToFontTransform,
): Point {
  assertTransform(transform);
  if (
    !Number.isFinite(point.x) ||
    !Number.isFinite(point.y) ||
    point.x < 0 ||
    point.x > 1 ||
    point.y < 0 ||
    point.y > 1
  )
    throw new Error("invalid_capture_point");
  return {
    x:
      (point.x * transform.captureWidthPx - transform.originXPx) *
      transform.fontUnitsPerPixel,
    y:
      (transform.baselinePx - point.y * transform.captureHeightPx) *
      transform.fontUnitsPerPixel,
  };
}

export function fontPointToCapture(
  point: Point,
  transform: CaptureToFontTransform,
): Point {
  assertTransform(transform);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y))
    throw new Error("invalid_font_point");
  return {
    x:
      (point.x / transform.fontUnitsPerPixel + transform.originXPx) /
      transform.captureWidthPx,
    y:
      (transform.baselinePx - point.y / transform.fontUnitsPerPixel) /
      transform.captureHeightPx,
  };
}

/** SVG applies y inversion once; canonical points, including descenders, stay intact. */
export function outlineSvgTransform(): string {
  return "scale(1 -1)";
}

/** Prototype rotate/scale/crop operate on display pixels; they are not vector edits. */
export function assertCanonicalEditSupported(edit: {
  rotation: number;
  scale: number;
  crop: number;
}): void {
  if (edit.rotation !== 0 || edit.scale !== 100 || edit.crop !== 0)
    throw new Error(
      "prototype_preview_edit_requires_explicit_geometry_conversion",
    );
}

export type ServiceMode = "demo" | "cloud";
export function selectServiceMode(explicitChoice?: ServiceMode): ServiceMode {
  return explicitChoice ?? "demo";
}
