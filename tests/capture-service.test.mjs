import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const transpileUrl = createTypeScriptLoader(import.meta.url);
const transportUrl = await transpileUrl("../packages/contracts/src/client.ts");
const clientUrl = await transpileUrl("../src/lib/capture-client.ts", [
  ["../../packages/contracts/src/client", transportUrl],
]);
const recoveryUrl = await transpileUrl("../src/lib/capture-recovery.ts");
const serviceUrl = await transpileUrl("../src/lib/capture-service.ts", [
  ["./capture-client", clientUrl],
  ["./capture-recovery", recoveryUrl],
]);
const { submitCapture } = await import(serviceUrl);
const { CaptureError } = await import(clientUrl);

const imageBytes = Uint8Array.of(
  137,
  80,
  78,
  71,
  13,
  10,
  26,
  10,
  0,
  1,
  255,
  42,
);
const document = {
  schema_version: 1,
  coordinate_system: {
    origin: "top_left",
    x_positive: "right",
    y_positive: "down",
    normalized: true,
  },
  capture_size_px: { width: 700, height: 290 },
  timing: "observed",
  operations: [],
  parent_source_hash: null,
  strokes: [
    {
      stroke_id: "observed-stroke",
      pointer_type: "mouse",
      points: [
        { x: 0.2, y: 0.3, t_ms: 0 },
        { x: 0.4, y: 0.5, t_ms: 20 },
      ],
    },
  ],
};

function harness(overrides = {}) {
  const initial = {
    id: "operation-a",
    ownerUid: "owner-a",
    localProjectId: "local-project",
    projectId: "cloud-project",
    originalId: "original-a",
    initKey: "original-init-key",
    sampleKey: "original-sample-key",
    phase: "prepared",
    createdAt: "2026-10-19T00:00:00Z",
    ...overrides,
  };
  const operations = new Map([[initial.id, structuredClone(initial)]]);
  const originals = new Map([
    [
      "original-a",
      {
        id: "original-a",
        localProjectId: "local-project",
        bytes: new Blob([imageBytes], { type: "image/png" }),
        name: "Selected original.png",
        mime: "image/png",
        lastModified: 123,
        createdAt: "2026-10-19T00:00:00Z",
      },
    ],
  ]);
  const phases = [];
  const calls = {
    init: [],
    asset: [],
    renew: [],
    upload: [],
    complete: [],
    job: [],
    sample: [],
  };
  const progress = [];
  const recovery = {
    getOriginal: async (id) => originals.get(id),
    patch: async (id, patch) => {
      const current = operations.get(id);
      const next =
        current.phase === "confirmed"
          ? current
          : { ...current, ...structuredClone(patch) };
      operations.set(id, next);
      phases.push(next.phase);
      return structuredClone(next);
    },
  };
  const ready = {
    asset_id: "asset-a",
    state: "ready",
    job_id: "job-a",
    duplicate_of: null,
    reject_reason: null,
  };
  const client = Object.fromEntries(
    Object.keys(calls).map((method) => [
      method,
      async (...args) => {
        calls[method].push(args);
        throw new Error(`Unexpected ${method}`);
      },
    ]),
  );
  client.init = async (...args) => {
    calls.init.push(args);
    return { asset_id: "asset-a" };
  };
  client.asset = async (...args) => {
    calls.asset.push(args);
    return ready;
  };
  client.sample = async (...args) => {
    calls.sample.push(args);
    return { sample_id: "sample-a", asset_id: "asset-a" };
  };
  return {
    initial,
    operations,
    originals,
    recovery,
    phases,
    calls,
    client,
    ready,
    progress,
    run: (
      operation = operations.get(initial.id),
      ownerUid = "owner-a",
      signal,
    ) =>
      submitCapture(
        client,
        structuredClone(operation),
        ownerUid,
        (value) => progress.push(value),
        signal,
        recovery,
      ),
  };
}

function usePendingUpload(h) {
  h.client.asset = async (...args) => {
    h.calls.asset.push(args);
    return { ...h.ready, state: "pending_upload", job_id: null };
  };
  h.client.renew = async (...args) => {
    h.calls.renew.push(args);
    return { upload: { url: "/signed-object", method: "PUT", headers: {} } };
  };
  h.client.upload = async (...args) => {
    h.calls.upload.push(args);
    args[2](imageBytes.length, imageBytes.length);
  };
  h.client.complete = async (...args) => {
    h.calls.complete.push(args);
    return { asset: h.ready, job_id: "job-a" };
  };
}

test("image capture hashes and uploads preserved original bytes independently of resized preview", async () => {
  const h = harness();
  usePendingUpload(h);
  // A demo preview is deliberately different data and is never part of the operation or request.
  const preview = new Blob(["resized JPEG preview"], { type: "image/jpeg" });
  assert.notEqual(preview.size, imageBytes.length);
  const result = await h.run();
  const [projectId, init, key] = h.calls.init[0];
  assert.equal(projectId, "cloud-project");
  assert.equal(key, h.initial.initKey);
  assert.equal(init.declared_mime, "image/png");
  assert.equal(init.declared_size, imageBytes.length);
  const expectedHash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", imageBytes)),
  )
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  assert.equal(init.sha256, `sha256:${expectedHash}`);
  assert.equal(h.calls.upload[0][0].url, "/signed-object");
  assert.deepEqual(
    new Uint8Array(await h.calls.upload[0][1].arrayBuffer()),
    imageBytes,
  );
  assert.equal(result.phase, "confirmed");
  assert.equal(result.sampleId, "sample-a");
  assert.ok(
    h.originals.has("original-a"),
    "server confirmation retains the local original",
  );
  assert.ok(h.progress.every((item) => item.etaSeconds === null));
  assert.deepEqual(
    h.progress.find((item) => item.basis === "bytes"),
    {
      phase: "uploading",
      completed: 0,
      total: imageBytes.length,
      basis: "bytes",
      etaSeconds: null,
    },
  );
});

test("interrupted initialization reload retries the exact original body and idempotency key", async () => {
  const h = harness();
  let fail = true;
  h.client.init = async (...args) => {
    h.calls.init.push(args);
    if (fail) {
      fail = false;
      throw new TypeError("unknown commit");
    }
    return { asset_id: "asset-a" };
  };
  await assert.rejects(h.run(), TypeError);
  const reloaded = structuredClone(h.operations.get(h.initial.id));
  assert.equal(reloaded.phase, "retry");
  assert.equal(reloaded.assetId, undefined);
  assert.ok(h.originals.has(reloaded.originalId));
  await h.run(reloaded);
  assert.equal(h.calls.init.length, 2);
  assert.deepEqual(h.calls.init[0].slice(0, 3), h.calls.init[1].slice(0, 3));
  assert.equal(h.operations.get(h.initial.id).phase, "confirmed");
});

test("lost completion response resumes the durable verification job without another upload or complete", async () => {
  const h = harness();
  let server = { ...h.ready, state: "pending_upload", job_id: null };
  usePendingUpload(h);
  h.client.asset = async (...args) => {
    h.calls.asset.push(args);
    return server;
  };
  h.client.complete = async (...args) => {
    h.calls.complete.push(args);
    server = { ...h.ready, state: "uploaded" };
    throw new TypeError("completion committed before disconnect");
  };
  h.client.job = async (...args) => {
    h.calls.job.push(args);
    server = h.ready;
    return {
      state: "succeeded",
      stage: "publish",
      progress: { completed_units: 3, total_units: 3 },
      error_code: null,
    };
  };
  await assert.rejects(h.run(), TypeError);
  const retained = h.operations.get(h.initial.id);
  assert.equal(retained.assetId, "asset-a");
  assert.equal(retained.phase, "retry");
  const result = await h.run(retained);
  assert.equal(result.jobId, "job-a");
  assert.equal(result.phase, "confirmed");
  assert.equal(h.calls.init.length, 1);
  assert.equal(h.calls.upload.length, 1);
  assert.equal(h.calls.complete.length, 1);
  assert.equal(h.calls.job.length, 1);
  assert.ok(
    h.progress.some(
      (item) =>
        item.phase === "verifying" &&
        item.stage === "publish" &&
        item.completed === 3 &&
        item.total === 3,
    ),
  );
});

test("raw stroke registration replays the original immutable document/key and confirms one sample", async () => {
  const h = harness({ originalId: undefined, strokeDocument: document });
  const receipts = new Map();
  let disconnect = true;
  h.client.sample = async (...args) => {
    h.calls.sample.push(structuredClone(args));
    if (!receipts.has(args[2]))
      receipts.set(args[2], {
        sample_id: "single-sample",
        asset_id: "stroke-asset",
      });
    if (disconnect) {
      disconnect = false;
      throw new TypeError("sample committed but response lost");
    }
    return receipts.get(args[2]);
  };
  await assert.rejects(h.run(), TypeError);
  assert.equal(h.operations.get(h.initial.id).phase, "retry");
  const result = await h.run();
  assert.equal(result.phase, "confirmed");
  assert.equal(receipts.size, 1);
  assert.deepEqual(h.calls.sample[0], h.calls.sample[1]);
  assert.deepEqual(h.calls.sample[1][1], {
    source_kind: "strokes",
    stroke_document: document,
  });
  assert.equal(result.assetId, "stroke-asset");
  await h.run(result);
  assert.equal(
    h.calls.sample.length,
    2,
    "already confirmed operations do not register again",
  );
  assert.deepEqual(h.calls.init, []);
  assert.deepEqual(h.calls.upload, []);
});

test("a different owner cannot send, patch or consume another identity's retained capture", async () => {
  const h = harness();
  await assert.rejects(
    h.run(h.initial, "different-owner"),
    (error) =>
      error instanceof CaptureError && error.code === "identity_mismatch",
  );
  assert.deepEqual(h.phases, []);
  assert.ok(Object.values(h.calls).every((items) => items.length === 0));
  assert.deepEqual(h.operations.get(h.initial.id), h.initial);
  assert.ok(h.originals.has("original-a"));
});

test("rejected images retain original and retake recovery without registering a sample", async () => {
  const h = harness({ assetId: "asset-a" });
  h.client.asset = async (...args) => {
    h.calls.asset.push(args);
    return { ...h.ready, state: "rejected", reject_reason: "invalid_image" };
  };
  await assert.rejects(h.run(), (error) => error.code === "capture_rejected");
  assert.equal(h.operations.get(h.initial.id).phase, "retake");
  assert.ok(h.originals.has("original-a"));
  assert.deepEqual(h.calls.sample, []);
});

test("a terminal verification failure enters retake immediately without a misleading retry", async () => {
  const h = harness();
  let reads = 0;
  usePendingUpload(h);
  h.client.complete = async (...args) => {
    h.calls.complete.push(args);
    return {
      asset: { ...h.ready, state: "verifying" },
      job_id: "job-a",
    };
  };
  h.client.asset = async (...args) => {
    h.calls.asset.push(args);
    reads += 1;
    return reads === 1
      ? { ...h.ready, state: "pending_upload", job_id: null }
      : {
          ...h.ready,
          state: "rejected",
          reject_reason: "image_decode_failed",
        };
  };
  h.client.job = async (...args) => {
    h.calls.job.push(args);
    return {
      ...h.ready,
      state: "failed",
      stage: "decode",
      error_code: "image_decode_failed",
      progress: { completed_units: 1, total_units: 4 },
    };
  };
  await assert.rejects(h.run(), (error) => error.code === "capture_rejected");
  assert.equal(h.operations.get(h.initial.id).phase, "retake");
  assert.ok(h.originals.has("original-a"));
  assert.deepEqual(h.calls.sample, []);
});

test("owner-local duplicate verification registers the rejected alias under its original sample key", async () => {
  const h = harness({ assetId: "asset-a" });
  h.client.asset = async (...args) => {
    h.calls.asset.push(args);
    return {
      ...h.ready,
      state: "rejected",
      reject_reason: "duplicate",
      duplicate_of: "canonical-owner-asset",
    };
  };
  const result = await h.run();
  assert.equal(result.phase, "confirmed");
  assert.deepEqual(h.calls.sample[0].slice(0, 3), [
    "cloud-project",
    {
      source_kind: "asset",
      asset_id: "asset-a",
      source_type: "freeform_photo",
    },
    "original-sample-key",
  ]);
  assert.equal(h.calls.init.length, 0);
});

test("lost sample response after image verification retries registration without re-uploading", async () => {
  const h = harness({ assetId: "asset-a" });
  let fail = true;
  h.client.sample = async (...args) => {
    h.calls.sample.push(args);
    if (fail) {
      fail = false;
      throw new TypeError("lost response");
    }
    return { sample_id: "sample-a", asset_id: "asset-a" };
  };
  await assert.rejects(h.run(), TypeError);
  assert.equal(h.operations.get(h.initial.id).phase, "retry");
  assert.equal(h.operations.get(h.initial.id).assetId, "asset-a");
  await h.run();
  assert.equal(h.calls.sample.length, 2);
  assert.deepEqual(
    h.calls.sample[0].slice(0, 3),
    h.calls.sample[1].slice(0, 3),
  );
  assert.equal(h.calls.init.length, 0);
  assert.equal(h.calls.upload.length, 0);
});

test("a mismatched local original reference cannot send another project's file", async () => {
  const h = harness();
  h.originals.set("original-a", {
    ...h.originals.get("original-a"),
    localProjectId: "different-local-project",
  });
  await assert.rejects(h.run(), (error) => error.code === "original_missing");
  assert.equal(h.operations.get(h.initial.id).phase, "retry");
  assert.ok(Object.values(h.calls).every((items) => items.length === 0));
  assert.ok(h.originals.has("original-a"));
});

// Minimal request/transaction event driver: these tests execute the real adapter's
// preservation and terminal mutation logic, without claiming a browser IDB concurrency test.
function indexedDbDriver() {
  const stores = { originals: new Map(), operations: new Map() };
  return {
    stores,
    open() {
      const request = {};
      request.result = {
        close() {},
        transaction(name) {
          const tx = {
            abort() {
              queueMicrotask(() => tx.onabort?.());
            },
          };
          const complete = () => queueMicrotask(() => tx.oncomplete?.());
          tx.objectStore = () => ({
            transaction: tx,
            add(record) {
              stores[name].set(record.id, structuredClone(record));
              complete();
            },
            put(record) {
              stores[name].set(record.id, structuredClone(record));
            },
            get(id) {
              const result = {};
              queueMicrotask(() => {
                result.result = structuredClone(stores[name].get(id));
                result.onsuccess?.();
                complete();
              });
              return result;
            },
          });
          return tx;
        },
      };
      queueMicrotask(() => request.onsuccess?.());
      return request;
    },
  };
}

test("the real recovery adapter preserves selected File bytes and keeps confirmed operations terminal", async () => {
  const previous = globalThis.indexedDB;
  const driver = indexedDbDriver();
  globalThis.indexedDB = driver;
  const { captureRecovery } = await import(recoveryUrl);
  try {
    const file = new File([imageBytes], "Original photo.png", {
      type: "image/png",
      lastModified: 1234,
    });
    const preserved = await captureRecovery.preserve("local-project", file);
    const original = await captureRecovery.getOriginal(preserved.id);
    assert.deepEqual(
      new Uint8Array(await original.bytes.arrayBuffer()),
      imageBytes,
    );
    assert.equal(original.mime, "image/png");
    assert.equal(original.name, "Original photo.png");
    assert.equal(original.lastModified, 1234);
    const pending = await captureRecovery.prepare({
      ownerUid: "owner-a",
      localProjectId: "local-project",
      projectId: "cloud-project",
      originalId: original.id,
    });
    const confirmed = await captureRecovery.patch(pending.id, {
      phase: "confirmed",
      assetId: "asset-a",
      sampleId: "sample-a",
    });
    // A late network failure from another tab must not downgrade the already confirmed operation.
    const late = await captureRecovery.patch(pending.id, {
      phase: "retry",
      assetId: "stale-asset",
    });
    assert.deepEqual(late, confirmed);
    assert.deepEqual(driver.stores.operations.get(pending.id), confirmed);
    assert.ok(driver.stores.originals.has(original.id));
  } finally {
    if (previous === undefined) delete globalThis.indexedDB;
    else globalThis.indexedDB = previous;
  }
});

const { createCaptureClient } = await import(clientUrl);

test("capture transport is inert until requested and sends explicit authenticated idempotent JSON only", async () => {
  const calls = [];
  const client = createCaptureClient(
    "https://capture.example.test",
    async () => "verified-token",
    async (url, options) => {
      calls.push({ url: String(url), options });
      return new Response(
        JSON.stringify({ sample_id: "sample-a", asset_id: "asset-a" }),
        { status: 201 },
      );
    },
  );
  assert.equal(calls.length, 0);
  await client.sample(
    "project/opaque",
    { source_kind: "strokes", stroke_document: document },
    "stable-sample-key",
  );
  assert.equal(calls.length, 1);
  const call = calls[0];
  assert.equal(
    call.url,
    "https://capture.example.test/api/v1/projects/project%2Fopaque/samples",
  );
  assert.equal(call.options.headers.Authorization, "Bearer verified-token");
  assert.equal(call.options.headers["Idempotency-Key"], "stable-sample-key");
  assert.equal(call.options.credentials, "omit");
  assert.equal(call.options.redirect, "error");
  assert.equal(call.options.cache, "no-store");
  assert.deepEqual(JSON.parse(call.options.body), {
    source_kind: "strokes",
    stroke_document: document,
  });
  await assert.rejects(
    () =>
      client.upload(
        {
          method: "PUT",
          url: "https://other-origin.test/api/v1/storage/objects/token",
          headers: {},
        },
        new Blob(),
        () => {},
      ),
    (error) => error.code === "invalid_capability",
  );
  await assert.rejects(
    () =>
      client.upload(
        { method: "GET", url: "/api/v1/storage/objects/token", headers: {} },
        new Blob(),
        () => {},
      ),
    (error) => error.code === "invalid_capability",
  );
});

function mockModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}
const ownerTokenModule = await transpileUrl("../src/lib/owner-token.ts");
const hooksModule = mockModule(`
  export const useState = (...args) => globalThis.__penforgeCaptureTest.hooks.useState(...args);
  export const useRef = (...args) => globalThis.__penforgeCaptureTest.hooks.useRef(...args);
  export const useEffect = (...args) => globalThis.__penforgeCaptureTest.hooks.useEffect(...args);
  export const createElement = (type, props, ...children) => ({ type, props: {...props, children} });
`);
const authModule = mockModule(`
  export const getAccountIdentityService = () => globalThis.__penforgeCaptureTest.identity;
  export const getPenforgeAuthConfiguration = () => ({state: "configured"});
`);
const projectsModule = mockModule(
  `export const createProjectsClient = () => globalThis.__penforgeCaptureTest.projects;`,
);
const recoveryModule = mockModule(`export const captureRecovery = {
  list: (...args) => globalThis.__penforgeCaptureTest.recovery.list(...args),
  prepare: (...args) => globalThis.__penforgeCaptureTest.recovery.prepare(...args)
};`);
const submitModule = mockModule(`
  export const submitCapture = (...args) => globalThis.__penforgeCaptureTest.submit(...args);
  export const captureErrorText = (error) => error.code ?? "capture_failed";
`);
let panelSource = await readFile(
  new URL("../src/components/creation/CaptureCloudPanel.tsx", import.meta.url),
  "utf8",
);
for (const [from, to] of [
  ['"react"', JSON.stringify(hooksModule)],
  ["next/link", mockModule("export default function Link() {}")],
  ["@/lib/auth-service", authModule],
  ["@/lib/owner-token", ownerTokenModule],
  ["../../../packages/contracts/src/client", projectsModule],
  [
    "@/lib/api-boundary",
    mockModule(
      "export const preserveLegacyStrokes = () => { throw new Error('not used'); };",
    ),
  ],
  [
    "@/lib/job-client",
    mockModule("export const createJobClient = () => ({});"),
  ],
  ["./JobMonitor", mockModule("export function JobMonitor() {}")],
  ["@/lib/capture-client", clientUrl],
  ["@/lib/capture-recovery", recoveryModule],
  ["@/lib/capture-service", submitModule],
  ["./CaptureCloudPanel.module.css", mockModule("export default {}")],
  [
    "@/lib/extraction-client",
    mockModule(
      "export const createExtractionClient = () => ({templates: async () => []});",
    ),
  ],
  ["./CloudReviewPanel", mockModule("export function CloudReviewPanel() {}")],
  [
    "./CloudExportPanel",
    mockModule(
      "export function CloudExportPanel() {}; export const createExportClient = () => ({});",
    ),
  ],
])
  panelSource = panelSource.replaceAll(from, to);
panelSource =
  `import * as React from ${JSON.stringify(hooksModule)};\n` + panelSource;
const panelCompiled = ts.transpileModule(panelSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.React,
  },
}).outputText;
const { CaptureCloudPanel } = await import(mockModule(panelCompiled));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function panelHarness(initialUid = "owner-a") {
  const slots = [],
    effects = [],
    subscribers = new Set();
  let hookIndex = 0;
  let uid = initialUid;
  let mounted = false;
  const identity = {
    configuration: {
      state: "configured",
      apiOrigin: "https://capture.example.test",
    },
    getSnapshot: () => ({ identity: uid ? { firebase_uid: uid } : null }),
    subscribe: (fn) => {
      subscribers.add(fn);
      return () => subscribers.delete(fn);
    },
    connect: async () => {},
    getAccessToken: async () => "token",
  };
  const hooks = {
    useRef(value) {
      const index = hookIndex++;
      return (slots[index] ??= { current: value });
    },
    useState(value) {
      const index = hookIndex++;
      if (!(index in slots)) slots[index] = value;
      return [
        slots[index],
        (next) => {
          slots[index] = typeof next === "function" ? next(slots[index]) : next;
        },
      ];
    },
    useEffect(effect) {
      hookIndex++;
      if (!mounted) effects.push(effect);
    },
  };
  const state = {
    hooks,
    identity,
    projects: {
      list: async () => ({
        items: [{ id: "cloud-project", name: "Destination" }],
      }),
    },
    recovery: { list: async () => [], prepare: async (input) => input },
    submit: async () => {},
  };
  globalThis.__penforgeCaptureTest = state;
  const render = () => {
    hookIndex = 0;
    return CaptureCloudPanel({
      localProjectId: "local-project",
      sample: {
        kind: "draw",
        strokes: [[{ x: 0.1, y: 0.2 }]],
        strokeDocument: document,
      },
    });
  };
  render();
  const cleanups = effects.map((effect) => effect());
  mounted = true;
  return {
    state,
    render,
    switchUid(next) {
      uid = next;
      subscribers.forEach((fn) => fn());
    },
    unmount() {
      cleanups.forEach((fn) => fn?.());
    },
  };
}
function allElements(node) {
  if (!node || typeof node !== "object") return [];
  if (Array.isArray(node)) return node.flatMap(allElements);
  return [node, ...allElements(node.props?.children)];
}
function elementText(node) {
  if (Array.isArray(node)) return node.map(elementText).join("");
  if (node && typeof node === "object")
    return elementText(node.props?.children);
  return node == null || typeof node === "boolean" ? "" : String(node);
}
function panelButton(tree, text) {
  return allElements(tree).find(
    (node) => node.type === "button" && elementText(node).includes(text),
  );
}
async function settlePanel() {
  await new Promise((resolve) => setImmediate(resolve));
}

test("capture panel accepts initial identity hydration then fences a stale account list response", async () => {
  const previousWindow = globalThis.window;
  globalThis.window = { location: { hostname: "127.0.0.1" } };
  const h = panelHarness(null);
  h.state.identity.connect = async () => {
    if (!h.state.identity.getSnapshot().identity) h.switchUid("owner-a");
  };
  let loading = deferred();
  h.state.projects.list = () => loading.promise;
  try {
    panelButton(h.render(), "Connect capture storage").props.onClick();
    await settlePanel();
    loading.resolve({
      items: [{ id: "owner-a-project", name: "Owner A project" }],
    });
    await settlePanel();
    assert.ok(
      elementText(h.render()).includes("Owner A project"),
      "the first configured connection hydrates without needing a second click",
    );
    // Start another list operation, switch accounts before its result arrives, and ensure
    // neither its rows nor its final/error state can replace the destination UI.
    h.switchUid("owner-b");
    loading = deferred();
    panelButton(h.render(), "Check capture connection").props.onClick();
    await settlePanel();
    h.switchUid("owner-c");
    loading.resolve({
      items: [{ id: "owner-b-project", name: "Stale owner B project" }],
    });
    await settlePanel();
    assert.equal(
      elementText(h.render()).includes("Stale owner B project"),
      false,
    );
    assert.ok(elementText(h.render()).includes("Account changed"));
    assert.equal(
      panelButton(h.render(), "Check capture connection").props.disabled,
      false,
    );
  } finally {
    h.unmount();
    delete globalThis.__penforgeCaptureTest;
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("capture panel late submission failure cannot overwrite account-change feedback or resume progress", async () => {
  const previousWindow = globalThis.window;
  globalThis.window = { location: { hostname: "127.0.0.1" } };
  const h = panelHarness();
  const submission = deferred();
  let emitProgress;
  h.state.submit = (...args) => {
    emitProgress = args[3];
    return submission.promise;
  };
  try {
    panelButton(h.render(), "Connect capture storage").props.onClick();
    await settlePanel();
    panelButton(h.render(), "Save raw drawing").props.onClick();
    await settlePanel();
    h.switchUid("owner-b");
    emitProgress({
      phase: "confirmed",
      completed: 1,
      total: 1,
      basis: "verified_checks",
      etaSeconds: null,
    });
    submission.reject(new CaptureError("old_owner_failure"));
    await settlePanel();
    assert.ok(elementText(h.render()).includes("Account changed"));
    assert.equal(elementText(h.render()).includes("old_owner_failure"), false);
    assert.equal(elementText(h.render()).includes("Source confirmed"), false);
    assert.equal(
      panelButton(h.render(), "Check capture connection").props.disabled,
      false,
    );
  } finally {
    h.unmount();
    delete globalThis.__penforgeCaptureTest;
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("a stale pending capture rereads canonical confirmation without another API request", async () => {
  const h = harness();
  const confirmed = {
    ...h.initial,
    phase: "confirmed",
    assetId: "asset-a",
    sampleId: "sample-a",
  };
  h.operations.set(h.initial.id, confirmed);
  const result = await h.run(h.initial);
  assert.deepEqual(result, confirmed);
  assert.ok(Object.values(h.calls).every((items) => items.length === 0));
});

test("same-operation network failure interleaved with another tab's confirmation returns confirmed", async () => {
  const h = harness({ originalId: undefined, strokeDocument: document });
  const responses = [];
  h.client.sample = (...args) => {
    h.calls.sample.push(args);
    const response = deferred();
    responses.push(response);
    return response.promise;
  };
  const first = h.run(h.initial);
  const second = h.run(h.initial);
  await settlePanel();
  assert.equal(
    responses.length,
    2,
    "both tabs have unresolved requests before either response arrives",
  );
  assert.deepEqual(
    h.calls.sample[0].slice(0, 3),
    h.calls.sample[1].slice(0, 3),
  );
  responses[1].resolve({
    sample_id: "single-committed-sample",
    asset_id: "raw-source",
  });
  const secondResult = await second;
  assert.equal(secondResult.phase, "confirmed");
  responses[0].reject(
    new TypeError("older tab lost its response after commit"),
  );
  const firstResult = await first;
  assert.deepEqual(firstResult, secondResult);
  assert.equal(
    h.operations.get(h.initial.id).sampleId,
    "single-committed-sample",
  );
  assert.equal(h.operations.get(h.initial.id).phase, "confirmed");
  assert.equal(
    h.progress.filter((value) => value.phase === "confirmed").length,
    2,
  );
});

test("a template capture retains its exact manifest and source type through upload and sample retry", async () => {
  const template = {
    template_id: "latin-core",
    template_version: "1.0.0",
    manifest_sha256: `sha256:${"a".repeat(64)}`,
    page_index: 1,
  };
  const h = harness({ sourceType: "template_photo", template });
  let registration = 0;
  h.client.sample = async (...args) => {
    h.calls.sample.push(args);
    if (++registration === 1) throw new TypeError("lost response");
    return { sample_id: "template-sample", asset_id: "asset-a" };
  };
  await assert.rejects(h.run(h.initial));
  const retained = h.operations.get(h.initial.id);
  assert.equal(retained.phase, "retry");
  const final = await h.run(structuredClone(retained));
  assert.equal(final.phase, "confirmed");
  assert.equal(h.calls.init[0][1].source_type, "template_photo");
  assert.deepEqual(h.calls.init[0][1].template, template);
  for (const call of h.calls.sample) {
    assert.equal(call[1].source_type, "template_photo");
    assert.deepEqual(call[1].template, template);
    assert.equal(call[2], h.initial.sampleKey);
  }
  assert.deepEqual(h.initial.template, template);
});
