import { createTypeScriptLoader, javascriptModuleUrl as shim } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

const transpileUrl = createTypeScriptLoader(import.meta.url);

const transportUrl = await transpileUrl("../packages/contracts/src/client.ts");
const requestUrl = await transpileUrl("../src/lib/account-request.ts", [["../../packages/contracts/src/client", transportUrl]]);
const writingShim = shim("export const normalizeWritingDocument = (value) => value;");
const documentsUrl = await transpileUrl("../src/lib/account-documents.ts", [
  ["../../packages/contracts/src/client", transportUrl],
  ["./account-request", requestUrl],
  ["./writing", writingShim],
]);
const workspaceShimUrl = shim(`
export class WorkspaceError extends Error {
  constructor(code) { super(code); this.code = code; this.name = "WorkspaceError"; }
}
export const createWorkspaceClient = () => { throw new Error("unused"); };
export const emptyWorkspace = () => ({ step: "capture" });
export const projectToWorkspace = (project) => project.workspace;
export const workspaceToProject = () => { throw new Error("unused"); };
`);
const cloudShim = shim(`
export class CloudProjectError extends Error {
  constructor(code) { super(code); this.code = code; this.name = "CloudProjectError"; }
}
export class CloudProjectService {}
export const createCloudProjectService = () => { throw new Error("unused"); };
`);
const ownerTokenUrl = await transpileUrl("../src/lib/owner-token.ts");
const accountUrl = await transpileUrl("../src/lib/account-project-service.ts", [
  ["./owner-token", ownerTokenUrl],
  ["./cloud-project-service", cloudShim],
  ["./account-workspace", workspaceShimUrl],
]);
const documents = await import(documentsUrl);
const account = await import(accountUrl);
const { WorkspaceError } = await import(workspaceShimUrl);

function storage() {
  const values = new Map();
  return {
    values,
    get length() {
      return values.size;
    },
    key(index) {
      return [...values.keys()][index] ?? null;
    },
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

const page = {
  format: "letter",
  paper: "cotton",
  title: "To Priya",
  recipient: "Dear Priya,",
  body: "Thinking of you.",
  signature: "With love,",
  ink: "#192e35",
  accent: "#d7836f",
  fontSize: 34,
  lineHeight: 1.4,
  alignment: "left",
  weight: 420,
  spacing: 0.2,
  slant: 4,
  widthScale: 0.95,
  projectId: "11111111-1111-4111-8111-111111111111",
  generatedExportId: "22222222-2222-4222-8222-222222222222",
};

test("writing documents keep their project and generated-export references", () => {
  const body = documents.writingBody(page);
  assert.equal(body.project_id, page.projectId);
  assert.equal(body.generated_export_id, page.generatedExportId);
  const restored = documents.documentFromWire({
    id: "33333333-3333-4333-8333-333333333333",
    title: page.title,
    format: page.format,
    row_version: 3,
    updated_at: "2026-10-21T10:00:00Z",
    document: body,
  });
  assert.equal(restored.projectId, page.projectId);
  assert.equal(restored.generatedExportId, page.generatedExportId);
  assert.equal(restored.body, page.body);
});

test("a saved page is written once and its recovery record is cleared", async () => {
  const store = storage();
  const calls = [];
  const states = [];
  const saver = new documents.DocumentSaver({
    client: {
      update: async (id, document, format, version) => {
        calls.push({ id, document, format, version });
        return { row_version: version + 1 };
      },
    },
    ownerUid: "owner-a",
    documentId: "page-1",
    initialVersion: 4,
    storage: store,
    onState: (state, message) => states.push({ state, message }),
    onSaved: () => undefined,
  });
  saver.schedule(page, "letter");
  await saver.flush();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].version, 4);
  assert.equal(calls[0].document.generatedExportId, page.generatedExportId);
  assert.deepEqual(states.at(-1), { state: "saved", message: "Saved to your account" });
  assert.equal(documents.hasPendingDocument("owner-a", "page-1", store), false);
});

test("an interrupted page save keeps the exact body and replays it", async () => {
  const store = storage();
  const sent = [];
  let fail = true;
  const states = [];
  const saver = new documents.DocumentSaver({
    client: {
      update: async (_id, document, format, version) => {
        sent.push({ document, format, version });
        if (fail) throw new documents.DocumentError("network_unavailable");
        return { row_version: version + 1 };
      },
    },
    ownerUid: "owner-a",
    documentId: "page-2",
    initialVersion: 7,
    storage: store,
    onState: (state, message) => states.push({ state, message }),
    onSaved: () => undefined,
  });
  saver.schedule(page, "letter");
  await saver.flush();
  assert.equal(states.at(-1).state, "offline");
  const key = [...store.values.keys()].find((item) => item.includes("page-2"));
  const record = JSON.parse(store.getItem(key));
  assert.equal(record.ownerUid, "owner-a");
  assert.equal(record.version, 7);
  assert.equal(record.body.title, "To Priya");
  // A replacement save must not reuse a newer ETag than the failed attempt.
  fail = false;
  await saver.flush();
  assert.equal(sent[1].version, 7);
  assert.deepEqual(sent[1].document, sent[0].document);
  assert.equal(documents.hasPendingDocument("owner-a", "page-2", store), false);
});

test("pending page recovery is scoped to one identity", () => {
  const store = storage();
  store.setItem(documents.DOCUMENT_RECOVERY_PREFIX + "owner-a:page-3", "{}");
  assert.equal(documents.hasPendingDocument("owner-a", "page-3", store), true);
  assert.equal(documents.hasPendingDocument("owner-b", "page-3", store), false);
});

const workspace = (overrides = {}) => ({
  step: "capture",
  name: "Aurora hand",
  previewText: "hello",
  style: { thickness: 40, slant: 6, spacing: 50, proportion: 55 },
  review: { approvedGlyphIds: [], assignments: [] },
  ...overrides,
});

const projectOf = (value) => ({
  id: "project-1",
  origin: { kind: "account" },
  workspace: value,
});

function workspaceSaver({ store, client, states = [], saved = [] }) {
  return new account.WorkspaceSaver({
    client,
    ownerUid: "owner-a",
    projectId: "project-1",
    initialVersion: 2,
    storage: store,
    onState: (snapshot) => states.push(snapshot),
    onSaved: (version, updatedAt) => saved.push({ version, updatedAt }),
  });
}

test("a failed workspace save keeps its base revision for the retry", async () => {
  const store = storage();
  const versions = [];
  let fail = true;
  const states = [];
  const saver = workspaceSaver({
    store,
    states,
    client: {
      put: async (_id, value, version) => {
        versions.push(version);
        if (fail) throw new WorkspaceError("network_unavailable");
        return { version: version + 1, updatedAt: "2026-10-21T10:00:00Z" };
      },
    },
  });
  saver.schedule(projectOf(workspace({ previewText: "first" })));
  await saver.flush();
  assert.equal(states.at(-1).state, "offline");
  assert.equal(saver.currentVersion, 2);
  fail = false;
  await saver.flush();
  assert.deepEqual(versions, [2, 2]);
  assert.equal(saver.currentVersion, 3);
  assert.equal(states.at(-1).state, "saved");
  assert.equal(account.readPendingWorkspaceWrites("owner-a", "project-1", store).length, 0);
});

test("unrelated remote edits survive an automatic rebase", async () => {
  const store = storage();
  let conflict = false;
  const puts = [];
  const states = [];
  const saver = workspaceSaver({
    store,
    states,
    client: {
      put: async (_id, value, version) => {
        puts.push({ value, version });
        if (conflict) {
          conflict = false;
          throw new WorkspaceError("revision_conflict");
        }
        return { version: version + 1, updatedAt: "2026-10-21T10:00:00Z" };
      },
      get: async () => ({
        workspace: workspace({ name: "Renamed elsewhere" }),
        version: 9,
      }),
    },
  });
  // First save establishes this editor's base.
  saver.schedule(projectOf(workspace()));
  await saver.flush();
  assert.equal(saver.currentVersion, 3);
  conflict = true;
  saver.schedule(projectOf(workspace({ previewText: "mine" })));
  await saver.flush();
  assert.equal(puts.length, 3);
  assert.equal(puts[2].version, 9);
  assert.equal(puts[2].value.previewText, "mine");
  assert.equal(puts[2].value.name, "Renamed elsewhere");
  assert.equal(states.at(-1).state, "saved");
  assert.equal(saver.currentVersion, 10);
});

test("a same-field remote change asks for a choice and keeps both backups", async () => {
  const store = storage();
  const states = [];
  let attempt = 0;
  const saver = workspaceSaver({
    store,
    states,
    client: {
      put: async (_id, _value, version) => {
        attempt += 1;
        if (attempt === 1) throw new WorkspaceError("revision_conflict");
        return { version: version + 1, updatedAt: "2026-10-21T10:00:00Z" };
      },
      get: async () => ({
        workspace: workspace({ previewText: "theirs" }),
        version: 5,
      }),
    },
  });
  // Another tab's unresolved intent for the same project.
  store.setItem(
    "penforge-workspace-op-v1:owner-a%3Aproject-1%3Aother-tab",
    JSON.stringify({
      schemaVersion: 1,
      ownerUid: "owner-a",
      projectId: "project-1",
      operationId: "other-tab",
      baseVersion: 5,
      workspace: workspace({ previewText: "other tab" }),
      createdAt: "2026-10-21T09:00:00Z",
    }),
  );
  saver.schedule(projectOf(workspace({ previewText: "mine" })));
  await saver.flush();
  assert.equal(states.at(-1).state, "conflict");
  assert.match(states.at(-1).message, /changed in another tab/);
  await saver.resolve("saved");
  const remaining = account.readPendingWorkspaceWrites("owner-a", "project-1", store);
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0].operationId, "other-tab");
  assert.equal(states.at(-1).state, "saved");
});

test("a retried operation replays its stored body before reconciliation", async () => {
  const store = storage();
  const puts = [];
  let attempt = 0;
  const saver = workspaceSaver({
    store,
    client: {
      put: async (_id, value, version) => {
        attempt += 1;
        if (attempt === 1) throw new WorkspaceError("network_unavailable");
        puts.push({ value, version });
        return { version: version + 1, updatedAt: "2026-10-21T10:00:00Z" };
      },
      get: async () => ({
        workspace: workspace({ previewText: "remote" }),
        version: 4,
      }),
    },
  });
  saver.schedule(projectOf(workspace({ previewText: "kept" })));
  await saver.flush();
  const [record] = account.readPendingWorkspaceWrites("owner-a", "project-1", store);
  assert.equal(record.workspace.previewText, "kept");
  await saver.retryPending();
  assert.equal(puts[0].version, record.baseVersion);
  assert.equal(puts[0].value.previewText, "kept");
});

test("recovery keys are per operation and per identity", () => {
  assert.equal(account.isWorkspaceRecoveryKey("penforge-workspace-op-v1:a"), true);
  assert.equal(account.isWorkspaceRecoveryKey(null), true);
  assert.equal(account.isWorkspaceRecoveryKey("penforge-writing-draft-v1"), false);
  const store = storage();
  store.setItem(
    "penforge-workspace-op-v1:owner-a%3Aproject-1%3Aop-1",
    JSON.stringify({
      schemaVersion: 1,
      ownerUid: "owner-a",
      projectId: "project-1",
      operationId: "op-1",
      baseVersion: 1,
      workspace: workspace(),
      createdAt: "2026-10-21T09:00:00Z",
    }),
  );
  assert.equal(account.readPendingWorkspaceWrites("owner-b", undefined, store).length, 0);
  assert.equal(account.readPendingWorkspaceWrites("owner-a", undefined, store).length, 1);
});

// The draft wire contract is stricter than the UI models: whole-millisecond
// origins and bare lowercase digests. These regressions lock the mapping so a
// real capture and a ready export can always be saved and resumed.
const workspaceUrl = await transpileUrl("../src/lib/account-workspace.ts", [
  ["../../packages/contracts/src/client", transportUrl],
  ["./account-request", requestUrl],
]);
const workspaceWire = await import(workspaceUrl);

test("a fractional observed capture origin is sent as whole milliseconds", () => {
  const state = {
    ...workspaceWire.emptyWorkspace("Latn"),
    sample: {
      kind: "draw",
      strokes: [],
      captureStartedAtMs: 1792555345881.5,
      captureAssetId: "33333333-3333-4333-8333-333333333333",
    },
  };
  const wire = workspaceWire.toWire(state);
  assert.equal(wire.sample.capture_started_at_ms, 1792555345882);
  assert.ok(Number.isInteger(wire.sample.capture_started_at_ms));

  const unbounded = workspaceWire.toWire({
    ...state,
    sample: { ...state.sample, captureStartedAtMs: 4.2e13 },
  });
  assert.equal(unbounded.sample.capture_started_at_ms, 10 ** 13);

  const unknown = workspaceWire.toWire({
    ...state,
    sample: { ...state.sample, captureStartedAtMs: Number.NaN },
  });
  assert.equal("capture_started_at_ms" in unknown.sample, false);
});

test("draft export references carry the bare digest the schema accepts", () => {
  const digest = "a".repeat(64);
  const reference = {
    exportId: "44444444-4444-4444-8444-444444444444",
    format: "ttf",
    status: "ready",
    sha256: `sha256:${digest}`,
    createdAt: "2026-10-21T10:42:51.349Z",
  };
  const wire = workspaceWire.toWire({
    ...workspaceWire.emptyWorkspace("Latn"),
    exports: [reference],
    selectedExportId: reference.exportId,
  });
  assert.equal(wire.exports[0].sha256, digest);
  assert.match(wire.exports[0].sha256, /^[0-9a-f]{64}$/);
  assert.equal(wire.selected_export_id, reference.exportId);

  const invalid = workspaceWire.toWire({
    ...workspaceWire.emptyWorkspace("Latn"),
    exports: [{ ...reference, sha256: "sha256:not-a-digest" }],
  });
  assert.equal("sha256" in invalid.exports[0], false);
});
