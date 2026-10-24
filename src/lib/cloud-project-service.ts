import { createOwnerTokenProvider } from "./owner-token";
/** Opt-in metadata persistence for cloud projects. It never sends demo glyph data. */
import {
  createProjectsClient,
  ProjectsResponseError,
} from "../../packages/contracts/src/client";
import type { components } from "../../packages/contracts/src/generated";
import type { IdentityService } from "./auth-service";
import { mapProject, type CloudProjectView } from "./api-boundary";

type ProjectCreate = components["schemas"]["ProjectCreate"];
type ProjectPatch = components["schemas"]["ProjectPatch"];
type StyleControls = NonNullable<ProjectPatch["style_controls"]>;

export type CloudProjectEditor = {
  id: string;
  baseProject: CloudProjectView;
  name: string;
  controls: StyleControls;
  originalName: string;
  originalControls: StyleControls;
};

/** This snapshot belongs to the editor, never to a subsequently refreshed list row. */
export function openCloudProjectEditor(
  project: CloudProjectView,
): CloudProjectEditor {
  const controls: StyleControls = {
    thickness: project.styleControls.thickness ?? 50,
    slant: project.styleControls.slant ?? 0,
    spacing: project.styleControls.spacing ?? 50,
    smoothness: project.styleControls.smoothness ?? 50,
    proportion: project.styleControls.proportion ?? 50,
  };
  return {
    id: project.id,
    baseProject: { ...project, styleControls: { ...project.styleControls } },
    name: project.name,
    originalName: project.name,
    controls: { ...controls },
    originalControls: controls,
  };
}

export function cloudProjectEditorPatch(
  editor: CloudProjectEditor,
): ProjectPatch {
  const patch: ProjectPatch = {};
  if (editor.name.trim() !== editor.originalName)
    patch.name = editor.name.trim();
  const controls: StyleControls = {};
  for (const key of Object.keys(editor.controls) as Array<
    keyof StyleControls
  >) {
    if (editor.controls[key] !== editor.originalControls[key])
      controls[key] = editor.controls[key];
  }
  if (Object.keys(controls).length > 0) patch.style_controls = controls;
  return patch;
}

const CREATE_RECOVERY_KEY = "penforge-cloud-create-recovery-v1";
const CHANGE_RECOVERY_KEY = "penforge-cloud-change-recovery-v1";
const CREATE_RECOVERY_VERSION = 1;
const OPERATION_PREFIX = "penforge-cloud-operation-v2:";

export type CloudPendingCreate = {
  schemaVersion: 1;
  ownerUid: string;
  idempotencyKey: string;
  body: ProjectCreate;
};

export class CloudProjectError extends Error {
  constructor(
    public readonly code:
      | "recovery_unavailable"
      | "pending_create_mismatch"
      | "pending_create_identity_mismatch"
      | "local_conflict_backup_unavailable"
      | "network_unavailable"
      | "not_signed_in",
    public readonly recovery?: CloudProjectConflict,
    public readonly pendingCreate?: CloudPendingCreate,
  ) {
    super(code);
    this.name = "CloudProjectError";
  }
}

export type CloudProjectConflict = {
  mode: "interrupted" | "conflict";
  recoveryId: string;
  ownerUid: string;
  projectId: string;
  localName: string;
  /** Exact metadata patch stays local until the owner chooses a resolution. */
  localChanges: ProjectPatch;
  serverProject: {
    name: string;
    rowVersion: number;
    updatedAt: string;
  } | null;
};
type SavedConflicts = { schemaVersion: 1; conflicts: CloudProjectConflict[] };

export type CloudProjectServiceState =
  "local-only" | "syncing" | "saved" | "conflict" | "offline";

function browserStorage(): Storage {
  try {
    if (typeof window !== "undefined") return window.localStorage;
  } catch {
    /* Storage availability is an explicit prerequisite for recoverable writes. */
  }
  throw new CloudProjectError("recovery_unavailable");
}

function newIdempotencyKey(): string {
  if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function")
    throw new CloudProjectError("recovery_unavailable");
  return crypto.randomUUID();
}

function isPendingCreate(value: unknown): value is CloudPendingCreate {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  if (
    record.schemaVersion !== CREATE_RECOVERY_VERSION ||
    typeof record.ownerUid !== "string" ||
    typeof record.idempotencyKey !== "string" ||
    !record.idempotencyKey ||
    !record.body ||
    typeof record.body !== "object" ||
    Array.isArray(record.body)
  )
    return false;
  const body = record.body as Record<string, unknown>;
  return (
    typeof body.name === "string" &&
    body.name.length >= 1 &&
    body.name.length <= 120 &&
    typeof body.script_code === "string" &&
    (body.style_controls === undefined ||
      (typeof body.style_controls === "object" &&
        body.style_controls !== null &&
        !Array.isArray(body.style_controls)))
  );
}

/** Recover the locally recorded request attached to any failed create response. */
export function pendingCreateFromError(
  error: unknown,
): CloudPendingCreate | null {
  if (!error || typeof error !== "object" || !("pendingCreate" in error))
    return null;
  return isPendingCreate(error.pendingCreate) ? error.pendingCreate : null;
}

function validConflict(value: unknown): value is CloudProjectConflict {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return (
    ["interrupted", "conflict"].includes(String(record.mode)) &&
    (record.recoveryId === undefined ||
      typeof record.recoveryId === "string") &&
    typeof record.ownerUid === "string" &&
    typeof record.projectId === "string" &&
    typeof record.localName === "string" &&
    record.localChanges !== null &&
    typeof record.localChanges === "object" &&
    !Array.isArray(record.localChanges)
  );
}

function normalizeConflict(
  conflict: Omit<CloudProjectConflict, "recoveryId"> & {
    recoveryId?: string;
  },
): CloudProjectConflict {
  return {
    ...conflict,
    recoveryId:
      conflict.recoveryId ||
      `legacy-${conflict.ownerUid}-${conflict.projectId}`,
  };
}

function storedConflicts(storage: Storage): CloudProjectConflict[] {
  const raw = storage.getItem(CHANGE_RECOVERY_KEY);
  if (raw === null) return [];
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new CloudProjectError("local_conflict_backup_unavailable");
  }
  if (validConflict(value)) return [normalizeConflict(value)];
  if (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).schemaVersion === 1 &&
    Array.isArray((value as Record<string, unknown>).conflicts) &&
    (value as { conflicts: unknown[] }).conflicts.every(validConflict)
  )
    return (value as SavedConflicts).conflicts.map(normalizeConflict);
  throw new CloudProjectError("local_conflict_backup_unavailable");
}

/** Each operation owns one key. No read/modify/write of a shared collection. */
function operationKey(
  kind: "create" | "change",
  ownerUid: string,
  id: string,
): string {
  return `${OPERATION_PREFIX}${kind}:${encodeURIComponent(ownerUid)}:${encodeURIComponent(id)}`;
}

function legacyDoneKey(
  kind: "create" | "change",
  ownerUid: string,
  id: string,
): string {
  return `${operationKey(kind, ownerUid, id)}:legacy-resolved`;
}

function operationValues(
  storage: Storage,
  kind: "create" | "change",
): unknown[] {
  const values: unknown[] = [];
  // Snapshot key names before reading values: a concurrent removal only removes its own record.
  const keys = Array.from({ length: storage.length }, (_, index) =>
    storage.key(index),
  );
  for (const key of keys) {
    if (
      !key?.startsWith(`${OPERATION_PREFIX}${kind}:`) ||
      key.endsWith(":legacy-resolved")
    )
      continue;
    const raw = storage.getItem(key);
    if (raw === null) continue;
    try {
      values.push(JSON.parse(raw));
    } catch {
      /* Keep malformed bytes untouched. */
    }
  }
  return values;
}

function pendingCreates(storage: Storage): CloudPendingCreate[] {
  const values = operationValues(storage, "create").filter(isPendingCreate);
  const raw = storage.getItem(CREATE_RECOVERY_KEY);
  if (raw !== null) {
    try {
      const legacy: unknown = JSON.parse(raw);
      if (
        isPendingCreate(legacy) &&
        storage.getItem(
          legacyDoneKey("create", legacy.ownerUid, legacy.idempotencyKey),
        ) === null &&
        !values.some(
          (item) =>
            item.ownerUid === legacy.ownerUid &&
            item.idempotencyKey === legacy.idempotencyKey,
        )
      )
        values.push(legacy);
    } catch {
      /* Preserve the original legacy bytes, including malformed records. */
    }
  }
  return values;
}

export function readCloudPendingCreates(
  ownerUid?: string,
): CloudPendingCreate[] {
  try {
    return pendingCreates(browserStorage()).filter(
      (item) => ownerUid === undefined || item.ownerUid === ownerUid,
    );
  } catch {
    return [];
  }
}

function writeConflict(conflict: CloudProjectConflict): void {
  try {
    browserStorage().setItem(
      operationKey("change", conflict.ownerUid, conflict.recoveryId),
      JSON.stringify(conflict),
    );
  } catch {
    throw new CloudProjectError("local_conflict_backup_unavailable");
  }
}

export function isCloudRecoveryStorageKey(key: string | null): boolean {
  return (
    key === null ||
    key === CREATE_RECOVERY_KEY ||
    key === CHANGE_RECOVERY_KEY ||
    key.startsWith(OPERATION_PREFIX)
  );
}

export function readCloudProjectConflicts(
  ownerUid?: string,
): CloudProjectConflict[] {
  try {
    const storage = browserStorage();
    const conflicts = operationValues(storage, "change")
      .filter(validConflict)
      .map(normalizeConflict);
    let legacy: CloudProjectConflict[] = [];
    try {
      legacy = storedConflicts(storage);
    } catch {
      /* Preserve invalid legacy bytes. */
    }
    for (const conflict of legacy) {
      if (
        storage.getItem(
          legacyDoneKey("change", conflict.ownerUid, conflict.recoveryId),
        ) === null &&
        !conflicts.some(
          (item) =>
            item.ownerUid === conflict.ownerUid &&
            item.recoveryId === conflict.recoveryId,
        )
      )
        conflicts.push(conflict);
    }
    return conflicts.filter(
      (conflict) => ownerUid === undefined || conflict.ownerUid === ownerUid,
    );
  } catch {
    return [];
  }
}

export function readCloudProjectConflict(
  ownerUid?: string,
  recoveryId?: string,
): CloudProjectConflict | null {
  const conflicts = readCloudProjectConflicts(ownerUid);
  return (
    (recoveryId
      ? conflicts.find((conflict) => conflict.recoveryId === recoveryId)
      : conflicts[0]) ?? null
  );
}

function removeOperation(
  kind: "create" | "change",
  ownerUid: string,
  id: string,
): void {
  const storage = browserStorage();
  // Legacy arrays/singletons are immutable migration evidence. A per-operation marker prevents resurrection.
  if (
    storage.getItem(
      kind === "create" ? CREATE_RECOVERY_KEY : CHANGE_RECOVERY_KEY,
    ) !== null
  )
    storage.setItem(legacyDoneKey(kind, ownerUid, id), "1");
  storage.removeItem(operationKey(kind, ownerUid, id));
}

function removeProjectConflict(ownerUid: string, recoveryId: string): void {
  removeOperation("change", ownerUid, recoveryId);
}

function projectConflict(
  ownerUid: string,
  projectId: string,
  localChanges: ProjectPatch,
  recoveryId: string,
  mode: CloudProjectConflict["mode"],
  fallbackName: string,
  error?: ProjectsResponseError,
): CloudProjectConflict {
  const current = error?.context?.current_project;
  const currentName =
    current &&
    typeof current === "object" &&
    !Array.isArray(current) &&
    typeof (current as Record<string, unknown>).name === "string"
      ? ((current as Record<string, unknown>).name as string)
      : "";
  const localName = localChanges.name ?? (currentName || fallbackName);
  if (!current || typeof current !== "object" || Array.isArray(current))
    return {
      mode,
      recoveryId,
      ownerUid,
      projectId,
      localName,
      localChanges,
      serverProject: null,
    };
  const project = current as Record<string, unknown>;
  return {
    mode,
    recoveryId,
    ownerUid,
    projectId,
    localName,
    localChanges,
    serverProject:
      typeof project.name === "string" &&
      typeof project.row_version === "number" &&
      typeof project.updated_at === "string"
        ? {
            name: project.name,
            rowVersion: project.row_version,
            updatedAt: project.updated_at,
          }
        : null,
  };
}

function isOffline(error: unknown): boolean {
  return (
    (typeof navigator !== "undefined" && navigator.onLine === false) ||
    error instanceof TypeError
  );
}

export class CloudProjectService {
  constructor(
    private readonly client: ReturnType<typeof createProjectsClient>,
    private readonly ownerUid: string = "test-owner",
  ) {}

  async list(): Promise<CloudProjectView[]> {
    const result: CloudProjectView[] = [];
    let cursor: string | undefined;
    do {
      const page = await this.client.list({ limit: 100, cursor });
      result.push(...page.items.map(mapProject));
      cursor = page.next_cursor ?? undefined;
      if (result.length > 10000) throw new Error("project_page_limit");
    } while (cursor);
    return result;
  }

  async get(id: string): Promise<CloudProjectView> {
    return mapProject((await this.client.get(id)).project);
  }

  async create(
    name: string,
    scriptCode = "Latn",
  ): Promise<{ project: CloudProjectView; replayed: boolean }> {
    const pending: CloudPendingCreate = {
      schemaVersion: CREATE_RECOVERY_VERSION,
      ownerUid: this.ownerUid,
      idempotencyKey: newIdempotencyKey(),
      body: { name: name.trim(), script_code: scriptCode, style_controls: {} },
    };
    try {
      browserStorage().setItem(
        operationKey("create", pending.ownerUid, pending.idempotencyKey),
        JSON.stringify(pending),
      );
    } catch {
      throw new CloudProjectError("recovery_unavailable");
    }
    return this.sendPendingCreate(pending);
  }

  async retryCreate(
    recovery: CloudPendingCreate,
  ): Promise<{ project: CloudProjectView; replayed: boolean }> {
    if (recovery.ownerUid !== this.ownerUid)
      throw new CloudProjectError("pending_create_identity_mismatch");
    const pending = pendingCreates(browserStorage()).find(
      (item) =>
        item.ownerUid === this.ownerUid &&
        item.idempotencyKey === recovery.idempotencyKey,
    );
    if (!pending) throw new CloudProjectError("recovery_unavailable");
    return this.sendPendingCreate(pending);
  }

  private async sendPendingCreate(
    pending: CloudPendingCreate,
  ): Promise<{ project: CloudProjectView; replayed: boolean }> {
    try {
      const result = await this.client.create(
        pending.body,
        pending.idempotencyKey,
      );
      try {
        removeOperation("create", pending.ownerUid, pending.idempotencyKey);
      } catch {
        /* Confirmed server response remains authoritative. */
      }
      return { project: mapProject(result.project), replayed: result.replayed };
    } catch (error) {
      // Even auth/claim errors cannot prove an earlier interrupted request did not commit.
      if (isOffline(error))
        throw new CloudProjectError("network_unavailable", undefined, pending);
      if (error && typeof error === "object") {
        try {
          Object.defineProperty(error, "pendingCreate", { value: pending });
        } catch {
          /* Stored evidence remains available. */
        }
      }
      throw error;
    }
  }

  async update(
    project: CloudProjectView,
    changes: ProjectPatch,
  ): Promise<CloudProjectView> {
    return this.updateWithRecovery(project, changes, newIdempotencyKey());
  }

  private async updateWithRecovery(
    project: CloudProjectView,
    changes: ProjectPatch,
    recoveryId: string,
  ): Promise<CloudProjectView> {
    const localChanges: ProjectPatch = { ...changes };
    if (typeof changes.name === "string")
      localChanges.name = changes.name.trim();
    writeConflict(
      projectConflict(
        this.ownerUid,
        project.id,
        localChanges,
        recoveryId,
        "interrupted",
        project.name,
      ),
    );
    try {
      const result = await this.client.update(
        project.id,
        changes,
        `"v${project.rowVersion}"`,
      );
      try {
        removeProjectConflict(this.ownerUid, recoveryId);
      } catch {
        /* The successful cloud write is authoritative; recovery cleanup is best effort. */
      }
      return mapProject(result.project);
    } catch (error) {
      if (error instanceof ProjectsResponseError && error.status === 412) {
        const conflict = projectConflict(
          this.ownerUid,
          project.id,
          localChanges,
          recoveryId,
          "conflict",
          project.name,
          error,
        );
        writeConflict(conflict);
        throw Object.assign(new Error("revision_conflict"), {
          code: "revision_conflict",
          conflict,
        });
      }
      const recovery = projectConflict(
        this.ownerUid,
        project.id,
        localChanges,
        recoveryId,
        "interrupted",
        project.name,
      );
      if (
        error instanceof ProjectsResponseError &&
        (error.status === 400 || error.status === 422)
      ) {
        removeProjectConflict(this.ownerUid, recoveryId);
        throw error;
      }
      writeConflict(recovery);
      if (isOffline(error))
        throw new CloudProjectError("network_unavailable", recovery);
      if (error && typeof error === "object") {
        try {
          Object.defineProperty(error, "recovery", { value: recovery });
        } catch {
          /* Recovery remains stored even if this error cannot carry the ID. */
        }
      }
      throw error;
    }
  }

  async saveResolvedConflict(
    conflict: CloudProjectConflict,
    useLocalChanges: boolean,
  ): Promise<CloudProjectView> {
    if (conflict.ownerUid !== this.ownerUid)
      throw new CloudProjectError("pending_create_identity_mismatch");
    const current = await this.get(conflict.projectId);
    if (!useLocalChanges) {
      removeProjectConflict(this.ownerUid, conflict.recoveryId);
      return current;
    }
    if (patchMatchesProject(current, conflict.localChanges)) {
      removeProjectConflict(this.ownerUid, conflict.recoveryId);
      return current;
    }
    return this.updateWithRecovery(
      current,
      conflict.localChanges,
      conflict.recoveryId,
    );
  }

  async requestDeletion(project: CloudProjectView): Promise<void> {
    await this.client.requestDeletion(project.id, `"v${project.rowVersion}"`);
  }
}

function patchMatchesProject(
  project: CloudProjectView,
  patch: ProjectPatch,
): boolean {
  if (patch.name !== undefined && patch.name !== project.name) return false;
  if (patch.style_controls) {
    for (const [key, value] of Object.entries(patch.style_controls)) {
      if (
        value !==
        project.styleControls[
          key as keyof NonNullable<ProjectPatch["style_controls"]>
        ]
      )
        return false;
    }
  }
  return true;
}

export function createCloudProjectService(
  identity: IdentityService,
): CloudProjectService {
  if (identity.configuration.state !== "configured")
    throw new CloudProjectError("not_signed_in");
  const ownerUid = identity.getSnapshot().identity?.firebase_uid;
  if (!ownerUid) throw new CloudProjectError("not_signed_in");
  return new CloudProjectService(
    createProjectsClient(
      identity.configuration.apiOrigin,
      createOwnerTokenProvider(
        identity,
        ownerUid,
        () => new CloudProjectError("not_signed_in"),
      ),
    ),
    ownerUid,
  );
}

export const cloudImportUnavailableReason =
  "Import is unavailable because local drafts include handwriting and glyph revisions that this metadata-only service cannot preserve yet.";

export const cloudDuplicateUnavailableReason =
  "Cloud duplication is unavailable until project snapshots and lineage can be copied atomically.";
