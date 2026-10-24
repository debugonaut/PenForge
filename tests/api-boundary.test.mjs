import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const transpileUrl = createTypeScriptLoader(import.meta.url);
const importTypeScript = async (relative) => import(await transpileUrl(relative));
const boundary = await importTypeScript("../src/lib/api-boundary.ts");
const canonical = await importTypeScript(
  "../packages/contracts/src/canonical.ts",
);
const client = await importTypeScript("../packages/contracts/src/client.ts");
const boundaryUrl = await transpileUrl("../src/lib/api-boundary.ts");
const clientUrl = await transpileUrl("../packages/contracts/src/client.ts");
const ownerTokenUrl = await transpileUrl("../src/lib/owner-token.ts");
const cloudUrl = await transpileUrl("../src/lib/cloud-project-service.ts", [
  ["./owner-token", ownerTokenUrl],
  ["../../packages/contracts/src/client", clientUrl],
  ["./api-boundary", boundaryUrl],
]);
const cloudService = await import(cloudUrl);
const uuid = "00000000-0000-4000-8000-000000000001";

function memoryStorage(store) {
  return {
    get length() {
      return store.size;
    },
    key: (index) => [...store.keys()][index] ?? null,
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => store.set(key, value),
    removeItem: (key) => store.delete(key),
  };
}

test("server metadata maps to a distinct cloud model without claiming a generated font", () => {
  const mapped = boundary.mapProject({
    id: uuid,
    name: "My writing",
    script_code: "Deva",
    status: "ready",
    row_version: 7,
    updated_at: "2026-10-19T00:00:00Z",
    active_snapshot_id: uuid,
  });
  assert.deepEqual(mapped, {
    mode: "cloud",
    id: uuid,
    name: "My writing",
    scriptCode: "Deva",
    serverState: "ready",
    rowVersion: 7,
    updatedAt: "2026-10-19T00:00:00Z",
    activeSnapshotId: uuid,
    styleControls: {},
    fontAvailability: "unavailable",
  });
  assert.equal("isDemo" in mapped, false);
  assert.equal(boundary.selectServiceMode(), "demo");
  assert.equal(boundary.selectServiceMode("cloud"), "cloud");
});

test("all durable states preserve cancellation, real counts, unknown ETA and review independently", () => {
  for (const [state, expected] of Object.entries({
    queued: "queued",
    running: "processing",
    cancel_requested: "cancel-requested",
    succeeded: "completed",
    failed: "failed",
    canceled: "canceled",
  })) {
    const mapped = boundary.mapJob({
      id: uuid,
      job_type: "analyze_samples",
      state,
      progress: null,
      eta: {},
      requires_user_action: true,
    });
    assert.equal(mapped.status, expected);
    assert.equal(mapped.progress, null);
    assert.equal(mapped.etaRangeSeconds, null);
    assert.equal(mapped.needsReview, true);
  }
  const value = boundary.mapJob({
    id: uuid,
    job_type: "analyze_samples",
    state: "running",
    progress: {
      completed_units: 3,
      total_units: 8,
      plan_revision: 2,
      denominator_change_reason: "additional_regions",
    },
    eta: {
      lower_seconds: 2,
      upper_seconds: 5,
      benchmark_profile_id: "measured-fixture",
    },
  });
  assert.equal(value.progress, 3 / 8);
  assert.equal(value.planRevision, 2);
  assert.equal(value.denominatorChangeReason, "additional_regions");
  assert.deepEqual(value.etaRangeSeconds, [2, 5]);
});

test("legacy strokes retain x/y without guessed timing, pressure, dimensions or source edits", () => {
  const input = [
    [
      { x: 0.2, y: 0.3 },
      { x: 0.23, y: 0.34 },
    ],
  ];
  const before = structuredClone(input);
  const result = boundary.preserveLegacyStrokes(input);
  assert.deepEqual(input, before);
  assert.equal(result.timing, "unavailable_legacy");
  assert.equal(result.capture_size_px, null);
  for (const point of result.strokes[0].points) {
    assert.equal(point.t_ms, null);
    assert.equal(point.pressure, null);
  }
  assert.deepEqual(
    result.strokes[0].points.map(({ x, y }) => ({ x, y })),
    before[0],
  );
  result.strokes[0].points[0].x = 0.9;
  assert.deepEqual(input, before);
  assert.throws(() =>
    boundary.preserveLegacyStrokes([[{ x: Infinity, y: 0 }]]),
  );
  assert.throws(() => boundary.preserveLegacyStrokes([[]]));
});

test("supplied canvas baseline and uniform scale map reversibly, preserving descenders and proportions", () => {
  const transform = {
    captureWidthPx: 800,
    captureHeightPx: 600,
    baselinePx: 440,
    originXPx: 20,
    fontUnitsPerPixel: 1.25,
    unitsPerEm: 1000,
  };
  for (const point of [
    { x: 0, y: 0 },
    { x: 1, y: 1 },
    { x: 0.25, y: 0.8 },
  ]) {
    const font = boundary.capturePointToFont(point, transform);
    const restored = boundary.fontPointToCapture(font, transform);
    assert.ok(Math.abs(point.x - restored.x) < 1e-12);
    assert.ok(Math.abs(point.y - restored.y) < 1e-12);
  }
  assert.ok(boundary.capturePointToFont({ x: 0.2, y: 0.9 }, transform).y < 0);
  assert.equal(boundary.outlineSvgTransform(), "scale(1 -1)");
  assert.throws(() =>
    boundary.capturePointToFont(
      { x: 0, y: 0 },
      { ...transform, fontUnitsPerPixel: 0 },
    ),
  );
  assert.throws(() =>
    boundary.assertCanonicalEditSupported({
      rotation: 15,
      scale: 100,
      crop: 0,
    }),
  );
  assert.throws(() =>
    boundary.assertCanonicalEditSupported({
      rotation: 0,
      scale: 100,
      crop: 10,
    }),
  );
  boundary.assertCanonicalEditSupported({ rotation: 0, scale: 100, crop: 0 });
});

test("Python/TypeScript canonical hashes agree on curves, metadata, UTF8 ordering and float encodings", async () => {
  const vectors = JSON.parse(
    await readFile(
      new URL(
        "../packages/contracts/fixtures/hash-vectors.v1.json",
        import.meta.url,
      ),
    ),
  );
  for (const vector of vectors)
    assert.equal(
      await canonical.contentHash(vector.value, vector.exclude_root_keys),
      vector.sha256,
      vector.name,
    );
  assert.deepEqual(
    canonical.canonicalBytes({ b: 1, a: -0 }),
    canonical.canonicalBytes({ a: 0, b: 1 }),
  );
  for (const invalid of [
    Infinity,
    NaN,
    2 ** 53,
    1e20,
    "\ud800",
    undefined,
    new Date(),
  ])
    assert.throws(() => canonical.canonicalBytes(invalid));
});

test("canonical hashing rejects sparse, accessor and non-JSON roots even when excluding fields", () => {
  const accessor = Object.defineProperty({}, "x", {
    enumerable: true,
    get() {
      throw new Error("accessor must never execute");
    },
  });
  const sparse = Array(1);
  const arrayAccessor = Object.defineProperty([null], "0", {
    enumerable: true,
    get() {
      throw new Error("accessor must never execute");
    },
  });
  const symbolObject = { [Symbol("private")]: 1 };
  for (const value of [
    sparse,
    arrayAccessor,
    new Date(0),
    accessor,
    symbolObject,
    { artifact_hash: undefined },
  ])
    assert.throws(() => canonical.canonicalBytes(value, ["artifact_hash"]));
  assert.throws(() => canonical.canonicalBytes(sparse));
  assert.notDeepEqual(
    canonical.canonicalBytes([]),
    canonical.canonicalBytes([null]),
  );
});

test("foundation fetch client makes only explicit calls and does not leak error response data", async () => {
  let called = 0;
  const api = client.createFoundationClient(
    "http://127.0.0.1:8000",
    async (url, options) => {
      called++;
      assert.equal(url.pathname, "/api/v1/health");
      assert.equal(options.credentials, "omit");
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    },
  );
  assert.equal(called, 0);
  assert.deepEqual(await api.get("/api/v1/health"), { status: "ok" });
  assert.equal(called, 1);
  const failed = client.createFoundationClient(
    "http://127.0.0.1:8000",
    async () =>
      new Response("private raw error", {
        status: 503,
        headers: { "X-Request-ID": "test-request" },
      }),
  );
  await assert.rejects(
    failed.get("/api/v1/health"),
    (error) =>
      error.status === 503 &&
      error.requestId === "test-request" &&
      !error.message.includes("private"),
  );
  assert.throws(() =>
    client.createFoundationClient("https://secret:token@example.com"),
  );
});

test("cloud project transport is authenticated, metadata-only and sends explicit ETag/idempotency headers", async () => {
  const calls = [];
  const project = {
    id: uuid,
    name: "Cloud note",
    script_code: "Latn",
    status: "draft",
    active_snapshot_id: null,
    style_controls: { thickness: 55 },
    row_version: 2,
    created_at: "2026-10-19T00:00:00Z",
    updated_at: "2026-10-19T00:00:00Z",
  };
  const api = client.createProjectsClient(
    "https://api.example.test",
    async () => "identity-token",
    async (url, options) => {
      calls.push({ url: String(url), options });
      return new Response(JSON.stringify(project), {
        status: 201,
        headers: { ETag: '"v2"' },
      });
    },
  );
  const result = await api.create(
    {
      name: "Cloud note",
      script_code: "Latn",
      style_controls: { thickness: 55 },
    },
    "request-key",
  );
  assert.equal(result.etag, '"v2"');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.headers.Authorization, "Bearer identity-token");
  assert.equal(calls[0].options.headers["Idempotency-Key"], "request-key");
  assert.equal(calls[0].options.credentials, "omit");
  assert.equal(calls[0].options.redirect, "error");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    name: "Cloud note",
    script_code: "Latn",
    style_controls: { thickness: 55 },
  });
  const patchApi = client.createProjectsClient(
    "https://api.example.test",
    async () => "identity-token",
    async (_url, options) => {
      assert.equal(options.headers["If-Match"], '"v1"');
      return new Response(JSON.stringify({ ...project, row_version: 3 }), {
        status: 200,
        headers: { ETag: '"v3"' },
      });
    },
  );
  await patchApi.update(uuid, { name: "Updated" }, '"v1"');
});

test("interrupted creations restore exact bodies/keys after reload without blocking independent owners or operations", async () => {
  const store = new Map();
  const previousWindow = globalThis.window;
  globalThis.window = { localStorage: memoryStorage(store) };
  const calls = [];
  const project = {
    id: uuid,
    name: "Recovered draft",
    script_code: "Deva",
    status: "draft",
    row_version: 1,
    updated_at: "2026-10-19T00:00:00Z",
  };
  const api = {
    create: async (body, key) => {
      calls.push({ body, key });
      throw new TypeError("unknown commit");
    },
  };
  try {
    const service = new cloudService.CloudProjectService(api, "guest-owner");
    await assert.rejects(service.create("Recovered draft", "Deva"));
    await assert.rejects(service.create("Independent draft"));
    const originals = cloudService.readCloudPendingCreates("guest-owner");
    assert.equal(originals.length, 2);
    assert.notEqual(originals[0].idempotencyKey, originals[1].idempotencyKey);
    const destination = new cloudService.CloudProjectService(
      {
        create: async (body, key) => {
          calls.push({ body, key });
          return { project: { ...project, name: body.name }, replayed: false };
        },
      },
      "destination-owner",
    );
    await assert.rejects(
      destination.retryCreate(originals[0]),
      (error) => error.code === "pending_create_identity_mismatch",
    );
    await destination.create("Destination independent");
    assert.equal(cloudService.readCloudPendingCreates("guest-owner").length, 2);
    const reloaded = new cloudService.CloudProjectService(
      {
        create: async (body, key) => {
          calls.push({ body, key });
          return { project, replayed: true };
        },
      },
      "guest-owner",
    );
    const recovered = await reloaded.retryCreate({
      ...originals[0],
      body: { name: "Tampered", script_code: "Latn" },
    });
    assert.equal(recovered.replayed, true);
    assert.deepEqual(
      calls.at(-1),
      calls[0],
      "retry reads the stored original details rather than a changed form body",
    );
    assert.deepEqual(cloudService.readCloudPendingCreates("guest-owner"), [
      originals[1],
    ]);
    // Claims make the old guest unavailable; an authorization error still cannot disprove its earlier unknown commit.
    const claimed = new cloudService.CloudProjectService(
      {
        create: async () => {
          throw new client.ProjectsResponseError(403, null, "account_merged");
        },
      },
      "guest-owner",
    );
    await assert.rejects(claimed.retryCreate(originals[1]));
    assert.deepEqual(cloudService.readCloudPendingCreates("guest-owner"), [
      originals[1],
    ]);
    await destination.create("Another destination creation");
    assert.equal(cloudService.readCloudPendingCreates().length, 1);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("cloud conflict recovery keeps the complete local metadata patch until resolved", async () => {
  const store = new Map();
  const previousWindow = globalThis.window;
  globalThis.window = {
    localStorage: memoryStorage(store),
  };
  const current = {
    id: uuid,
    name: "Remote title",
    script_code: "Latn",
    status: "draft",
    row_version: 2,
    updated_at: "2026-10-19T00:00:00Z",
    style_controls: { thickness: 40 },
  };
  const saved = { ...current, name: "Local title", row_version: 3 };
  let patchSent;
  const service = new cloudService.CloudProjectService({
    update: async (_id, patch, etag) => {
      if (etag === '"v1"')
        throw new client.ProjectsResponseError(412, null, "revision_conflict", {
          current_project: {
            name: current.name,
            row_version: current.row_version,
            updated_at: current.updated_at,
          },
        });
      patchSent = { patch, etag };
      return { project: saved };
    },
    get: async () => ({ project: current }),
  });
  try {
    await assert.rejects(
      service.update(
        { id: uuid, name: "Older title", rowVersion: 1 },
        { name: "Local title", style_controls: { thickness: 67, slant: -5 } },
      ),
      (error) => error.code === "revision_conflict",
    );
    await assert.rejects(
      service.update(
        {
          id: "00000000-0000-4000-8000-000000000002",
          name: "Second",
          rowVersion: 1,
        },
        { name: "Second local title", style_controls: { thickness: 32 } },
      ),
      (error) => error.code === "revision_conflict",
    );
    const conflict = cloudService.readCloudProjectConflict();
    assert.deepEqual(conflict.localChanges, {
      name: "Local title",
      style_controls: { thickness: 67, slant: -5 },
    });
    await service.saveResolvedConflict(conflict, true);
    assert.deepEqual(patchSent, {
      patch: conflict.localChanges,
      etag: '"v2"',
    });
    assert.equal(
      cloudService.readCloudProjectConflict().projectId,
      "00000000-0000-4000-8000-000000000002",
    );
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("cloud conflict rebase does not overwrite server fields the tab did not change", async () => {
  const store = new Map();
  const previousWindow = globalThis.window;
  globalThis.window = {
    localStorage: memoryStorage(store),
  };
  const current = {
    id: uuid,
    name: "Other tab title",
    script_code: "Latn",
    status: "draft",
    row_version: 2,
    updated_at: "2026-10-19T00:00:00Z",
    style_controls: { thickness: 72, spacing: 63 },
  };
  let rebasedPatch;
  const service = new cloudService.CloudProjectService({
    update: async (_id, patch, etag) => {
      if (etag === '"v1"')
        throw new client.ProjectsResponseError(412, null, "revision_conflict", {
          current_project: {
            name: current.name,
            row_version: current.row_version,
            updated_at: current.updated_at,
          },
        });
      rebasedPatch = patch;
      return {
        project: {
          ...current,
          name: patch.name ?? current.name,
          row_version: 3,
        },
      };
    },
    get: async () => ({ project: current }),
  });
  try {
    await assert.rejects(
      service.update(
        {
          id: uuid,
          name: "Earlier title",
          rowVersion: 1,
          styleControls: { thickness: 50, spacing: 50 },
        },
        { name: "Recovered title" },
      ),
      (error) => error.code === "revision_conflict",
    );
    const conflict = cloudService.readCloudProjectConflict();
    await service.saveResolvedConflict(conflict, true);
    assert.deepEqual(rebasedPatch, { name: "Recovered title" });
    assert.equal(
      cloudService.readCloudProjectConflict(),
      null,
      "the resolved local recovery record is removed",
    );
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("interrupted cloud update persists its patch and detects a commit before retrying", async () => {
  const store = new Map();
  const previousWindow = globalThis.window;
  globalThis.window = {
    localStorage: memoryStorage(store),
  };
  let server = {
    id: uuid,
    name: "Committed before response loss",
    script_code: "Latn",
    status: "draft",
    row_version: 2,
    updated_at: "2026-10-19T00:00:00Z",
    style_controls: { thickness: 50 },
  };
  let calls = 0;
  const service = new cloudService.CloudProjectService({
    update: async (_id, patch) => {
      calls++;
      assert.deepEqual(patch, { name: "Committed before response loss" });
      if (calls === 1) throw new TypeError("response interrupted after commit");
      return { project: server };
    },
    get: async () => ({ project: server }),
  });
  try {
    await assert.rejects(
      service.update(
        { id: uuid, name: "Old title", rowVersion: 1, styleControls: {} },
        { name: "Committed before response loss" },
      ),
      (error) => error.code === "network_unavailable",
    );
    const recovery = cloudService.readCloudProjectConflict();
    assert.equal(recovery.mode, "interrupted");
    assert.deepEqual(recovery.localChanges, {
      name: "Committed before response loss",
    });
    const checked = await service.saveResolvedConflict(recovery, true);
    assert.equal(checked.rowVersion, 2);
    assert.equal(calls, 1, "the already committed patch is not written twice");
    assert.equal(cloudService.readCloudProjectConflict(), null);
    assert.equal(store.size, 0);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("separate unresolved edits retain each tab's recovery patch", async () => {
  const store = new Map();
  const previousWindow = globalThis.window;
  globalThis.window = {
    localStorage: memoryStorage(store),
  };
  const current = {
    id: uuid,
    name: "Cloud title",
    script_code: "Latn",
    status: "draft",
    row_version: 2,
    updated_at: "2026-10-19T00:00:00Z",
    style_controls: { thickness: 60 },
  };
  const service = new cloudService.CloudProjectService({
    update: async () => {
      throw new client.ProjectsResponseError(412, null, "revision_conflict", {
        current_project: {
          name: current.name,
          row_version: current.row_version,
          updated_at: current.updated_at,
        },
      });
    },
    get: async () => ({ project: current }),
  });
  try {
    const stale = {
      id: uuid,
      name: "Cloud title",
      rowVersion: 1,
      styleControls: {},
    };
    await assert.rejects(
      service.update(stale, { name: "Tab A title" }),
      (error) => error.code === "revision_conflict",
    );
    await assert.rejects(
      service.update(stale, { style_controls: { spacing: 62 } }),
      (error) => error.code === "revision_conflict",
    );
    const recoveries = cloudService.readCloudProjectConflicts("test-owner");
    assert.equal(recoveries.length, 2);
    assert.notEqual(recoveries[0].recoveryId, recoveries[1].recoveryId);
    assert.deepEqual(
      recoveries.map((item) => item.localChanges),
      [{ name: "Tab A title" }, { style_controls: { spacing: 62 } }],
    );
    await service.saveResolvedConflict(recoveries[0], false);
    const remaining = cloudService.readCloudProjectConflicts("test-owner");
    assert.equal(remaining.length, 1);
    assert.deepEqual(remaining[0].localChanges, {
      style_controls: { spacing: 62 },
    });
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("an open editor keeps its original ETag after remote same-field save and list refresh", async () => {
  const previousWindow = globalThis.window;
  globalThis.window = { localStorage: memoryStorage(new Map()) };
  let remote = {
    id: uuid,
    name: "Original",
    script_code: "Latn",
    status: "draft",
    row_version: 1,
    updated_at: "2026-10-19T00:00:00Z",
    style_controls: { thickness: 50 },
  };
  const etags = [];
  const service = new cloudService.CloudProjectService({
    list: async () => ({ items: [remote], next_cursor: null }),
    get: async () => ({ project: remote }),
    update: async (_id, patch, etag) => {
      etags.push(etag);
      if (etag !== `"v${remote.row_version}"`)
        throw new client.ProjectsResponseError(412, null, "revision_conflict", {
          current_project: remote,
        });
      remote = {
        ...remote,
        name: patch.name ?? remote.name,
        style_controls: { ...remote.style_controls, ...patch.style_controls },
        row_version: remote.row_version + 1,
      };
      return { project: remote };
    },
  });
  try {
    const [first] = await service.list();
    const editor = cloudService.openCloudProjectEditor(first);
    editor.name = "Local same-field edit";
    remote = {
      ...remote,
      name: "Remote same-field edit",
      style_controls: { thickness: 79 },
      row_version: 2,
    };
    const [refreshed] = await service.list();
    assert.equal(refreshed.rowVersion, 2);
    assert.equal(editor.baseProject.rowVersion, 1);
    await assert.rejects(
      service.update(
        editor.baseProject,
        cloudService.cloudProjectEditorPatch(editor),
      ),
      (error) => error.code === "revision_conflict",
    );
    assert.equal(remote.name, "Remote same-field edit");
    await service.saveResolvedConflict(
      cloudService.readCloudProjectConflict("test-owner"),
      true,
    );
    assert.deepEqual(etags, ['"v1"', '"v2"']);
    assert.equal(remote.name, editor.name);
    assert.equal(remote.style_controls.thickness, 79);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("interleaved recovery write/write keeps both unresolved operations", async () => {
  const previousWindow = globalThis.window;
  const store = new Map();
  const storage = memoryStorage(store);
  const pending = [];
  const serviceA = new cloudService.CloudProjectService({
    update: () => new Promise((_resolve, reject) => pending.push(reject)),
  });
  const serviceB = new cloudService.CloudProjectService({
    update: () => new Promise((_resolve, reject) => pending.push(reject)),
  });
  const stale = {
    id: uuid,
    name: "Original",
    rowVersion: 1,
    styleControls: {},
  };
  let nested;
  let interleave = true;
  const set = storage.setItem;
  storage.setItem = (key, value) => {
    if (interleave) {
      interleave = false;
      nested = serviceB
        .update(stale, { style_controls: { spacing: 64 } })
        .catch((error) => error);
      assert.equal(
        pending.length,
        1,
        "tab B is unresolved before tab A commits its storage write",
      );
    }
    set(key, value);
  };
  globalThis.window = { localStorage: storage };
  try {
    const outer = serviceA
      .update(stale, { name: "Tab A" })
      .catch((error) => error);
    assert.equal(pending.length, 2);
    assert.equal(
      cloudService.readCloudProjectConflicts("test-owner").length,
      2,
    );
    pending.forEach((reject) => reject(new TypeError("connection lost")));
    await Promise.all([outer, nested]);
    assert.equal(
      cloudService.readCloudProjectConflicts("test-owner").length,
      2,
    );
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("interleaved recovery remove/write cannot delete another tab's new backup", async () => {
  const previousWindow = globalThis.window;
  const store = new Map();
  const storage = memoryStorage(store);
  globalThis.window = { localStorage: storage };
  const stale = {
    id: uuid,
    name: "Original",
    rowVersion: 1,
    styleControls: {},
  };
  const remote = {
    id: uuid,
    name: "Remote",
    script_code: "Latn",
    status: "draft",
    row_version: 2,
    updated_at: "2026-10-19T00:00:00Z",
    style_controls: {},
  };
  const serviceA = new cloudService.CloudProjectService({
    update: async () => {
      throw new TypeError("unknown commit A");
    },
    get: async () => ({ project: remote }),
  });
  let nested;
  let rejectB;
  const serviceB = new cloudService.CloudProjectService({
    update: () =>
      new Promise((_resolve, reject) => {
        rejectB = reject;
      }),
  });
  try {
    await assert.rejects(serviceA.update(stale, { name: "Tab A" }));
    const original = cloudService.readCloudProjectConflict("test-owner");
    const remove = storage.removeItem;
    storage.removeItem = (key) => {
      nested = serviceB
        .update(stale, { name: "Tab B" })
        .catch((error) => error);
      assert.equal(
        cloudService.readCloudProjectConflicts("test-owner").length,
        2,
      );
      remove(key);
    };
    await serviceA.saveResolvedConflict(original, false);
    const remaining = cloudService.readCloudProjectConflicts("test-owner");
    assert.equal(remaining.length, 1);
    assert.equal(remaining[0].localName, "Tab B");
    rejectB(new TypeError("unknown commit B"));
    await nested;
    assert.equal(
      cloudService.readCloudProjectConflicts("test-owner").length,
      1,
    );
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("legacy create and aggregate recovery bytes survive migration and per-operation resolution", async () => {
  const previousWindow = globalThis.window;
  const legacyChange = JSON.stringify({
    schemaVersion: 1,
    conflicts: [
      {
        mode: "interrupted",
        recoveryId: "legacy-a",
        ownerUid: "test-owner",
        projectId: uuid,
        localName: "A",
        localChanges: { name: "A" },
        serverProject: null,
      },
      {
        mode: "interrupted",
        recoveryId: "legacy-b",
        ownerUid: "test-owner",
        projectId: uuid,
        localName: "B",
        localChanges: { name: "B" },
        serverProject: null,
      },
    ],
  });
  const legacyCreate = JSON.stringify({
    schemaVersion: 1,
    ownerUid: "test-owner",
    idempotencyKey: "original-key",
    body: { name: "Legacy", script_code: "Deva", style_controls: { slant: 3 } },
  });
  const store = new Map([
    ["penforge-cloud-change-recovery-v1", legacyChange],
    ["penforge-cloud-create-recovery-v1", legacyCreate],
    ["penforge-projects-v1", "populated-local-draft-bytes"],
  ]);
  globalThis.window = { localStorage: memoryStorage(store) };
  const project = {
    id: uuid,
    name: "Legacy",
    script_code: "Deva",
    status: "draft",
    row_version: 2,
    updated_at: "2026-10-19T00:00:00Z",
    style_controls: {},
  };
  const service = new cloudService.CloudProjectService({
    create: async (body, key) => {
      assert.equal(key, "original-key");
      assert.deepEqual(body, JSON.parse(legacyCreate).body);
      return { project, replayed: true };
    },
    get: async () => ({ project }),
  });
  try {
    await service.retryCreate(
      cloudService.readCloudPendingCreates("test-owner")[0],
    );
    assert.deepEqual(cloudService.readCloudPendingCreates("test-owner"), []);
    await service.saveResolvedConflict(
      cloudService.readCloudProjectConflict("test-owner", "legacy-a"),
      false,
    );
    assert.equal(
      cloudService.readCloudProjectConflicts("test-owner").length,
      1,
    );
    assert.equal(
      cloudService.readCloudProjectConflict("test-owner").recoveryId,
      "legacy-b",
    );
    assert.equal(store.get("penforge-cloud-change-recovery-v1"), legacyChange);
    assert.equal(store.get("penforge-cloud-create-recovery-v1"), legacyCreate);
    assert.equal(
      store.get("penforge-projects-v1"),
      "populated-local-draft-bytes",
    );
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("simultaneous creates use separate operation keys and confirming one retains the other", async () => {
  const previousWindow = globalThis.window;
  const store = new Map();
  const calls = [];
  globalThis.window = { localStorage: memoryStorage(store) };
  const service = new cloudService.CloudProjectService({
    create: (body, key) =>
      new Promise((resolve, reject) =>
        calls.push({ body, key, resolve, reject }),
      ),
  });
  const project = {
    id: uuid,
    name: "A",
    script_code: "Latn",
    status: "draft",
    row_version: 1,
    updated_at: "2026-10-19T00:00:00Z",
  };
  try {
    const a = service.create("A");
    const b = service.create("B").catch((error) => error);
    assert.equal(calls.length, 2);
    assert.notEqual(calls[0].key, calls[1].key);
    assert.equal(cloudService.readCloudPendingCreates("test-owner").length, 2);
    calls[0].resolve({ project, replayed: false });
    await a;
    assert.equal(cloudService.readCloudPendingCreates("test-owner").length, 1);
    assert.equal(
      cloudService.readCloudPendingCreates("test-owner")[0].idempotencyKey,
      calls[1].key,
    );
    calls[1].reject(new TypeError("unknown commit"));
    await b;
    assert.equal(cloudService.readCloudPendingCreates("test-owner").length, 1);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("an identity change while obtaining a token cannot send another owner's recovery operation", async () => {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  globalThis.window = { localStorage: memoryStorage(new Map()) };
  let uid = "original-owner";
  let resolveToken;
  let requested = 0;
  globalThis.fetch = async () => {
    requested++;
    throw new Error("must not send with changed identity");
  };
  const identity = {
    configuration: {
      state: "configured",
      apiOrigin: "https://api.example.test",
    },
    getSnapshot: () => ({ identity: { firebase_uid: uid } }),
    getAccessToken: () =>
      new Promise((resolve) => {
        resolveToken = resolve;
      }),
  };
  try {
    const service = cloudService.createCloudProjectService(identity);
    const operation = service.create("Original identity draft");
    uid = "destination-owner";
    resolveToken("original-token");
    await assert.rejects(operation, (error) => error.code === "not_signed_in");
    assert.equal(requested, 0);
    assert.equal(
      cloudService.readCloudPendingCreates("original-owner").length,
      1,
    );
    assert.equal(
      cloudService.readCloudPendingCreates("destination-owner").length,
      0,
    );
  } finally {
    globalThis.fetch = previousFetch;
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("retryable HTTP create failures restore their original body/key and cannot replay under another identity", async () => {
  const previousWindow = globalThis.window;
  globalThis.window = { localStorage: memoryStorage(new Map()) };
  const calls = [];
  let fail = true;
  const project = {
    id: uuid,
    name: "HTTP recovery original",
    script_code: "Deva",
    status: "draft",
    row_version: 1,
    updated_at: "2026-10-19T00:00:00Z",
  };
  const service = new cloudService.CloudProjectService(
    {
      create: async (body, key) => {
        calls.push({ body, key });
        if (fail) {
          fail = false;
          throw new client.ProjectsResponseError(
            503,
            null,
            "dependency_unavailable",
          );
        }
        return { project, replayed: true };
      },
    },
    "original-owner",
  );
  try {
    let original;
    try {
      await service.create("HTTP recovery original", "Deva");
    } catch (error) {
      assert.ok(error instanceof client.ProjectsResponseError);
      original = cloudService.pendingCreateFromError(error);
    }
    assert.deepEqual(original.body, {
      name: "HTTP recovery original",
      script_code: "Deva",
      style_controls: {},
    });
    assert.equal(original.idempotencyKey, calls[0].key);
    const other = new cloudService.CloudProjectService(
      {
        create: async () => {
          throw new Error("must not send foreign recovery");
        },
      },
      "destination-owner",
    );
    await assert.rejects(
      other.retryCreate(original),
      (error) => error.code === "pending_create_identity_mismatch",
    );
    const retry = await service.retryCreate(original);
    assert.equal(retry.replayed, true);
    assert.deepEqual(
      calls[1],
      calls[0],
      "the primary form's restored record reuses the original request",
    );
    assert.deepEqual(
      cloudService.readCloudPendingCreates("original-owner"),
      [],
    );
    assert.equal(
      cloudService.pendingCreateFromError({
        pendingCreate: { ownerUid: "malformed" },
      }),
      null,
    );
    assert.equal(cloudService.pendingCreateFromError(null), null);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});
