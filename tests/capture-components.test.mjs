import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { setImmediate } from "node:timers/promises";
import { test } from "node:test";
import ts from "typescript";
import {
  javascriptModuleUrl,
  createTypeScriptLoader,
} from "./helpers/typescript.mjs";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";

const hooks = javascriptModuleUrl(`
export const useState=(...args)=>globalThis.__captureComponents.state(...args);
export const useRef=(...args)=>globalThis.__captureComponents.ref(...args);
export const useEffect=(...args)=>globalThis.__captureComponents.effect(...args);
export const useCallback=(fn,deps)=>globalThis.__captureComponents.memo(()=>fn,deps);
export const useId=()=>globalThis.__captureComponents.id();
`);
const jsx = javascriptModuleUrl(`
export const jsx=(type,props)=>({type,props});
export const jsxs=jsx; export const Fragment="fragment";
`);
const styles = javascriptModuleUrl("export default {};");
const children = javascriptModuleUrl(`
export const IndicGeometryReview=()=>null; export const JobMonitor=()=>null;
export const MeasuredStyle=()=>null;
`);
const geometry = javascriptModuleUrl(`
export const outlinePath=()=>""; export const outlineViewBox=()=>"0 0 1 1";
export const transformOutline=(value)=>value;
export const fitOutlineSpacing=(value)=>value;
`);
const extraction = javascriptModuleUrl(`
export class ExtractionError extends Error {};
export const extractionErrorText=()=>"Review could not load. Retry.";
`);
const load = createTypeScriptLoader(import.meta.url);
const strokeCapture = await load("../src/lib/stroke-capture.ts");

async function component(path) {
  let source = ts.transpileModule(
    await readFile(new URL(path, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
  for (const [from, to] of [
    ["react/jsx-runtime", jsx],
    ["react", hooks],
    ["../../../packages/contracts/src/unicode", unicodeModuleUrl],
    ["@/lib/stroke-capture", strokeCapture],
    ["@/lib/canonical-geometry", geometry],
    ["@/lib/extraction-client", extraction],
    ["./IndicGeometryReview", children],
    ["./JobMonitor", children],
    ["./MeasuredStyle", children],
    ["./CloudReviewPanel.module.css", styles],
  ])
    source = source.replaceAll(JSON.stringify(from), JSON.stringify(to));
  return import(javascriptModuleUrl(source));
}
const { DrawingCanvas } = await component(
  "../src/components/creation/DrawingCanvas.tsx",
);
const { CloudReviewPanel } = await component(
  "../src/components/creation/CloudReviewPanel.tsx",
);

function harness(Component) {
  const cells = [];
  let cursor = 0;
  let pending = [];
  const same = (a, b) =>
    a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const h = {
    state(initial) {
      const index = cursor++;
      if (!cells[index]) cells[index] = { value: initial };
      return [
        cells[index].value,
        (next) => {
          cells[index].value =
            typeof next === "function" ? next(cells[index].value) : next;
        },
      ];
    },
    ref(initial) {
      const index = cursor++;
      if (!cells[index]) cells[index] = { value: { current: initial } };
      return cells[index].value;
    },
    memo(fn, deps) {
      const index = cursor++;
      if (!same(cells[index]?.deps, deps)) cells[index] = { value: fn(), deps };
      return cells[index].value;
    },
    effect(fn, deps) {
      const index = cursor++;
      if (!same(cells[index]?.deps, deps)) {
        const cleanup = cells[index]?.cleanup;
        cells[index] = { deps };
        pending.push(() => {
          cleanup?.();
          cells[index].cleanup = fn();
        });
      }
    },
    id() {
      return h.memo(() => `test-${cursor}`, []);
    },
    render(props) {
      cursor = 0;
      globalThis.__captureComponents = h;
      const tree = Component(props);
      const effects = pending;
      pending = [];
      for (const effect of effects) effect();
      return tree;
    },
    unmount() {
      for (const cell of cells) cell.cleanup?.();
    },
  };
  return h;
}
function nodes(tree, predicate) {
  if (!tree || typeof tree !== "object") return [];
  if (Array.isArray(tree))
    return tree.flatMap((item) => nodes(item, predicate));
  return [
    ...(predicate(tree) ? [tree] : []),
    ...nodes(tree.props?.children, predicate),
  ];
}
function client(sourceId, samples) {
  const calls = [];
  return {
    calls,
    samples: async (id, signal) => {
      calls.push({ id, signal });
      return samples
        ? samples()
        : [
            {
              sample_id: sourceId,
              source_type: "canvas_drawing",
              capture_size_px: { width: 200, height: 100 },
            },
          ];
    },
    glyphs: async () => [],
    coverage: async () => ({
      script_code: "Latn",
      core_approved: 0,
      core_required: 52,
      ascii_approved: 0,
      ascii_required: 95,
      user_approved: [],
    }),
    followUp: async () => null,
    styleProfile: async () => null,
    capabilities: async () => [],
  };
}
const reviewProps = (backend, extra = {}) => ({
  client: backend,
  projectId: "project-a",
  ownerUid: "owner-a",
  operations: [],
  startOpen: true,
  ...extra,
});

test("opened review loads saved sources automatically without repeating for callback changes", async () => {
  const h = harness(CloudReviewPanel);
  const backend = client("saved-source");
  const changed = [];
  h.render(
    reviewProps(backend, { onChanged: (...values) => changed.push(values) }),
  );
  await setImmediate();
  const tree = h.render(reviewProps(backend, { onChanged: () => {} }));
  assert.equal(backend.calls.length, 1);
  assert.equal(changed.length, 1);
  const options = nodes(tree, (node) => node.type === "option");
  assert.ok(options.some((node) => node.props.value === "saved-source"));
  assert.ok(
    !options.some(
      (node) => node.props.children === "Save a private source first",
    ),
  );
  h.unmount();
});

test("closed review stays idle until explicitly opened", async () => {
  const h = harness(CloudReviewPanel);
  const backend = client("source");
  const tree = h.render(reviewProps(backend, { startOpen: false }));
  await setImmediate();
  assert.equal(backend.calls.length, 0);
  nodes(tree, (node) => node.type === "button")[0].props.onClick();
  await setImmediate();
  assert.equal(backend.calls.length, 1);
  h.unmount();
});

test("late source responses cannot repopulate review after account/project changes", async () => {
  let resolveOld;
  const oldSamples = new Promise((resolve) => {
    resolveOld = resolve;
  });
  const old = client("old", () => oldSamples);
  const next = client("new-source");
  const oldChanges = [];
  const newChanges = [];
  const h = harness(CloudReviewPanel);
  h.render(
    reviewProps(old, { onChanged: (...values) => oldChanges.push(values) }),
  );
  h.render(
    reviewProps(next, {
      ownerUid: "owner-b",
      projectId: "project-b",
      onChanged: (...values) => newChanges.push(values),
    }),
  );
  await setImmediate();
  assert.equal(old.calls[0].signal.aborted, true);
  resolveOld([
    { sample_id: "old-private-source", source_type: "canvas_drawing" },
  ]);
  await setImmediate();
  const tree = h.render(
    reviewProps(next, { ownerUid: "owner-b", projectId: "project-b" }),
  );
  const options = nodes(tree, (node) => node.type === "option");
  assert.ok(options.some((node) => node.props.value === "new-source"));
  assert.ok(!options.some((node) => node.props.value === "old-private-source"));
  assert.equal(oldChanges.length, 0);
  assert.equal(newChanges.length, 1);
  h.unmount();
});

test("failed initial review clears busy state and can be retried", async () => {
  let fail = true;
  const backend = client("source", () => {
    if (fail) throw new Error("private failure");
    return [{ sample_id: "recovered-source", source_type: "canvas_drawing" }];
  });
  const h = harness(CloudReviewPanel);
  h.render(reviewProps(backend));
  await setImmediate();
  let tree = h.render(reviewProps(backend));
  assert.ok(nodes(tree, (node) => node.props?.role === "alert").length);
  const refresh = nodes(tree, (node) => node.type === "button")[0];
  assert.equal(refresh.props.disabled, false);
  fail = false;
  refresh.props.onClick();
  await setImmediate();
  tree = h.render(reviewProps(backend));
  assert.ok(
    nodes(tree, (node) => node.type === "option").some(
      (node) => node.props.value === "recovered-source",
    ),
  );
  h.unmount();
});

function draw(accept) {
  const h = harness(DrawingCanvas);
  const writes = [];
  const observed = [];
  const tree = h.render({
    strokes: [],
    onChange: (value) => writes.push(value),
    onObservedStroke: (value, size) => {
      observed.push({ value, size });
      return accept;
    },
    label: "Draw a letter",
  });
  const canvas = nodes(tree, (node) => node.type === "canvas")[0];
  const element = {
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 100 }),
    focus() {},
    setPointerCapture() {},
  };
  const event = (x, timeStamp) => ({
    isPrimary: true,
    button: 0,
    pointerId: 7,
    pointerType: "mouse",
    clientX: x,
    clientY: 30,
    timeStamp,
    currentTarget: element,
    preventDefault() {},
  });
  canvas.props.onPointerDown(event(20, 10));
  canvas.props.onPointerUp(event(40, 20));
  h.unmount();
  return { writes, observed };
}
globalThis.ResizeObserver ??= class {
  observe() {}
  disconnect() {}
};

test("rejected raw stroke never appears in the committed visible drawing", () => {
  const { writes, observed } = draw(false);
  assert.equal(observed.length, 1);
  assert.deepEqual(writes, []);
});
test("accepted raw stroke commits the corresponding visible drawing", () => {
  const { writes, observed } = draw(true);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].length, 1);
  assert.deepEqual(
    writes[0][0],
    observed[0].value.points.map(({ x, y }) => ({ x, y })),
  );
});
test("legacy observer callbacks returning void retain normal drawing behavior", () => {
  assert.equal(draw(undefined).writes.length, 1);
});
