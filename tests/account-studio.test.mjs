import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";
import {
  createTypeScriptLoader,
  javascriptModuleUrl as shim,
} from "./helpers/typescript.mjs";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";

const load = createTypeScriptLoader(import.meta.url);
const state = await import(
  await load("../src/components/creation/creation-state.ts")
);
const recovery = await load("../src/lib/export-recovery.ts");
const recoveries = await import(recovery);
const strokeCapture = await load("../src/lib/stroke-capture.ts");
const photoRegions = await load("../src/lib/photo-regions.ts");
const photoRecovery = await load("../src/lib/photo-recovery.ts", [
  ["./photo-regions", photoRegions],
]);
const photoRecoveries = await import(photoRecovery);

const hooks = shim(`
export const Fragment = Symbol.for("fragment");
export const createElement = (type, props, ...children) => ({type, props: {...props, children}});
export const useState = (initial) => globalThis.__accountStudioHarness.state(initial);
export const useRef = (initial) => globalThis.__accountStudioHarness.ref(initial);
export const useEffect = (effect, deps) => globalThis.__accountStudioHarness.effect(effect, deps);
`);
const drawing = shim("export function DrawingCanvas() {}");
const canonical = shim(
  "export function CloudReviewPanel() {} export function CanonicalGlyph() {}",
);
const backend = shim(
  "export const useAccountBackend = () => globalThis.__accountStudioHarness.backend;",
);
const exportClient = shim(
  "export const createExportClient = () => ({}); export const exportErrorText = (error) => error.message;",
);
const replacements = [
  ["react", hooks],
  ["next/link", shim("export default function Link() {}")],
  [
    "@/components/brand/Characters",
    shim(
      "export function PenCharacter() {} export function QuillCharacter() {}",
    ),
  ],
  ["./DrawingCanvas", drawing],
  [
    "./StepStage",
    shim("export function StepStage() {} export function StepMascot() {}"),
  ],
  ["./CloudReviewPanel", canonical],
  ["./CloudExportPanel", shim("export function CloudExportPanel() {}")],
  ["./PhotoLetterPanel", shim("export function PhotoLetterPanel() {}")],
  ["../../../packages/contracts/src/unicode", unicodeModuleUrl],
  ["@/lib/stroke-capture", strokeCapture],
  [
    "@/lib/capture-service",
    shim(
      "export const submitCapture = async () => { throw new Error('unexpected capture'); }; export const captureErrorText = (error) => error.message;",
    ),
  ],
  ["@/lib/capture-recovery", shim("export const captureRecovery = {};")],
  ["@/lib/use-account-backend", backend],
  ["./WizardIcons", shim("export function UploadIcon() {}")],
  ["./AccountStudio.module.css", shim("export default {};")],
  [
    "./DevanagariTextReadiness",
    shim("export function DevanagariTextReadiness() {}"),
  ],
  ["./JobMonitor", shim("export function JobMonitor() {}")],
  [
    "./ContextualFontPreview",
    shim("export function ContextualFontPreview() {}"),
  ],
  ["@/lib/export-client", exportClient],
  ["@/lib/writing", shim("export const downloadWritingBlob = () => {};")],
  ["./CloudReviewPanel.module.css", shim("export default {};")],
  ["@/lib/export-recovery", recovery],
  ["@/lib/photo-regions", photoRegions],
  ["@/lib/photo-recovery", photoRecovery],
  ["./PhotoLetterPanel.module.css", shim("export default {};")],
  [
    "@/lib/extraction-client",
    shim(
      "export class ExtractionError extends Error {} export const extractionErrorText = (error) => error.message;",
    ),
  ],
];
async function component(path) {
  let source = await readFile(new URL(path, import.meta.url), "utf8");
  for (const [from, to] of replacements)
    source = source.replaceAll(`"${from}"`, JSON.stringify(to));
  source = `import * as React from ${JSON.stringify(hooks)};\n${source}`;
  return import(
    shim(
      ts.transpileModule(source, {
        compilerOptions: {
          module: ts.ModuleKind.ESNext,
          target: ts.ScriptTarget.ES2022,
          jsx: ts.JsxEmit.React,
        },
      }).outputText,
    )
  );
}
const { AccountStudio } = await component(
  "../src/components/creation/AccountStudio.tsx",
);
const { CloudExportPanel } = await component(
  "../src/components/creation/CloudExportPanel.tsx",
);
const { PhotoLetterPanel } = await component(
  "../src/components/creation/PhotoLetterPanel.tsx",
);
const { DrawingCanvas } = await import(drawing);
const { CanonicalGlyph } = await import(canonical);

function harness(backendValue = {}) {
  const slots = [],
    effects = [];
  let index = 0;
  return {
    backend: {
      ready: true,
      templates: [],
      operations: [],
      refreshOperations: async () => {},
      ...backendValue,
    },
    state(initial) {
      const position = index++;
      if (!(position in slots))
        slots[position] = typeof initial === "function" ? initial() : initial;
      return [
        slots[position],
        (value) => {
          slots[position] =
            typeof value === "function" ? value(slots[position]) : value;
        },
      ];
    },
    ref(initial) {
      const position = index++;
      slots[position] ??= { current: initial };
      return slots[position];
    },
    effect(effect, deps) {
      const position = index++;
      const old = slots[position];
      if (
        old &&
        deps?.every((value, i) => Object.is(value, old.deps?.[i])) &&
        deps.length === old.deps?.length
      )
        return;
      const next = { deps };
      slots[position] = next;
      effects.push(() => {
        old?.cleanup?.();
        next.cleanup = effect();
      });
    },
    render(Component, props) {
      globalThis.__accountStudioHarness = this;
      index = 0;
      return Component(props);
    },
    async settle() {
      for (const effect of effects.splice(0)) effect();
      for (let pass = 0; pass < 6; pass++) await new Promise(setImmediate);
    },
    dispose() {
      for (const value of slots) value?.cleanup?.();
    },
  };
}
function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  if (!node || typeof node !== "object") return [];
  return [node, ...nodes(node.props?.children)];
}
const text = (node) => {
  if (Array.isArray(node)) return node.map(text).join("");
  if (node && typeof node === "object") return text(node.props?.children);
  return typeof node === "string" || typeof node === "number"
    ? String(node)
    : "";
};
const button = (tree, label) =>
  nodes(tree).find(
    (node) => node.type === "button" && text(node).includes(label),
  );
const project = {
  id: "project-a",
  name: "My hand",
  scriptCode: "Latn",
  glyphs: [],
  captures: [],
  assignments: [],
  exports: [],
  snapshots: [],
  sample: null,
  previewText: "A B A",
  activeSnapshotId: "previous-snapshot",
  selectedExportId: null,
  origin: { kind: "account", ownerUid: "writer-a", workspaceVersion: 1 },
};
function storage() {
  const values = new Map();
  return {
    get length() {
      return values.size;
    },
    key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

test("confirmed account image captures can open review without preview bytes or cached glyphs", () => {
  const uploaded = {
    ...project,
    sample: { kind: "upload", captureAssetId: "asset-a" },
    captures: [{ sampleId: "sample-a", assetId: "asset-a", kind: "image" }],
  };
  assert.equal(state.availableStep(uploaded, "review"), "review");
  assert.equal(state.availableStep(uploaded, "export"), "review");
  assert.equal(state.availableStep(project, "review"), "capture");
  assert.equal(
    state.availableStep({ ...uploaded, origin: { kind: "demo" } }, "review"),
    "capture",
  );
  assert.equal(
    state.availableStep(
      {
        ...project,
        sample: { kind: "upload", dataUrl: "data:image/png;base64,AA" },
      },
      "review",
    ),
    "review",
  );
});

test("account specimen loads approved detail geometry and excludes pending or subset mappings", async () => {
  const ids = [];
  const active = {
    id: "a",
    codepoints: [65],
    active_revision_id: "approved-a",
    active_outline: null,
    outline: null,
  };
  const pending = {
    id: "b",
    codepoints: [66],
    active_revision_id: null,
    active_outline: null,
    outline: { pending: true },
  };
  const subset = {
    id: "ab",
    codepoints: [65, 66],
    active_revision_id: "approved-ab",
    active_outline: null,
    outline: null,
  };
  const outline = { approved: true };
  const h = harness({
    extraction: {
      glyphs: async () => [subset, pending, active],
      glyph: async (_project, id) => {
        ids.push(id);
        return {
          data: {
            ...active,
            active_outline: outline,
            outline: { pending: true },
          },
        };
      },
    },
  });
  const props = {
    project,
    step: "preview",
    go() {},
    update() {},
    announce() {},
  };
  h.render(AccountStudio, props);
  await h.settle();
  const tree = h.render(AccountStudio, props);
  const rendered = nodes(tree).filter((node) => node.type === CanonicalGlyph);
  assert.deepEqual(
    ids,
    ["a"],
    "repeated specimen letters fetch one detail; unapproved letters are not fetched",
  );
  assert.equal(rendered.length, 2);
  assert.ok(rendered.every((node) => node.props.outline === outline));
  assert.ok(
    nodes(tree).some((node) => node.type === "span" && text(node) === "B"),
  );
});

test("specimen does not substitute pending geometry when approval disappears during loading", async () => {
  const h = harness({
    extraction: {
      glyphs: async () => [
        { id: "a", codepoints: [65], active_revision_id: "old" },
      ],
      glyph: async () => ({
        data: {
          id: "a",
          codepoints: [65],
          active_revision_id: null,
          active_outline: null,
          outline: { pending: true },
        },
      }),
    },
  });
  const props = {
    project,
    step: "preview",
    go() {},
    update() {},
    announce() {},
  };
  h.render(AccountStudio, props);
  await h.settle();
  assert.equal(
    nodes(h.render(AccountStudio, props)).filter(
      (node) => node.type === CanonicalGlyph,
    ).length,
    0,
  );
});

test("ordinary account export freezes current approvals while explicit historical export stays available", async () => {
  for (const quiet of [true, false]) {
    globalThis.localStorage = storage();
    const bodies = [],
      receipts = [];
    const h = harness();
    const props = {
      quiet,
      defaultSnapshotId: "old-snapshot",
      projectId: project.id,
      ownerUid: "writer-a",
      client: {
        create: async (_project, body) => {
          bodies.push(body);
          return { job_id: "job-a", export_ids: [`font-${bodies.length}`] };
        },
        get: async (id) => ({
          id,
          status: "ready",
          format: "ttf",
          stage: "ready",
          snapshot_id: quiet ? `fresh-${bodies.length}` : "old-snapshot",
        }),
      },
      onReady: (rows) => receipts.push(rows),
    };
    let tree = h.render(CloudExportPanel, props);
    await h.settle();
    if (!quiet) {
      button(tree, "Open private font export").props.onClick();
      tree = h.render(CloudExportPanel, props);
    }
    for (let pass = 0; pass < 2; pass++) {
      button(tree, "Build approved snapshot").props.onClick();
      await h.settle();
      tree = h.render(CloudExportPanel, props);
    }
    assert.equal(bodies.length, 2);
    assert.ok(
      bodies.every((body) =>
        quiet ? !("snapshot_id" in body) : body.snapshot_id === "old-snapshot",
      ),
    );
    assert.deepEqual(
      receipts.map((rows) => rows[0].snapshot_id),
      quiet ? ["fresh-1", "fresh-2"] : ["old-snapshot", "old-snapshot"],
    );
  }
});

test("resuming an unknown-commit export preserves its original snapshot and request key", async () => {
  globalThis.localStorage = storage();
  const pending = {
    key: "11111111-1111-4111-8111-111111111111",
    body: {
      family_name: "Earlier hand",
      formats: ["ttf"],
      snapshot_id: "frozen-before-retry",
    },
  };
  recoveries.saveExportRecovery(
    localStorage,
    recoveries.exportRecoveryPrefix("writer-a", project.id),
    pending,
  );
  const calls = [];
  const h = harness();
  const props = {
    quiet: true,
    projectId: project.id,
    ownerUid: "writer-a",
    client: {
      create: async (...args) => {
        calls.push(args);
        return { job_id: "job-a", export_ids: ["font-a"] };
      },
      get: async () => ({
        id: "font-a",
        status: "ready",
        format: "ttf",
        stage: "ready",
      }),
    },
  };
  h.render(CloudExportPanel, props);
  await h.settle();
  const tree = h.render(CloudExportPanel, props);
  button(tree, "Resume saved export request").props.onClick();
  await h.settle();
  assert.deepEqual(calls[0][1], pending.body);
  assert.equal(calls[0][2], pending.key);
});

test("account drawing rejects invalid observed strokes without replacing the valid raw capture", () => {
  const h = harness({ capture: {}, uid: "writer-a" });
  const props = {
    project,
    step: "capture",
    go() {},
    update() {},
    announce() {},
  };
  let tree = h.render(AccountStudio, props);
  button(tree, "Draw one letter").props.onClick();
  tree = h.render(AccountStudio, props);
  let canvas = nodes(tree).find((node) => node.type === DrawingCanvas);
  const valid = {
    stroke_id: "stroke-a",
    pointer_type: "mouse",
    points: [
      { x: 0.1, y: 0.2, t_ms: 100 },
      { x: 0.2, y: 0.3, t_ms: 110 },
    ],
  };
  assert.equal(
    canvas.props.onObservedStroke(valid, { width: 460, height: 300 }),
    true,
  );
  canvas.props.onChange([
    [
      { x: 0.1, y: 0.2 },
      { x: 0.2, y: 0.3 },
    ],
  ]);
  tree = h.render(AccountStudio, props);
  canvas = nodes(tree).find((node) => node.type === DrawingCanvas);
  assert.equal(
    canvas.props.onObservedStroke(
      { ...valid, stroke_id: "bad", points: [{ x: NaN, y: 0.2, t_ms: 120 }] },
      { width: 460, height: 300 },
    ),
    false,
  );
  tree = h.render(AccountStudio, props);
  canvas = nodes(tree).find((node) => node.type === DrawingCanvas);
  assert.equal(canvas.props.strokes.length, 1);
  assert.deepEqual(canvas.props.sourceSize, { width: 460, height: 300 });
  assert.ok(text(tree).includes("was not added"));
});

test("selected photo letters are submitted in one source-bound batch and retries retain exact request identity", async () => {
  globalThis.localStorage = storage();
  const oldDocument = globalThis.document;
  const oldBitmap = globalThis.createImageBitmap;
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({ drawImage() {} }),
    }),
  };
  globalThis.createImageBitmap = async () => ({
    width: 20,
    height: 20,
    close() {},
  });
  const h = harness();
  let refreshed;
  try {
    const sample = {
      sample_id: "11111111-1111-4111-8111-111111111111",
      asset_id: "asset-a",
      source_sha256: `sha256:${"a".repeat(64)}`,
    };
    const calls = [];
    let ready = 0;
    const props = {
      projectId: project.id,
      ownerUid: "writer-a",
      sample,
      onReady: () => {
        ready++;
      },
      onClose() {},
      capture: {
        asset: async () => ({
          asset_id: "asset-a",
          project_id: project.id,
          sha256: sample.source_sha256,
          width_px: 20,
          height_px: 20,
        }),
        preview: async () =>
          new Blob(["verified by capture transport"], { type: "image/png" }),
      },
      extraction: {
        analyzeImageRegions: async (...args) => {
          calls.push(args);
          const saved = photoRecoveries.readPhotoRecovery(
            localStorage,
            {
              ownerUid: "writer-a",
              projectId: project.id,
              sampleId: sample.sample_id,
              digest: sample.source_sha256,
            },
            { width: 20, height: 20 },
          );
          assert.equal(
            saved.state,
            "ready",
            "the exact retry key must be durable before the first POST",
          );
          assert.equal(saved.draft.attempt.key, args[3]);
          if (calls.length === 1) throw new Error("network interrupted");
          return {
            job_id: "33333333-3333-4333-8333-333333333333",
            state: "succeeded",
            stage: "done",
            progress: { completed_units: 2, total_units: 2 },
            quality: [
              {
                sample_id: sample.sample_id,
                regions: args[2].map((assignment) => ({
                  region_id: assignment.region_id,
                  assignment,
                  status: "analyzed",
                  reasons: [],
                  outline_hash: `sha256:${"b".repeat(64)}`,
                })),
              },
            ],
          };
        },
      },
    };
    h.render(PhotoLetterPanel, props);
    await h.settle();
    let tree = h.render(PhotoLetterPanel, props);
    for (const letter of ["A", "g"]) {
      const label = nodes(tree).find(
        (node) =>
          node.type === "label" && text(node).includes("Which character"),
      );
      const input = nodes(label).find((node) => node.type === "input");
      input.props.onChange({ target: { value: letter } });
      tree = h.render(PhotoLetterPanel, props);
      button(tree, "Add letter to batch").props.onClick();
      tree = h.render(PhotoLetterPanel, props);
    }
    button(tree, "Process 2 letters together").props.onClick();
    await h.settle();
    tree = h.render(PhotoLetterPanel, props);
    assert.equal(calls.length, 1);
    assert.equal(
      calls[0][2].length,
      2,
      "one request contains both chosen letters",
    );
    assert.deepEqual(
      calls[0][2].map((row) => row.codepoints),
      [[65], [103]],
    );
    assert.ok(
      calls[0][2].every(
        (row) =>
          row.source_sha256 === sample.source_sha256 &&
          row.source_width === 20 &&
          row.source_height === 20,
      ),
    );
    h.dispose();
    refreshed = harness();
    refreshed.render(PhotoLetterPanel, props);
    await refreshed.settle();
    tree = refreshed.render(PhotoLetterPanel, props);
    assert.ok(
      text(tree).includes("2 / 96 letters"),
      "queued selections survive a full panel remount",
    );
    assert.ok(
      button(tree, "Resume this letter batch"),
      nodes(tree)
        .filter((node) => node.type === "button")
        .map(text)
        .join(" | "),
    );
    button(tree, "Resume this letter batch").props.onClick();
    await refreshed.settle();
    assert.equal(calls.length, 2);
    assert.equal(
      calls[1][3],
      calls[0][3],
      "retry keeps the unknown-commit idempotency key",
    );
    assert.deepEqual(
      calls[1][2],
      calls[0][2],
      "retry cannot edit an already submitted source-bound batch",
    );
    assert.equal(ready, 1);
    tree = refreshed.render(PhotoLetterPanel, props);
    props.extraction.job = async () => ({
      job_id: "33333333-3333-4333-8333-333333333333",
      state: "succeeded",
      stage: "done",
      progress: { completed_units: 2, total_units: 2 },
      quality: [
        {
          sample_id: sample.sample_id,
          regions: calls[1][2].map((assignment) => ({
            region_id: assignment.region_id,
            assignment,
            status: "analyzed",
            reasons: [],
            outline_hash: `sha256:${"b".repeat(64)}`,
          })),
        },
      ],
    });
    button(tree, "Resume this letter batch").props.onClick();
    await refreshed.settle();
    tree = refreshed.render(PhotoLetterPanel, props);
    assert.ok(
      button(tree, "Review 2 ready letters"),
      "completed retry does not double the ready count",
    );
  } finally {
    h.dispose();
    refreshed?.dispose();
    globalThis.document = oldDocument;
    globalThis.createImageBitmap = oldBitmap;
  }
});

function photoBrowser() {
  const previous = {
    document: globalThis.document,
    bitmap: globalThis.createImageBitmap,
  };
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({ drawImage() {} }),
    }),
  };
  globalThis.createImageBitmap = async () => ({
    width: 20,
    height: 20,
    close() {},
  });
  return () => {
    globalThis.document = previous.document;
    globalThis.createImageBitmap = previous.bitmap;
  };
}

function photoProps(analyze, onReady = () => {}) {
  const sample = {
    sample_id: "11111111-1111-4111-8111-111111111111",
    asset_id: "asset-a",
    source_sha256: `sha256:${"a".repeat(64)}`,
  };
  return {
    projectId: project.id,
    ownerUid: "writer-a",
    sample,
    onReady,
    onClose() {},
    capture: {
      asset: async () => ({
        asset_id: "asset-a",
        project_id: project.id,
        sha256: sample.source_sha256,
        width_px: 20,
        height_px: 20,
      }),
      preview: async () =>
        new Blob(["verified by capture transport"], { type: "image/png" }),
    },
    extraction: { analyzeImageRegions: analyze },
  };
}

async function selectPhotoLetters(h, props) {
  h.render(PhotoLetterPanel, props);
  await h.settle();
  let tree = h.render(PhotoLetterPanel, props);
  for (const character of ["A", "g"]) {
    const label = nodes(tree).find(
      (node) => node.type === "label" && text(node).includes("Which character"),
    );
    nodes(label)
      .find((node) => node.type === "input")
      .props.onChange({ target: { value: character } });
    tree = h.render(PhotoLetterPanel, props);
    button(tree, "Add letter to batch").props.onClick();
    tree = h.render(PhotoLetterPanel, props);
  }
  return tree;
}

test("zero and partial photo batches keep rejected boxes and retry only the remaining letters", async () => {
  const restore = photoBrowser();
  try {
    for (const validCount of [0, 1]) {
      globalThis.localStorage = storage();
      const calls = [];
      let reviewCalls = 0;
      const h = harness();
      const props = photoProps(
        async (...args) => {
          calls.push(args);
          return {
            job_id: "33333333-3333-4333-8333-333333333333",
            state: "succeeded",
            stage: "review",
            progress: { completed_units: 1, total_units: 1 },
            quality: [
              {
                sample_id: args[1],
                regions: args[2].map((assignment, index) =>
                  index < validCount || calls.length > 1
                    ? {
                        region_id: assignment.region_id,
                        assignment,
                        status: "analyzed",
                        reasons: [],
                        outline_hash: `sha256:${"b".repeat(64)}`,
                      }
                    : {
                        region_id: assignment.region_id,
                        assignment,
                        status: "needs_better_input",
                        reasons: ["empty_ink"],
                      },
                ),
              },
            ],
          };
        },
        async () => {
          reviewCalls++;
          if (reviewCalls === 1) throw new Error("navigation interrupted");
        },
      );
      try {
        let tree = await selectPhotoLetters(h, props);
        button(tree, "Process 2 letters together").props.onClick();
        await h.settle();
        tree = h.render(PhotoLetterPanel, props);
        assert.equal(
          reviewCalls,
          0,
          "successful processing cannot discard failed regions or open Review automatically",
        );
        assert.ok(text(tree).includes(`${2 - validCount} / 96 letters`));
        assert.ok(text(tree).includes("No ink survived"));
        const restored = photoRecoveries.readPhotoRecovery(
          localStorage,
          {
            ownerUid: props.ownerUid,
            projectId: props.projectId,
            sampleId: props.sample.sample_id,
            digest: props.sample.source_sha256,
          },
          { width: 20, height: 20 },
        );
        assert.equal(restored.state, "ready");
        assert.equal(restored.draft.regions.length, 2 - validCount);
        assert.equal(restored.draft.attempt, null);
        if (validCount === 0) {
          assert.ok(!button(tree, "Review 0"));
          assert.equal(calls.length, 1);
        } else {
          button(tree, "Review 1 ready letter").props.onClick();
          await h.settle();
          tree = h.render(PhotoLetterPanel, props);
          assert.ok(text(tree).includes("Review could not be opened"));
          button(tree, "Review 1 ready letter").props.onClick();
          await h.settle();
          assert.equal(
            reviewCalls,
            2,
            "an interrupted finalization is caught and explicitly retryable",
          );
          tree = h.render(PhotoLetterPanel, props);
          button(tree, "Process 1 letter together").props.onClick();
          await h.settle();
          assert.equal(calls.length, 2);
          assert.equal(calls[1][2].length, 1);
          assert.equal(
            calls[1][2][0].region_id,
            calls[0][2][1].region_id,
            "accepted letters are not processed again",
          );
          assert.equal(reviewCalls, 3);
        }
      } finally {
        h.dispose();
      }
    }
  } finally {
    restore();
  }
});

test("photo processing never starts when its exact recovery request cannot be saved", async () => {
  const restore = photoBrowser();
  const h = harness();
  globalThis.localStorage = {
    ...storage(),
    setItem() {
      throw new Error("quota exceeded");
    },
  };
  let calls = 0;
  const props = photoProps(async () => {
    calls++;
    throw new Error("unexpected request");
  });
  try {
    const tree = await selectPhotoLetters(h, props);
    button(tree, "Process 2 letters together").props.onClick();
    await h.settle();
    assert.equal(calls, 0);
    assert.ok(
      text(h.render(PhotoLetterPanel, props)).includes(
        "No new processing was started",
      ),
    );
  } finally {
    h.dispose();
    restore();
  }
});
