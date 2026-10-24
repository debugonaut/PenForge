import { createOwnerTokenProvider } from "./owner-token";
/**
 * Account-backed projects: debounced autosave, per-operation recovery records and
 * explicit conflict handling. Demo drafts never pass through this module unless a
 * person explicitly imports one.
 */
import {
  CloudProjectError,
  CloudProjectService,
  createCloudProjectService,
} from "./cloud-project-service";
import type { IdentityService } from "./auth-service";
import type { CloudProjectView } from "./api-boundary";
import {
  createWorkspaceClient,
  emptyWorkspace,
  projectToWorkspace,
  workspaceToProject,
  WorkspaceError,
  type AccountWorkspace,
  type LoadedWorkspace,
} from "./account-workspace";
import type { FontProject } from "./contracts";

export type SaveState =
  "idle" | "saving" | "saved" | "offline" | "error" | "conflict";

export type SaveSnapshot = {
  state: SaveState;
  message: string;
  pending: number;
};

type PendingWrite = {
  schemaVersion: 1;
  ownerUid: string;
  projectId: string;
  operationId: string;
  baseVersion: number;
  workspace: AccountWorkspace;
  createdAt: string;
};

const OPERATION_PREFIX = "penforge-workspace-op-v1:";
export const WORKSPACE_RECOVERY_PREFIX = OPERATION_PREFIX;
const SAVE_DELAY_MS = 700;

export function isWorkspaceRecoveryKey(key: string | null): boolean {
  return key === null || key.startsWith(OPERATION_PREFIX);
}

type StorageLike = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem" | "key" | "length"
>;

function browserStorage(): StorageLike | null {
  try {
    if (typeof window !== "undefined") return window.localStorage;
  } catch {
    /* Storage can be denied; saves then fail closed instead of pretending. */
  }
  return null;
}

function operationKey(
  ownerUid: string,
  projectId: string,
  operationId: string,
) {
  return `${OPERATION_PREFIX}${encodeURIComponent(ownerUid)}:${encodeURIComponent(projectId)}:${encodeURIComponent(operationId)}`;
}

function readRecords(
  storage: StorageLike | null,
  ownerUid: string,
  projectId: string,
): PendingWrite[] {
  if (!storage) return [];
  const keys: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key?.startsWith(OPERATION_PREFIX)) keys.push(key);
  }
  const records: PendingWrite[] = [];
  for (const key of keys) {
    const raw = storage.getItem(key);
    if (raw === null) continue;
    try {
      const value: unknown = JSON.parse(raw);
      if (
        value &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        (value as PendingWrite).schemaVersion === 1 &&
        (value as PendingWrite).ownerUid === ownerUid &&
        (value as PendingWrite).projectId === projectId &&
        typeof (value as PendingWrite).operationId === "string" &&
        typeof (value as PendingWrite).baseVersion === "number" &&
        (value as PendingWrite).workspace
      )
        records.push(value as PendingWrite);
    } catch {
      /* Malformed records are preserved for inspection, never replayed. */
    }
  }
  return records.sort((left, right) =>
    left.createdAt.localeCompare(right.createdAt),
  );
}

export function readPendingWorkspaceWrites(
  ownerUid?: string,
  projectId?: string,
  storage: StorageLike | null = browserStorage(),
): PendingWrite[] {
  if (!storage) return [];
  const keys: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key?.startsWith(OPERATION_PREFIX)) keys.push(key);
  }
  const all: PendingWrite[] = [];
  for (const key of keys) {
    const raw = storage.getItem(key);
    if (raw === null) continue;
    try {
      const value = JSON.parse(raw) as PendingWrite;
      if (value?.schemaVersion !== 1 || typeof value.ownerUid !== "string")
        continue;
      if (ownerUid !== undefined && value.ownerUid !== ownerUid) continue;
      if (projectId !== undefined && value.projectId !== projectId) continue;
      all.push(value);
    } catch {
      /* ignore malformed */
    }
  }
  return all;
}

/** Top-level three-way merge; unrelated remote edits survive a local retry. */
export function mergeWorkspace(
  base: AccountWorkspace | null,
  local: AccountWorkspace,
  remote: AccountWorkspace,
): { merged: AccountWorkspace; overlapping: string[] } {
  const merged: AccountWorkspace = { ...remote };
  const overlapping: string[] = [];
  const keys = Object.keys(local) as Array<keyof AccountWorkspace>;
  for (const key of keys) {
    if (key === "style" || key === "review") continue;
    const localChanged =
      JSON.stringify(local[key]) !== JSON.stringify(base?.[key]);
    if (!localChanged) continue;
    const remoteChanged =
      JSON.stringify(remote[key]) !== JSON.stringify(base?.[key]);
    if (remoteChanged) overlapping.push(String(key));
    // Explicit local intent wins once the person confirms the merge.
    Object.assign(merged, { [key]: local[key] });
  }
  const styleKeys = Object.keys(local.style) as Array<
    keyof AccountWorkspace["style"]
  >;
  const style = { ...remote.style };
  for (const key of styleKeys) {
    if (local.style[key] === base?.style?.[key]) continue;
    if (remote.style[key] !== base?.style?.[key])
      overlapping.push(`style.${String(key)}`);
    style[key] = local.style[key];
  }
  merged.style = style;
  merged.review = {
    approvedGlyphIds: local.review.approvedGlyphIds.length
      ? local.review.approvedGlyphIds
      : remote.review.approvedGlyphIds,
    assignments: local.review.assignments.length
      ? local.review.assignments
      : remote.review.assignments,
  };
  return { merged, overlapping };
}

export type AccountProject = {
  project: CloudProjectView;
  view: FontProject;
  version: number;
};

export class AccountProjectService {
  constructor(
    private readonly cloud: CloudProjectService,
    private readonly workspaceClient: ReturnType<typeof createWorkspaceClient>,
    readonly ownerUid: string,
    private readonly storage: StorageLike | null = browserStorage(),
  ) {}

  async list(): Promise<CloudProjectView[]> {
    return this.cloud.list();
  }

  async create(
    name: string,
    scriptCode: "Latn" | "Deva" = "Latn",
  ): Promise<AccountProject> {
    const { project } = await this.cloud.create(name, scriptCode);
    const workspace = emptyWorkspace(scriptCode);
    try {
      const saved = await this.workspaceClient.put(project.id, workspace, 0);
      return {
        project: { ...project, rowVersion: project.rowVersion },
        view: workspaceToProject(
          project.id,
          project.name,
          saved.updatedAt,
          this.ownerUid,
          saved.version,
          saved.workspace,
        ),
        version: saved.version,
      };
    } catch (cause) {
      // The metadata project exists. Opening it later creates the workspace, so
      // no retry creates a duplicate project.
      if (cause instanceof WorkspaceError) throw cause;
      throw cause;
    }
  }

  async open(projectId: string): Promise<AccountProject> {
    const project = await this.cloud.get(projectId);
    const loaded: LoadedWorkspace | null =
      await this.workspaceClient.get(projectId);
    if (loaded === null) {
      return {
        project,
        view: workspaceToProject(
          project.id,
          project.name,
          project.updatedAt,
          this.ownerUid,
          0,
          emptyWorkspace(project.scriptCode === "Deva" ? "Deva" : "Latn"),
        ),
        version: 0,
      };
    }
    return {
      project,
      view: workspaceToProject(
        project.id,
        project.name,
        loaded.updatedAt,
        this.ownerUid,
        loaded.version,
        loaded.workspace,
      ),
      version: loaded.version,
    };
  }

  async rename(
    project: CloudProjectView,
    name: string,
  ): Promise<CloudProjectView> {
    return this.cloud.update(project, { name: name.trim() });
  }

  async remove(project: CloudProjectView): Promise<void> {
    return this.cloud.requestDeletion(project);
  }

  async workspace(projectId: string): Promise<LoadedWorkspace | null> {
    return this.workspaceClient.get(projectId);
  }

  pending(projectId: string): PendingWrite[] {
    return readRecords(this.storage, this.ownerUid, projectId);
  }

  saver(
    projectId: string,
    initialVersion: number,
    callbacks: {
      onState: (snapshot: SaveSnapshot) => void;
      onSaved: (version: number, updatedAt: string) => void;
      onRemote?: (remote: AccountWorkspace, version: number) => void;
    },
  ): WorkspaceSaver {
    return new WorkspaceSaver({
      client: this.workspaceClient,
      ownerUid: this.ownerUid,
      projectId,
      initialVersion,
      storage: this.storage,
      ...callbacks,
    });
  }

  /** Explicit import: a new account project receives draft content, not local bytes. */
  async importDemoProject(demo: FontProject): Promise<AccountProject> {
    const created = await this.create(demo.name, "Latn");
    const workspace = projectToWorkspace({
      ...demo,
      id: created.project.id,
      origin: {
        kind: "account",
        ownerUid: this.ownerUid,
        workspaceVersion: created.version,
      },
    });
    // Local upload previews are deliberately not carried into private storage.
    if (workspace.sample?.kind === "upload") {
      workspace.sample = {
        kind: "upload",
        strokes: [],
        ...(demo.sample?.fileName ? { fileName: demo.sample.fileName } : {}),
      };
    }
    const saved = await this.workspaceClient.put(
      created.project.id,
      workspace,
      created.version,
    );
    return {
      project: created.project,
      view: workspaceToProject(
        created.project.id,
        created.project.name,
        saved.updatedAt,
        this.ownerUid,
        saved.version,
        saved.workspace,
      ),
      version: saved.version,
    };
  }
}

export class WorkspaceSaver {
  private version: number;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight = false;
  private queued = false;
  private disposed = false;
  private project: FontProject | null = null;
  private base: AccountWorkspace | null = null;
  private conflict: {
    local: AccountWorkspace;
    remote: AccountWorkspace;
    version: number;
  } | null = null;

  constructor(
    private readonly options: {
      client: ReturnType<typeof createWorkspaceClient>;
      ownerUid: string;
      projectId: string;
      initialVersion: number;
      storage: StorageLike | null;
      onState: (snapshot: SaveSnapshot) => void;
      onSaved: (version: number, updatedAt: string) => void;
      onRemote?: (remote: AccountWorkspace, version: number) => void;
    },
  ) {
    this.version = options.initialVersion;
  }

  get currentVersion(): number {
    return this.version;
  }

  schedule(project: FontProject) {
    if (this.disposed || project.origin.kind !== "account") return;
    this.project = project;
    this.conflict = null;
    this.emit("saving", "Saving…");
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, SAVE_DELAY_MS);
  }

  async flush(): Promise<void> {
    if (this.disposed) return;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.inFlight) {
      this.queued = true;
      return;
    }
    const project = this.project;
    if (!project) return;
    this.inFlight = true;
    const workspace = projectToWorkspace(project);
    try {
      const saved = await this.send(workspace);
      if (this.disposed) return;
      this.emitSaved(saved.version, saved.updatedAt);
    } catch (cause) {
      if (this.disposed) return;
      await this.handleFailure(cause, workspace);
    } finally {
      this.inFlight = false;
      if (this.queued && !this.disposed) {
        this.queued = false;
        void this.flush();
      }
    }
  }

  private async send(workspace: AccountWorkspace) {
    const record = this.record(workspace);
    const saved = await this.options.client.put(
      this.options.projectId,
      workspace,
      record.baseVersion,
    );
    // Also retire an older identical intent left by a failed attempt.
    this.removeConfirmed(workspace);
    this.base = workspace;
    return saved;
  }

  private record(workspace: AccountWorkspace): PendingWrite {
    const value: PendingWrite = {
      schemaVersion: 1,
      ownerUid: this.options.ownerUid,
      projectId: this.options.projectId,
      operationId: crypto.randomUUID(),
      baseVersion: this.version,
      workspace,
      createdAt: new Date().toISOString(),
    };
    try {
      this.options.storage?.setItem(
        operationKey(value.ownerUid, value.projectId, value.operationId),
        JSON.stringify(value),
      );
    } catch {
      throw new WorkspaceError("recovery_unavailable");
    }
    return value;
  }

  private removeRecord(operationId: string) {
    try {
      this.options.storage?.removeItem(
        operationKey(
          this.options.ownerUid,
          this.options.projectId,
          operationId,
        ),
      );
    } catch {
      /* The confirmed server write is authoritative. */
    }
  }

  private async handleFailure(cause: unknown, workspace: AccountWorkspace) {
    if (cause instanceof WorkspaceError && cause.code === "revision_conflict") {
      const remote = await this.options.client
        .get(this.options.projectId)
        .catch(() => null);
      if (!remote) {
        this.emit("error", "Couldn’t save — retry");
        return;
      }
      const base = this.base ?? null;
      const { merged, overlapping } = mergeWorkspace(
        base,
        workspace,
        remote.workspace,
      );
      if (overlapping.length === 0) {
        // No overlapping field changed: rebase automatically and keep both edits.
        this.version = remote.version;
        this.emit("saving", "Saving…");
        try {
          const saved = await this.options.client.put(
            this.options.projectId,
            merged,
            remote.version,
          );
          if (this.disposed) return;
          this.removeConfirmed(workspace);
          this.emitSaved(saved.version, saved.updatedAt);
          return;
        } catch (retry) {
          if (
            retry instanceof WorkspaceError &&
            retry.code === "revision_conflict"
          ) {
            /* fall through to explicit conflict below */
          } else {
            this.emitSaveFailure(retry);
            return;
          }
        }
      }
      this.conflict = {
        local: workspace,
        remote: remote.workspace,
        version: remote.version,
      };
      this.version = remote.version;
      this.options.onRemote?.(remote.workspace, remote.version);
      this.emit(
        "conflict",
        overlapping.length
          ? "This project changed in another tab. Choose which version to keep."
          : "This project changed elsewhere. Review your changes.",
      );
      return;
    }
    this.emitSaveFailure(cause);
  }

  private emitSaveFailure(cause: unknown) {
    if (cause instanceof WorkspaceError) {
      if (
        [
          "terms_required",
          "account_required",
          "email_verification_required",
          "service_not_ready",
        ].includes(cause.code)
      ) {
        this.emit(
          "error",
          "Open Your account to finish setup — changes kept on this device.",
        );
        return;
      }
      if (cause.code === "network_unavailable") {
        this.emit("offline", "Offline — changes kept on this device");
        return;
      }
      if (cause.code === "unauthenticated") {
        this.emit(
          "error",
          "Session expired. Sign in again — changes kept on this device.",
        );
        return;
      }
      if (cause.code === "not_found" || cause.code === "state_conflict") {
        this.emit("error", "This project was deleted in another session.");
        return;
      }
      if (cause.code === "recovery_unavailable") {
        this.emit(
          "error",
          "Couldn’t keep a recovery copy, so nothing was sent.",
        );
        return;
      }
      if (cause.code === "validation_error") {
        this.emit(
          "error",
          "This draft could not be saved. Keep this page open and retry.",
        );
        return;
      }
    }
    this.emit("error", "Couldn’t save — retry");
  }

  /** Remove only records this saver superseded; another tab's backup survives. */
  private removeConfirmed(workspace: AccountWorkspace) {
    const confirmed = JSON.stringify(workspace);
    for (const record of readRecords(
      this.options.storage,
      this.options.ownerUid,
      this.options.projectId,
    ))
      if (JSON.stringify(record.workspace) === confirmed)
        this.removeRecord(record.operationId);
  }

  private emit(state: SaveState, message: string) {
    const pending = readRecords(
      this.options.storage,
      this.options.ownerUid,
      this.options.projectId,
    ).length;
    if (!this.disposed) this.options.onState({ state, message, pending });
  }

  private emitSaved(version: number, updatedAt: string) {
    this.version = version;
    this.options.onSaved(version, updatedAt);
    this.emit("saved", "Saved to your account");
  }

  /** Explicit conflict resolution. Saved version discards only local draft text. */
  async resolve(choice: "mine" | "saved"): Promise<void> {
    const conflict = this.conflict;
    if (!conflict) return;
    this.conflict = null;
    if (choice === "saved") {
      this.removeConfirmed(conflict.local);
      this.base = conflict.remote;
      this.options.onRemote?.(conflict.remote, conflict.version);
      this.emit("saved", "Using the saved version");
      return;
    }
    try {
      this.emit("saving", "Saving…");
      const saved = await this.options.client.put(
        this.options.projectId,
        conflict.local,
        conflict.version,
      );
      this.removeConfirmed(conflict.local);
      this.base = conflict.local;
      this.emitSaved(saved.version, saved.updatedAt);
    } catch (cause) {
      this.emitSaveFailure(cause);
    }
  }

  /** Retry exact stored bodies for this owner/project, oldest first. */
  async retryPending(): Promise<void> {
    const records = readRecords(
      this.options.storage,
      this.options.ownerUid,
      this.options.projectId,
    );
    for (const record of records) {
      try {
        const saved = await this.options.client.put(
          this.options.projectId,
          record.workspace,
          record.baseVersion,
        );
        this.removeRecord(record.operationId);
        this.version = saved.version;
        this.base = record.workspace;
      } catch (cause) {
        if (
          cause instanceof WorkspaceError &&
          cause.code === "revision_conflict"
        ) {
          // Reconcile before any replacement write.
          const remote = await this.options.client
            .get(this.options.projectId)
            .catch(() => null);
          if (remote) {
            const { merged } = mergeWorkspace(
              this.base,
              record.workspace,
              remote.workspace,
            );
            const saved = await this.options.client.put(
              this.options.projectId,
              merged,
              remote.version,
            );
            this.removeRecord(record.operationId);
            this.version = saved.version;
            this.base = merged;
            continue;
          }
        }
        this.emitSaveFailure(cause);
        return;
      }
    }
    this.emit("saved", "Saved to your account");
  }

  dispose() {
    this.disposed = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}

export function createAccountProjectService(
  identity: IdentityService,
): AccountProjectService {
  const uid = identity.getSnapshot().identity?.firebase_uid;
  if (identity.configuration.state !== "configured" || !uid)
    throw new CloudProjectError("not_signed_in");
  const cloud = createCloudProjectService(identity);
  const workspace = createWorkspaceClient(
    identity.configuration.apiOrigin,
    createOwnerTokenProvider(
      identity,
      uid,
      () => new WorkspaceError("identity_mismatch"),
    ),
  );
  return new AccountProjectService(cloud, workspace, uid);
}
