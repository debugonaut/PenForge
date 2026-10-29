import assert from "node:assert/strict";
import { test } from "node:test";
import { createTypeScriptLoader } from "./helpers/typescript.mjs";

const load = createTypeScriptLoader(import.meta.url);
const geometryUrl = await load("../src/lib/photo-regions.ts");
const geometry = await import(geometryUrl);
const recovery = await import(
  await load("../src/lib/photo-recovery.ts", [["./photo-regions", geometryUrl]])
);
const scope = {
  ownerUid: "writer-a",
  projectId: "project-a",
  sampleId: "11111111-1111-4111-8111-111111111111",
  digest: `sha256:${"a".repeat(64)}`,
};
const size = { width: 1200, height: 1600 };
const id = (index) =>
  `22222222-2222-4222-8222-${String(index).padStart(12, "0")}`;
const drafts = () =>
  Array.from({ length: 52 }, (_, index) => ({
    id: id(index),
    character: String.fromCharCode(index < 26 ? 65 + index : 97 + index - 26),
    rect: { x: 10 + index * 10, y: 50, width: 8, height: 100 },
    baseline: 140,
  }));
function storage() {
  const values = new Map();
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  };
}
const draft = () => ({
  regions: drafts(),
  capitalHeight: 100,
  threshold: 180,
  attempt: null,
  readyCount: 0,
  issues: {},
});

test("all 52 selections and exact unknown-commit request identity survive refresh", () => {
  const store = storage();
  const value = draft();
  value.attempt = {
    key: id(90),
    regions: geometry.photoAssignments(
      scope.sampleId,
      scope.digest,
      size,
      value.regions,
      value.capitalHeight,
      value.threshold,
    ),
  };
  recovery.savePhotoRecovery(store, scope, size, value);
  const restored = recovery.readPhotoRecovery(store, scope, size);
  assert.equal(restored.state, "ready");
  assert.deepEqual(restored.draft, value);
  assert.equal(restored.draft.regions.length, 52);
  value.attempt.jobId = id(91);
  value.attempt.completed = true;
  value.readyCount = 52;
  recovery.savePhotoRecovery(store, scope, size, value);
  assert.deepEqual(
    recovery.readPhotoRecovery(store, scope, size).draft.attempt,
    value.attempt,
  );
  assert.ok(
    ![...store.values.values()].some((text) => text.includes("data:image")),
    "only bounded source-linked metadata is stored",
  );
});

test("recovery keys isolate account, project, sample and exact source digest", () => {
  const store = storage();
  recovery.savePhotoRecovery(store, scope, size, draft());
  const original = store.getItem(recovery.photoRecoveryKey(scope));
  for (const other of [
    { ...scope, ownerUid: "writer-b" },
    { ...scope, projectId: "project-b" },
    { ...scope, sampleId: id(92) },
    { ...scope, digest: `sha256:${"b".repeat(64)}` },
  ]) {
    assert.equal(
      recovery.readPhotoRecovery(store, other, size).state,
      "missing",
    );
    recovery.savePhotoRecovery(store, other, size, {
      ...draft(),
      threshold: 190,
    });
    assert.equal(store.getItem(recovery.photoRecoveryKey(scope)), original);
    store.setItem(recovery.photoRecoveryKey(other), original);
    assert.equal(
      recovery.readPhotoRecovery(store, other, size).state,
      "invalid",
      "copying another source record cannot rebind it",
    );
  }
  assert.equal(
    recovery.readPhotoRecovery(store, scope, { width: 600, height: 800 }).state,
    "invalid",
  );
});

test("unreadable earlier metadata remains byte-identical when a fresh selection is saved", () => {
  const store = storage();
  const key = recovery.photoRecoveryKey(scope);
  const malformed = "{ broken selection bytes ";
  store.setItem(key, malformed);
  assert.equal(recovery.readPhotoRecovery(store, scope, size).state, "invalid");
  assert.equal(store.getItem(key), malformed);
  recovery.savePhotoRecovery(store, scope, size, draft());
  assert.equal(recovery.readPhotoRecovery(store, scope, size).state, "ready");
  assert.equal(
    [...store.values.entries()].filter(
      ([name, value]) =>
        name.startsWith(`${key}:unreadable:`) && value === malformed,
    ).length,
    1,
  );
  const full = {
    getItem: store.getItem,
    setItem: () => {
      throw new Error("quota");
    },
  };
  store.setItem(key, malformed);
  assert.throws(() => recovery.savePhotoRecovery(full, scope, size, draft()));
  assert.equal(
    store.getItem(key),
    malformed,
    "a failed backup cannot overwrite the unreadable record",
  );
});

test("malformed, oversized or source-substituted recovery cannot become a pending request", () => {
  const store = storage();
  const key = recovery.photoRecoveryKey(scope);
  recovery.savePhotoRecovery(store, scope, size, draft());
  const valid = store.getItem(key);
  for (const change of [
    (row) => {
      row.version = 2;
    },
    (row) => {
      row.scope.ownerUid = "writer-b";
    },
    (row) => {
      row.size.width = 600;
    },
    (row) => {
      row.draft.image = "data:image/png;base64,private";
    },
    (row) => {
      row.draft.capitalHeight = 0;
    },
    (row) => {
      row.draft.regions[0].rect.x = -1;
    },
    (row) => {
      row.draft.regions[0].character = "AB";
    },
    (row) => {
      row.draft.readyCount = 97;
    },
    (row) => {
      row.draft.issues.unowned = "empty_ink";
    },
  ]) {
    const row = JSON.parse(valid);
    change(row);
    store.setItem(key, JSON.stringify(row));
    assert.equal(
      recovery.readPhotoRecovery(store, scope, size).state,
      "invalid",
    );
  }
  store.setItem(key, "x".repeat(131073));
  assert.equal(recovery.readPhotoRecovery(store, scope, size).state, "invalid");
  assert.equal(store.getItem(key).length, 131073);
  const pending = draft();
  pending.attempt = {
    key: id(90),
    regions: geometry.photoAssignments(
      scope.sampleId,
      scope.digest,
      size,
      pending.regions,
      100,
      180,
    ),
  };
  for (const change of [
    (row) => {
      row.attempt.regions[0].source_sha256 = `sha256:${"b".repeat(64)}`;
    },
    (row) => {
      row.attempt.regions[0].baseline_y_px = 100;
    },
    (row) => {
      row.attempt.jobId = "not-a-job-id";
    },
    (row) => {
      row.attempt.completed = true;
    },
  ]) {
    const row = structuredClone(pending);
    change(row);
    assert.throws(() => recovery.savePhotoRecovery(store, scope, size, row));
  }
});

test("completed processing preserves partial and zero-yield outcomes instead of reporting all letters ready", () => {
  const rows = geometry.photoAssignments(
    scope.sampleId,
    scope.digest,
    size,
    drafts().slice(0, 2),
    100,
    180,
  );
  const valid = (assignment) => ({
    region_id: assignment.region_id,
    assignment,
    status: "analyzed",
    reasons: [],
    outline_hash: `sha256:${"c".repeat(64)}`,
  });
  const rejected = (assignment) => ({
    region_id: assignment.region_id,
    assignment,
    status: "needs_better_input",
    reasons: ["crop_touches_ink"],
  });
  const partial = [
    {
      sample_id: scope.sampleId,
      state: "needs_better_input",
      regions: [valid(rows[0]), rejected(rows[1])],
    },
  ];
  assert.deepEqual(geometry.photoBatchOutcome(partial, rows), {
    ready: [rows[0].region_id],
    issues: { [rows[1].region_id]: "crop_touches_ink" },
  });
  const zero = [{ sample_id: scope.sampleId, regions: rows.map(rejected) }];
  assert.deepEqual(geometry.photoBatchOutcome(zero, rows).ready, []);
  assert.equal(
    Object.keys(geometry.photoBatchOutcome(zero, rows).issues).length,
    2,
  );
  assert.throws(() => geometry.photoBatchOutcome([], rows));
  const stale = structuredClone(partial);
  stale[0].regions[0].assignment.rect.x++;
  assert.throws(() => geometry.photoBatchOutcome(stale, rows));
  const unknown = structuredClone(partial);
  unknown[0].regions[0].status = "unknown";
  assert.throws(() => geometry.photoBatchOutcome(unknown, rows));
  const missing = structuredClone(partial);
  missing[0].regions.pop();
  assert.throws(() => geometry.photoBatchOutcome(missing, rows));
});
