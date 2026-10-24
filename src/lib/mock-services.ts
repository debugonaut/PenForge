import type {
  FontProject,
  FontService,
  FontStyle,
  Glyph,
  HandwritingService,
  ProcessingJob,
  ProjectChanges,
  ProjectService,
  ProjectStorageInfo,
  Sample,
  Stroke,
} from "./contracts";

// Keep the original key so projects from the first prototype can be migrated.
const STORAGE_KEY = "penforge-projects-v1";
const RECOVERY_KEY = "penforge-projects-recovery-v1";
const SCHEMA_VERSION = 1;
const DEMO_CHARS =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,!?";
const STEP_IDS = new Set([
  "capture",
  "review",
  "edit",
  "style",
  "preview",
  "export",
]);
const GLYPH_STATUSES = new Set([
  "missing",
  "detected",
  "needs-review",
  "edited",
  "approved",
]);
const DEFAULT_STYLE: FontStyle = {
  thickness: 50,
  slant: 0,
  spacing: 50,
  smoothness: 50,
  proportion: 50,
};

export class LocalProjectError extends Error {
  constructor(
    public readonly code:
      | "unavailable"
      | "invalid-data"
      | "unsupported-version"
      | "not-found"
      | "invalid-project",
    message: string,
  ) {
    super(message);
    this.name = "LocalProjectError";
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown, max = 10000): value is string =>
  typeof value === "string" && value.length <= max;
const number = (
  value: unknown,
  min = -Infinity,
  max = Infinity,
): value is number =>
  typeof value === "number" &&
  Number.isFinite(value) &&
  value >= min &&
  value <= max;
const character = (value: unknown): value is string =>
  typeof value === "string" && [...value].length === 1;

function validStrokes(value: unknown): value is Stroke[] {
  return (
    Array.isArray(value) &&
    value.length <= 2000 &&
    value.every(
      (stroke) =>
        Array.isArray(stroke) &&
        stroke.length <= 20000 &&
        stroke.every(
          (point) =>
            record(point) && number(point.x, 0, 1) && number(point.y, 0, 1),
        ),
    )
  );
}

const safeCaptureId = (value: unknown): value is string =>
  typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(value);
const onlyKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).every((key) => keys.includes(key));

/** Local recovery must never display one drawing while submitting a different raw source. */
function validStrokeMetadata(
  sample: Record<string, unknown>,
  strokes: Stroke[],
): boolean {
  const value = sample.strokeDocument;
  if (value === undefined) return sample.captureStartedAtMs === undefined;
  if (
    !record(value) ||
    !onlyKeys(value, [
      "schema_version",
      "coordinate_system",
      "capture_size_px",
      "timing",
      "strokes",
      "parent_source_hash",
      "operations",
    ]) ||
    value.schema_version !== 1 ||
    !record(value.coordinate_system) ||
    !onlyKeys(value.coordinate_system, [
      "origin",
      "x_positive",
      "y_positive",
      "normalized",
    ]) ||
    value.coordinate_system.origin !== "top_left" ||
    value.coordinate_system.x_positive !== "right" ||
    value.coordinate_system.y_positive !== "down" ||
    value.coordinate_system.normalized !== true ||
    !Array.isArray(value.strokes) ||
    value.strokes.length === 0 ||
    value.strokes.length !== strokes.length ||
    (value.parent_source_hash !== undefined &&
      value.parent_source_hash !== null) ||
    (value.operations !== undefined &&
      (!Array.isArray(value.operations) || value.operations.length !== 0))
  )
    return false;
  const observed = value.timing === "observed";
  if (!observed && value.timing !== "unavailable_legacy") return false;
  if (observed) {
    if (
      !record(value.capture_size_px) ||
      !onlyKeys(value.capture_size_px, ["width", "height"]) ||
      !Number.isInteger(value.capture_size_px.width) ||
      !number(value.capture_size_px.width, 1, 32768) ||
      !Number.isInteger(value.capture_size_px.height) ||
      !number(value.capture_size_px.height, 1, 32768) ||
      !number(sample.captureStartedAtMs, 0)
    )
      return false;
  } else if (
    value.capture_size_px !== null ||
    sample.captureStartedAtMs !== undefined
  )
    return false;
  const ids = new Set<string>();
  let total = 0;
  let lastTime = 0;
  return value.strokes.every((stroke, strokeIndex) => {
    if (
      !record(stroke) ||
      !onlyKeys(stroke, ["stroke_id", "pointer_type", "points"]) ||
      !safeCaptureId(stroke.stroke_id) ||
      ids.has(stroke.stroke_id) ||
      !Array.isArray(stroke.points) ||
      !stroke.points.length ||
      stroke.points.length > 20000 ||
      stroke.points.length !== strokes[strokeIndex].length
    )
      return false;
    ids.add(stroke.stroke_id);
    total += stroke.points.length;
    if (
      total > 100000 ||
      (stroke.pointer_type != null &&
        !["pen", "mouse", "touch", "keyboard"].includes(
          String(stroke.pointer_type),
        )) ||
      (!observed && stroke.pointer_type != null)
    )
      return false;
    return stroke.points.every((point, pointIndex) => {
      if (
        !record(point) ||
        !onlyKeys(point, [
          "x",
          "y",
          "t_ms",
          "pressure",
          "tilt_x",
          "tilt_y",
          "twist",
        ]) ||
        !number(point.x, 0, 1) ||
        !number(point.y, 0, 1) ||
        point.x !== strokes[strokeIndex][pointIndex].x ||
        point.y !== strokes[strokeIndex][pointIndex].y
      )
        return false;
      if (observed) {
        if (!number(point.t_ms, lastTime, 1e12)) return false;
        lastTime = point.t_ms;
      } else if (point.t_ms != null) return false;
      const metadata = ["pressure", "tilt_x", "tilt_y", "twist"];
      if (
        (!observed || stroke.pointer_type !== "pen") &&
        metadata.some((key) => point[key] != null)
      )
        return false;
      return (
        (point.pressure == null || number(point.pressure, 0, 1)) &&
        (point.tilt_x == null || number(point.tilt_x, -90, 90)) &&
        (point.tilt_y == null || number(point.tilt_y, -90, 90)) &&
        (point.twist == null ||
          (number(point.twist, 0, 360) && point.twist < 360))
      );
    });
  });
}

function validSample(value: unknown): value is Sample | null {
  if (value === null) return true;
  if (!record(value)) return false;
  if (
    value.originalCaptureId !== undefined &&
    !safeCaptureId(value.originalCaptureId)
  )
    return false;
  if (value.kind === "draw")
    return (
      value.originalCaptureId === undefined &&
      validStrokes(value.strokes) &&
      validStrokeMetadata(value, value.strokes)
    );
  if (
    value.strokeDocument !== undefined ||
    value.captureStartedAtMs !== undefined
  )
    return false;
  if (value.kind === "demo") return value.originalCaptureId === undefined;
  return (
    value.kind === "upload" &&
    text(value.dataUrl, 7 * 1024 * 1024) &&
    /^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/]+=*$/.test(
      value.dataUrl,
    ) &&
    (value.fileName === undefined || text(value.fileName, 1024))
  );
}

function validStyle(value: unknown): value is FontStyle {
  return (
    record(value) &&
    number(value.thickness, 0, 100) &&
    number(value.slant, -45, 45) &&
    number(value.spacing, 0, 100) &&
    number(value.smoothness, 0, 100) &&
    number(value.proportion, 0, 100)
  );
}

function validGlyph(value: unknown): value is Glyph {
  return (
    record(value) &&
    text(value.id, 200) &&
    value.id.length > 0 &&
    character(value.character) &&
    character(value.sourceCharacter) &&
    typeof value.status === "string" &&
    GLYPH_STATUSES.has(value.status) &&
    number(value.revision, 0) &&
    Number.isInteger(value.revision) &&
    number(value.rotation) &&
    number(value.scale, 1, 1000) &&
    number(value.crop, 0, 49) &&
    validStrokes(value.strokes)
  );
}

function validProject(value: unknown): value is FontProject {
  if (
    !record(value) ||
    !text(value.id, 200) ||
    !value.id ||
    !text(value.name, 240) ||
    !value.name.trim() ||
    !text(value.updatedAt, 40) ||
    !Number.isFinite(Date.parse(value.updatedAt)) ||
    typeof value.step !== "string" ||
    !STEP_IDS.has(value.step) ||
    !record(value.origin) ||
    value.origin.kind !== "demo" ||
    (value.scriptCode !== "Latn" && value.scriptCode !== "Deva") ||
    !validStyle(value.style) ||
    !validSample(value.sample) ||
    !text(value.previewText) ||
    !Array.isArray(value.captures) ||
    !Array.isArray(value.exports) ||
    !Array.isArray(value.assignments) ||
    !Array.isArray(value.snapshots) ||
    (value.activeSnapshotId !== null &&
      !text(value.activeSnapshotId, 200)) ||
    (value.selectedExportId !== null &&
      !text(value.selectedExportId, 200))
  )
    return false;
  return (
    Array.isArray(value.glyphs) &&
    value.glyphs.length <= 1024 &&
    value.glyphs.every(validGlyph) &&
    new Set(value.glyphs.map((glyph) => glyph.id)).size === value.glyphs.length
  );
}

/** Read a stored demo draft, migrating the legacy `isDemo: true` marker explicitly. */
function normalizeStoredProject(value: unknown): FontProject | null {
  if (!record(value)) return null;
  const demoOrigin =
    record(value.origin) && value.origin.kind === "demo"
      ? { kind: "demo" as const }
      : value.isDemo === true
        ? { kind: "demo" as const }
        : null;
  if (!demoOrigin) return null;
  const candidate: Record<string, unknown> = {
    ...value,
    origin: demoOrigin,
    scriptCode: value.scriptCode === "Deva" ? "Deva" : "Latn",
    captures: Array.isArray(value.captures) ? value.captures : [],
    exports: Array.isArray(value.exports) ? value.exports : [],
    assignments: Array.isArray(value.assignments) ? value.assignments : [],
    snapshots: Array.isArray(value.snapshots) ? value.snapshots : [],
    activeSnapshotId:
      typeof value.activeSnapshotId === "string"
        ? value.activeSnapshotId
        : null,
    selectedExportId:
      typeof value.selectedExportId === "string"
        ? value.selectedExportId
        : null,
  };
  delete candidate.isDemo;
  return validProject(candidate) ? candidate : null;
}

function storage(): Storage {
  try {
    if (typeof window !== "undefined") return window.localStorage;
  } catch {
    /* Browser privacy settings can reject even reading localStorage. */
  }
  throw new LocalProjectError(
    "unavailable",
    "Browser storage is unavailable. Enable local storage to keep your drafts.",
  );
}

type Snapshot = {
  raw: string | null;
  projects: FontProject[];
  info: ProjectStorageInfo;
};

function snapshot(): Snapshot {
  const local = storage();
  const raw = local.getItem(STORAGE_KEY);
  const info: ProjectStorageInfo = {
    state: "ready",
    recoverableProjects: 0,
    invalidProjects: 0,
    recoveryAvailable: local.getItem(RECOVERY_KEY) !== null,
  };
  if (raw === null) return { raw, projects: [], info };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {
      raw,
      projects: [],
      info: {
        ...info,
        state: "needs-recovery",
        message:
          "Saved draft data could not be read. Recover to keep a backup and start with an empty desk.",
      },
    };
  }
  // Legacy arrays are accepted; the next successful write adds the envelope.
  const candidates = Array.isArray(parsed)
    ? parsed
    : record(parsed) &&
        parsed.schemaVersion === SCHEMA_VERSION &&
        Array.isArray(parsed.projects)
      ? parsed.projects
      : null;
  if (!candidates) {
    const unsupported =
      record(parsed) &&
      typeof parsed.schemaVersion === "number" &&
      parsed.schemaVersion !== SCHEMA_VERSION;
    return {
      raw,
      projects: [],
      info: {
        ...info,
        state: unsupported ? "unsupported" : "needs-recovery",
        message: unsupported
          ? "These drafts use a different storage version. Their data has been kept unchanged. Open them with the matching version of Penforge, or reset this browser's drafts."
          : "Saved draft data has an unexpected format. Recover to keep a backup and start with an empty desk.",
      },
    };
  }
  const seen = new Set<string>();
  const projects = candidates.flatMap((item): FontProject[] => {
    const project = normalizeStoredProject(item);
    if (!project || seen.has(project.id)) return [];
    seen.add(project.id);
    return [project];
  });
  const invalidProjects = candidates.length - projects.length;
  return {
    raw,
    projects,
    info: {
      ...info,
      recoverableProjects: projects.length,
      invalidProjects,
      ...(invalidProjects
        ? {
            state: "needs-recovery",
            message: `${invalidProjects} saved draft${invalidProjects === 1 ? " has" : "s have"} invalid data. Recover the valid drafts before continuing; the original data will be backed up.`,
          }
        : {}),
    },
  };
}

function readAll(): FontProject[] {
  const current = snapshot();
  if (current.info.state !== "ready")
    throw new LocalProjectError(
      current.info.state === "unsupported"
        ? "unsupported-version"
        : "invalid-data",
      current.info.message || "Saved drafts need recovery.",
    );
  return current.projects;
}

function writeAll(projects: FontProject[]) {
  storage().setItem(
    STORAGE_KEY,
    JSON.stringify({ schemaVersion: SCHEMA_VERSION, projects }),
  );
}

function findProject(projects: FontProject[], id: string): FontProject {
  const project = projects.find((item) => item.id === id);
  if (!project)
    throw new LocalProjectError(
      "not-found",
      "This draft no longer exists in this browser. Open the workbench to choose another project.",
    );
  return project;
}

function validateProject(project: FontProject): void {
  if (!validProject(project))
    throw new LocalProjectError(
      "invalid-project",
      "This draft could not be saved because some project data is invalid. Keep names under 240 characters and preview text under 10,000 characters.",
    );
}

function updateProject(id: string, changes: ProjectChanges): FontProject {
  const all = readAll();
  const current = findProject(all, id);
  // Identity, demo status and timestamps remain owned by the adapter at runtime,
  // including when an untyped caller supplies unexpected fields.
  const next: FontProject = {
    ...current,
    ...changes,
    id: current.id,
    origin: { kind: "demo" },
    updatedAt: new Date().toISOString(),
  };
  validateProject(next);
  writeAll(all.map((item) => (item.id === id ? next : item)));
  return next;
}

function job(
  kind: ProcessingJob["kind"],
  status: ProcessingJob["status"],
  message: string,
  errorCode?: string,
): ProcessingJob {
  return {
    id: crypto.randomUUID(),
    kind,
    status,
    progress: status === "completed" ? 100 : undefined,
    message,
    ...(errorCode ? { errorCode } : {}),
  };
}
const pause = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

export const projectService: ProjectService = {
  async list() {
    return readAll().sort(
      (a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt),
    );
  },
  async get(id) {
    return findProject(readAll(), id);
  },
  async create(name) {
    const all = readAll();
    const project: FontProject = {
      id: crypto.randomUUID(),
      name: name.trim() || "Untitled font",
      updatedAt: new Date().toISOString(),
      step: "capture",
      glyphs: [],
      style: { ...DEFAULT_STYLE },
      sample: null,
      previewText: "A little note, in my own hand.",
      scriptCode: "Latn",
      captures: [],
      exports: [],
      assignments: [],
      snapshots: [],
      activeSnapshotId: null,
      selectedExportId: null,
      origin: { kind: "demo" },
    };
    validateProject(project);
    writeAll([...all, project]);
    return project;
  },
  // Prefer update for UI changes: a field patch preserves changes from other
  // steps and services. A deleted project is never recreated by a late save.
  async save(project) {
    return updateProject(project.id, project);
  },
  async update(id, changes) {
    return updateProject(id, changes);
  },
  async remove(id) {
    writeAll(readAll().filter((item) => item.id !== id));
  },
  async duplicate(id) {
    const all = readAll();
    const source = findProject(all, id);
    const sample =
      source.sample?.kind === "upload" ? { ...source.sample } : source.sample;
    // The original vault is scoped to the source draft. A copied preview needs an
    // explicit file selection before it can become a private source for this draft.
    if (sample?.kind === "upload") delete sample.originalCaptureId;
    const copy = {
      ...source,
      sample,
      id: crypto.randomUUID(),
      name: `${source.name.slice(0, 235)} copy`,
      updatedAt: new Date().toISOString(),
    };
    writeAll([...all, copy]);
    return copy;
  },
  async storageInfo() {
    try {
      return snapshot().info;
    } catch {
      return {
        state: "unavailable",
        message:
          "Browser storage is unavailable. Enable local storage to keep your drafts.",
        recoverableProjects: 0,
        invalidProjects: 0,
        recoveryAvailable: false,
      };
    }
  },
  async recoverLocalData() {
    const current = snapshot();
    if (current.info.state === "unsupported")
      throw new LocalProjectError(
        "unsupported-version",
        current.info.message || "Unsupported draft storage version.",
      );
    if (current.info.state === "ready") return current.projects;
    // Back up first. If storage is full, neither the source nor its backup is
    // replaced and the caller can explain that recovery could not complete.
    if (current.raw !== null) storage().setItem(RECOVERY_KEY, current.raw);
    writeAll(current.projects);
    return current.projects;
  },
  async recoveryBackup() {
    const current = snapshot();
    // Offer the untouched source before recovery/reset, including newer schema
    // versions. After recovery, offer the saved copy of that source instead.
    return current.info.state === "needs-recovery" ||
      current.info.state === "unsupported"
      ? current.raw
      : storage().getItem(RECOVERY_KEY);
  },
  async resetLocalData() {
    const local = storage();
    writeAll([]);
    local.removeItem(RECOVERY_KEY);
  },
};

function makeGlyphs(): Glyph[] {
  return [...DEMO_CHARS].map((value, index) => ({
    id: `glyph-${index}`,
    character: value,
    sourceCharacter: value,
    status:
      index % 17 === 0
        ? "missing"
        : index % 9 === 0
          ? "needs-review"
          : "detected",
    revision: 0,
    rotation: 0,
    scale: 100,
    crop: 0,
    strokes: [],
  }));
}

export const handwritingService: HandwritingService = {
  async submitSample(projectId, sample) {
    if (
      !validSample(sample) ||
      sample === null ||
      (sample.kind === "draw" &&
        !sample.strokes?.some((stroke) => stroke.length))
    )
      throw new LocalProjectError(
        "invalid-project",
        "Draw a mark, upload an image, or choose the demo sample before continuing.",
      );
    updateProject(projectId, { sample });
    return job("extract", "completed", "Sample saved locally for this demo.");
  },
  async extract(projectId) {
    await pause(550);
    const project = findProject(readAll(), projectId);
    if (
      !project.sample ||
      (project.sample.kind === "draw" &&
        !project.sample.strokes?.some((stroke) => stroke.length))
    )
      return job(
        "extract",
        "needs-input",
        "Add a handwriting sample or choose the demo sample first.",
        "SAMPLE_REQUIRED",
      );
    // Preparing suggestions again must never silently erase reviewed drawings.
    if (project.glyphs.length)
      return job(
        "extract",
        "completed",
        "Your existing demo letters and edits have been kept.",
      );
    updateProject(projectId, { glyphs: makeGlyphs() });
    return job(
      "extract",
      "completed",
      "Demo glyph suggestions are ready for your review. They are a fixture, not extracted from your sample.",
    );
  },
  async reviewGlyph(projectId, glyph) {
    if (!validGlyph(glyph))
      throw new LocalProjectError(
        "invalid-project",
        "This letter contains invalid edit data.",
      );
    const project = findProject(readAll(), projectId);
    const original = project.glyphs.find((item) => item.id === glyph.id);
    if (!original)
      throw new LocalProjectError(
        "not-found",
        "This letter is no longer available in the draft.",
      );
    const next = { ...glyph, sourceCharacter: original.sourceCharacter };
    updateProject(projectId, {
      glyphs: project.glyphs.map((item) =>
        item.id === glyph.id ? next : item,
      ),
    });
    return next;
  },
};

export const fontService: FontService = {
  async suggestStyle(projectId, instruction) {
    await pause(350);
    const project = findProject(readAll(), projectId);
    const words = instruction.toLowerCase();
    const style: FontStyle = { ...project.style };
    if (/\b(bold|bolder|heavy|heavier|thick|thicker|dark|darker)\b/.test(words))
      style.thickness = 75;
    if (/\b(light|lighter|thin|thinner|delicate)\b/.test(words))
      style.thickness = 30;
    if (/\b(slant|slanted|italic|lean)\b/.test(words)) style.slant = 18;
    if (/\b(wide|wider|airy|spaced)\b/.test(words)) style.spacing = 73;
    if (/\b(smooth|smoother|neat|neater|clean|cleaner)\b/.test(words))
      style.smoothness = 78;
    if (/\b(narrow|narrower|compact)\b/.test(words)) style.proportion = 32;
    const changed = Object.keys(style).some(
      (key) =>
        style[key as keyof FontStyle] !== project.style[key as keyof FontStyle],
    );
    return {
      job: job(
        "style-suggest",
        "completed",
        changed
          ? "A reversible local preset is ready. No model was called."
          : "No matching change was found. Try bold, light, slanted, airy, smooth, or narrow. No model was called.",
      ),
      style,
    };
  },
  async validate(projectId) {
    const project = findProject(readAll(), projectId);
    const present = new Set(
      project.glyphs
        .filter((item) => item.status !== "missing")
        .map((item) => item.character),
    );
    const required = new Set(
      [...DEMO_CHARS, ...project.previewText].filter(
        (value) => !/\s/.test(value),
      ),
    );
    const missing = [...required].filter((value) => !present.has(value));
    const warnings: string[] = [];
    if (!project.glyphs.length)
      warnings.push(
        "Prepare the demo alphabet in Review before checking character coverage.",
      );
    if (
      project.glyphs.some(
        (item) =>
          item.status === "needs-review" ||
          item.status === "detected" ||
          item.status === "edited",
      )
    )
      warnings.push("Some demo letters have not been approved yet.");
    if (
      new Set(project.glyphs.map((item) => item.character)).size <
      project.glyphs.length
    )
      warnings.push(
        "Two or more glyphs are assigned to the same character. Check the assignments in Review.",
      );
    warnings.push(
      "Coverage is a demo check. Real font outlines, spacing, and export validity have not been checked.",
    );
    return { missing, warnings };
  },
  async requestExport(projectId, formats) {
    findProject(readAll(), projectId);
    if (
      !formats.length ||
      formats.some((format) => !["ttf", "otf", "woff2"].includes(format))
    )
      return job(
        "generate-font",
        "needs-input",
        "Choose at least one supported format to explore the demo export flow.",
        "FORMAT_REQUIRED",
      );
    return job(
      "generate-font",
      "needs-input",
      "Real font generation is not connected in this demo. No file was created.",
      "FONT_GENERATION_UNAVAILABLE",
    );
  },
};

export const guestPolicy = { displayAllowance: 3, enforced: false } as const;
