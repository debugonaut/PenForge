/**
 * Durable account working state. Demo projects never enter this module, and
 * binary previews are referenced by private capture asset, never stored in JSON.
 */
import type { components } from "../../packages/contracts/src/generated";
import {
  createAccountRequest,
  type AccountRequestOptions,
} from "./account-request";
import type {
  CaptureReference,
  CreationStep,
  ExportFormat,
  ExportReference,
  FontProject,
  FontStyle,
  Glyph,
  GlyphStatus,
  Sample,
  Stroke,
} from "./contracts";

export type WorkspaceResponseWire = components["schemas"]["WorkspaceResponse"];
export type WorkspaceRequestWire = components["schemas"]["WorkspacePut"];

export type AccountGlyph = {
  id: string;
  character: string;
  sourceCharacter: string;
  status: GlyphStatus;
  revision: number;
  rotation: number;
  scale: number;
  crop: number;
  strokes: Stroke[];
};

export type AccountSample = {
  kind: "draw" | "upload" | "demo";
  strokes: Stroke[];
  strokeDocument?: Sample["strokeDocument"];
  captureStartedAtMs?: number;
  originalCaptureId?: string;
  captureAssetId?: string;
  fileName?: string;
};

export type AccountAssignment = {
  sampleId: string;
  codepoints: number[];
  updatedAt: string;
};

export type AccountWorkspace = {
  schemaVersion: 1;
  scriptCode: "Latn" | "Deva";
  step: CreationStep;
  glyphs: AccountGlyph[];
  style: FontStyle;
  sample: AccountSample | null;
  previewText: string;
  review: { approvedGlyphIds: string[]; assignments: AccountAssignment[] };
  captures: CaptureReference[];
  exports: ExportReference[];
  snapshots: { id: string; label: string; createdAt: string }[];
  selectedExportId: string | null;
  activeSnapshotId: string | null;
};

export type LoadedWorkspace = {
  projectId: string;
  workspace: AccountWorkspace;
  version: number;
  updatedAt: string;
};

export class WorkspaceError extends Error {
  constructor(
    readonly code:
      | "account_required"
      | "email_verification_required"
      | "terms_required"
      | "policy_changed"
      | "service_not_ready"
      | "unauthenticated"
      | "not_found"
      | "revision_conflict"
      | "precondition_required"
      | "state_conflict"
      | "validation_error"
      | "dependency_unavailable"
      | "network_unavailable"
      | "identity_mismatch"
      | "recovery_unavailable",
    readonly status = 0,
    readonly currentVersion?: number,
  ) {
    super("Account save could not be confirmed.");
    this.name = "WorkspaceError";
  }
}

const codes = new Set([
  "account_required",
  "email_verification_required",
  "terms_required",
  "policy_changed",
  "service_not_ready",

  "unauthenticated",
  "invalid_token",
  "account_merged",
  "account_deleting",
  "account_suspended",
  "not_found",
  "revision_conflict",
  "precondition_required",
  "state_conflict",
  "validation_error",
  "dependency_unavailable",
  "rate_limited",
]);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
) {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(maximum, Math.max(minimum, value))
    : fallback;
}

function text(value: unknown, maximum: number, fallback = "") {
  return typeof value === "string" ? value.slice(0, maximum) : fallback;
}

const STEPS = new Set([
  "capture",
  "review",
  "edit",
  "style",
  "preview",
  "export",
]);
const STATUSES = new Set([
  "missing",
  "detected",
  "needs-review",
  "edited",
  "approved",
]);
const FORMATS = new Set(["ttf", "otf", "woff2"]);

function toStrokes(value: unknown): Stroke[] {
  if (!Array.isArray(value) || value.length > 2000) return [];
  let budget = 100_000;
  return value.flatMap((stroke): Stroke[] => {
    if (!Array.isArray(stroke)) return [];
    const points = stroke.flatMap((point): { x: number; y: number }[] => {
      if (!record(point) || budget <= 0) return [];
      const x = num(point.x, -1, 0, 1);
      const y = num(point.y, -1, 0, 1);
      if (x < 0 || y < 0) return [];
      budget -= 1;
      return [{ x, y }];
    });
    return points.length ? [points] : [];
  });
}

/** Wire values are mapped explicitly; defaults never invent observed metadata. */
export function fromWire(value: unknown): AccountWorkspace {
  const wire = record(value) ? value : {};
  const style = record(wire.style) ? wire.style : {};
  const review = record(wire.review) ? wire.review : {};
  const sample = record(wire.sample) ? wire.sample : null;
  const rawGlyphs = Array.isArray(wire.glyphs)
    ? wire.glyphs.slice(0, 1024)
    : [];
  const glyphs: AccountGlyph[] = rawGlyphs.flatMap((item): AccountGlyph[] => {
    if (!record(item) || typeof item.id !== "string" || !item.id) return [];
    const character = text(item.character, 32);
    const sourceCharacter = text(item.source_character, 32);
    if (!character || !sourceCharacter) return [];
    const status = STATUSES.has(String(item.status))
      ? (item.status as GlyphStatus)
      : "needs-review";
    return [
      {
        id: item.id.slice(0, 200),
        character,
        sourceCharacter,
        status,
        revision: Math.max(0, Math.floor(num(item.revision, 0, 0, 1_000_000))),
        rotation: num(item.rotation, 0, -360, 360),
        scale: num(item.scale, 100, 1, 1000),
        crop: num(item.crop, 0, 0, 49),
        strokes: toStrokes(item.strokes),
      },
    ];
  });
  const captures = Array.isArray(wire.captures)
    ? wire.captures.slice(0, 64).flatMap((item): CaptureReference[] => {
        if (
          !record(item) ||
          typeof item.sample_id !== "string" ||
          typeof item.asset_id !== "string" ||
          (item.kind !== "draw" && item.kind !== "image")
        )
          return [];
        return [
          {
            sampleId: item.sample_id,
            assetId: item.asset_id,
            kind: item.kind,
            createdAt: text(item.created_at, 40),
          },
        ];
      })
    : [];
  const exports = Array.isArray(wire.exports)
    ? wire.exports.slice(0, 32).flatMap((item): ExportReference[] => {
        if (
          !record(item) ||
          typeof item.export_id !== "string" ||
          !FORMATS.has(String(item.format)) ||
          !["requested", "processing", "ready", "failed"].includes(
            String(item.status),
          )
        )
          return [];
        return [
          {
            exportId: item.export_id,
            format: item.format as ExportFormat,
            status: item.status as ExportReference["status"],
            ...(typeof item.sha256 === "string" &&
            /^[0-9a-f]{64}$/.test(item.sha256)
              ? { sha256: item.sha256 }
              : {}),
            createdAt: text(item.created_at, 40),
          },
        ];
      })
    : [];
  return {
    schemaVersion: 1,
    scriptCode: wire.script_code === "Deva" ? "Deva" : "Latn",
    step: STEPS.has(String(wire.step))
      ? (wire.step as CreationStep)
      : "capture",
    glyphs,
    style: {
      thickness: num(style.thickness, 50, 0, 100),
      slant: num(style.slant, 0, -45, 45),
      spacing: num(style.spacing, 50, 0, 100),
      smoothness: num(style.smoothness, 50, 0, 100),
      proportion: num(style.proportion, 50, 0, 100),
    },
    sample:
      sample === null
        ? null
        : {
            kind:
              sample.kind === "draw" || sample.kind === "demo"
                ? sample.kind
                : "upload",
            strokes: toStrokes(sample.strokes),
            ...(record(sample.stroke_document)
              ? {
                  strokeDocument:
                    sample.stroke_document as AccountSample["strokeDocument"],
                }
              : {}),
            ...(typeof sample.capture_started_at_ms === "number"
              ? { captureStartedAtMs: sample.capture_started_at_ms }
              : {}),
            ...(typeof sample.original_capture_id === "string"
              ? { originalCaptureId: sample.original_capture_id }
              : {}),
            ...(typeof sample.capture_asset_id === "string"
              ? { captureAssetId: sample.capture_asset_id }
              : {}),
            ...(typeof sample.file_name === "string"
              ? { fileName: sample.file_name.slice(0, 1024) }
              : {}),
          },
    previewText: text(wire.preview_text, 10_000),
    review: {
      approvedGlyphIds: Array.isArray(review.approved_glyph_ids)
        ? review.approved_glyph_ids
            .filter((id): id is string => typeof id === "string")
            .slice(0, 1024)
        : [],
      assignments: Array.isArray(review.assignments)
        ? review.assignments
            .slice(0, 512)
            .flatMap((item): AccountAssignment[] => {
              if (
                !record(item) ||
                typeof item.sample_id !== "string" ||
                !Array.isArray(item.codepoints)
              )
                return [];
              return [
                {
                  sampleId: item.sample_id,
                  codepoints: item.codepoints
                    .filter(
                      (cp): cp is number =>
                        typeof cp === "number" &&
                        Number.isInteger(cp) &&
                        cp >= 0 &&
                        cp <= 0x10ffff,
                    )
                    .slice(0, 32),
                  updatedAt: text(item.updated_at, 40),
                },
              ];
            })
        : [],
    },
    captures,
    exports,
    snapshots: Array.isArray(wire.snapshots)
      ? wire.snapshots.slice(0, 32).flatMap((item) => {
          if (!record(item) || typeof item.id !== "string") return [];
          return [
            {
              id: item.id,
              label: text(item.label, 120),
              createdAt: text(item.created_at, 40),
            },
          ];
        })
      : [],
    selectedExportId:
      typeof wire.selected_export_id === "string"
        ? wire.selected_export_id
        : null,
    activeSnapshotId:
      typeof wire.active_snapshot_id === "string"
        ? wire.active_snapshot_id
        : null,
  };
}

/** Build the wire body from required account state. No preview URL is sent. */
export function toWire(
  state: AccountWorkspace,
): WorkspaceRequestWire["workspace"] {
  return {
    schema_version: 1,
    script_code: state.scriptCode,
    step: state.step,
    glyphs: state.glyphs.map((glyph) => ({
      id: glyph.id,
      character: glyph.character,
      source_character: glyph.sourceCharacter,
      status: glyph.status,
      revision: glyph.revision,
      rotation: glyph.rotation,
      scale: glyph.scale,
      crop: glyph.crop,
      strokes: glyph.strokes.map((stroke) =>
        stroke.map(({ x, y }) => ({ x, y })),
      ),
    })),
    style: state.style,
    sample: state.sample
      ? {
          kind: state.sample.kind,
          ...(state.sample.strokes.length
            ? {
                strokes: state.sample.strokes.map((stroke) =>
                  stroke.map(({ x, y }) => ({ x, y })),
                ),
              }
            : {}),
          ...(state.sample.strokeDocument
            ? { stroke_document: state.sample.strokeDocument }
            : {}),
          ...(Number.isFinite(state.sample.captureStartedAtMs)
            ? {
                // Observed origins are sub-millisecond doubles; the server column
                // is integer milliseconds, so round at the boundary without
                // inventing a different time.
                capture_started_at_ms: Math.min(
                  10 ** 13,
                  Math.max(
                    0,
                    Math.round(state.sample.captureStartedAtMs as number),
                  ),
                ),
              }
            : {}),
          ...(state.sample.originalCaptureId
            ? { original_capture_id: state.sample.originalCaptureId }
            : {}),
          ...(state.sample.captureAssetId
            ? { capture_asset_id: state.sample.captureAssetId }
            : {}),
          ...(state.sample.fileName
            ? { file_name: state.sample.fileName }
            : {}),
        }
      : null,
    preview_text: state.previewText,
    review: {
      approved_glyph_ids: state.review.approvedGlyphIds,
      assignments: state.review.assignments.map((item) => ({
        sample_id: item.sampleId,
        codepoints: item.codepoints,
        updated_at: item.updatedAt,
      })),
    },
    captures: state.captures.map((capture) => ({
      sample_id: capture.sampleId,
      asset_id: capture.assetId,
      kind: capture.kind,
      created_at: capture.createdAt,
    })),
    exports: state.exports.map((item) => {
      // Draft references keep the bare lowercase digest; the canonical
      // `sha256:`-prefixed value stays with the immutable export record.
      const digest = (item.sha256 ?? "").replace(/^sha256:/i, "").toLowerCase();
      return {
        export_id: item.exportId,
        format: item.format,
        status: item.status,
        ...(/^[0-9a-f]{64}$/.test(digest) ? { sha256: digest } : {}),
        created_at: item.createdAt,
      };
    }),
    snapshots: state.snapshots.map((item) => ({
      id: item.id,
      label: item.label,
      created_at: item.createdAt,
    })),
    selected_export_id: state.selectedExportId,
    active_snapshot_id: state.activeSnapshotId,
  };
}

export function emptyWorkspace(
  scriptCode: "Latn" | "Deva" = "Latn",
): AccountWorkspace {
  return {
    schemaVersion: 1,
    scriptCode,
    step: "capture",
    glyphs: [],
    style: {
      thickness: 50,
      slant: 0,
      spacing: 50,
      smoothness: 50,
      proportion: 50,
    },
    sample: null,
    previewText: "",
    review: { approvedGlyphIds: [], assignments: [] },
    captures: [],
    exports: [],
    snapshots: [],
    selectedExportId: null,
    activeSnapshotId: null,
  };
}

export function projectToWorkspace(project: FontProject): AccountWorkspace {
  const sample = project.sample;
  return {
    schemaVersion: 1,
    scriptCode: project.scriptCode,
    step: project.step,
    glyphs: project.glyphs.map((glyph) => ({
      ...glyph,
      strokes: glyph.strokes.map((stroke) =>
        stroke.map(({ x, y }) => ({ x, y })),
      ),
    })),
    style: { ...project.style },
    sample: sample
      ? {
          kind: sample.kind,
          strokes: (sample.strokes ?? []).map((stroke) =>
            stroke.map(({ x, y }) => ({ x, y })),
          ),
          ...(sample.strokeDocument
            ? { strokeDocument: sample.strokeDocument }
            : {}),
          ...(sample.captureStartedAtMs !== undefined
            ? { captureStartedAtMs: sample.captureStartedAtMs }
            : {}),
          ...(sample.originalCaptureId
            ? { originalCaptureId: sample.originalCaptureId }
            : {}),
          ...(sample.captureAssetId
            ? { captureAssetId: sample.captureAssetId }
            : {}),
          ...(sample.fileName ? { fileName: sample.fileName } : {}),
        }
      : null,
    previewText: project.previewText,
    review: {
      approvedGlyphIds: project.glyphs
        .filter((glyph) => glyph.status === "approved")
        .map((glyph) => glyph.id),
      assignments: project.assignments.map((item) => ({ ...item })),
    },
    captures: project.captures.map((capture) => ({ ...capture })),
    exports: project.exports.map((item) => ({ ...item })),
    snapshots: project.snapshots.map((item) => ({ ...item })),
    selectedExportId: project.selectedExportId,
    activeSnapshotId: project.activeSnapshotId,
  };
}

export function workspaceToProject(
  id: string,
  name: string,
  serverUpdatedAt: string,
  ownerUid: string,
  version: number,
  workspace: AccountWorkspace,
): FontProject {
  const sample: Sample | null = workspace.sample
    ? {
        kind: workspace.sample.kind,
        ...(workspace.sample.strokes.length
          ? { strokes: workspace.sample.strokes }
          : {}),
        ...(workspace.sample.strokeDocument
          ? { strokeDocument: workspace.sample.strokeDocument }
          : {}),
        ...(workspace.sample.captureStartedAtMs !== undefined
          ? { captureStartedAtMs: workspace.sample.captureStartedAtMs }
          : {}),
        ...(workspace.sample.originalCaptureId
          ? { originalCaptureId: workspace.sample.originalCaptureId }
          : {}),
        ...(workspace.sample.captureAssetId
          ? { captureAssetId: workspace.sample.captureAssetId }
          : {}),
        ...(workspace.sample.fileName
          ? { fileName: workspace.sample.fileName }
          : {}),
      }
    : null;
  const glyphs: Glyph[] = workspace.glyphs.map((glyph) => ({ ...glyph }));
  return {
    id,
    name,
    updatedAt: serverUpdatedAt,
    step: workspace.step,
    glyphs,
    style: { ...workspace.style },
    sample,
    previewText: workspace.previewText,
    scriptCode: workspace.scriptCode,
    captures: workspace.captures.map((capture) => ({ ...capture })),
    exports: workspace.exports.map((item) => ({ ...item })),
    assignments: workspace.review.assignments.map((item) => ({ ...item })),
    snapshots: workspace.snapshots.map((item) => ({ ...item })),
    activeSnapshotId: workspace.activeSnapshotId,
    selectedExportId: workspace.selectedExportId,
    origin: { kind: "account", ownerUid, workspaceVersion: version },
  };
}

export function createWorkspaceClient(
  baseUrl: string,
  token: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  const send = createAccountRequest(
    baseUrl,
    token,
    { error: WorkspaceError, codes },
    fetcher,
  );
  async function request(
    path: string,
    options: AccountRequestOptions = {},
  ): Promise<{ body?: WorkspaceResponseWire; etag: string | null }> {
    const response = await send(path, options);
    return {
      body: (await response.json()) as WorkspaceResponseWire,
      etag: response.headers.get("ETag"),
    };
  }
  const path = (projectId: string) =>
    `/api/v1/projects/${encodeURIComponent(projectId)}/workspace`;
  return {
    async get(
      projectId: string,
      signal?: AbortSignal,
    ): Promise<LoadedWorkspace | null> {
      try {
        const result = await request(path(projectId), { signal });
        if (!result.body)
          throw new WorkspaceError("dependency_unavailable", 502);
        return {
          projectId: result.body.project_id,
          workspace: fromWire(result.body.workspace),
          version: result.body.row_version,
          updatedAt: result.body.updated_at,
        };
      } catch (cause) {
        if (cause instanceof WorkspaceError && cause.code === "not_found")
          return null;
        throw cause;
      }
    },
    async put(
      projectId: string,
      workspace: AccountWorkspace,
      version: number,
      signal?: AbortSignal,
    ): Promise<{
      workspace: AccountWorkspace;
      version: number;
      updatedAt: string;
    }> {
      const result = await request(path(projectId), {
        method: "PUT",
        body: { workspace: toWire(workspace) },
        ifMatch: `"v${version}"`,
        signal,
      });
      if (!result.body) throw new WorkspaceError("dependency_unavailable", 502);
      return {
        workspace: fromWire(result.body.workspace),
        version: result.body.row_version,
        updatedAt: result.body.updated_at,
      };
    },
  };
}
export type WorkspaceClient = ReturnType<typeof createWorkspaceClient>;
