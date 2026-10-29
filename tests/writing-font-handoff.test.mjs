import assert from "node:assert/strict";
import { test } from "node:test";
import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";
import { writingTextModuleUrl } from "./helpers/writing-text.mjs";

const load = createTypeScriptLoader(import.meta.url);
const {
  clearGeneratedWritingReference,
  requestedWritingExport,
  writingFontReferenceTransition,
  WritingFontHandoff,
} = await import(await load("../src/lib/writing-font-handoff.ts"));
const writing = await import(
  await load("../src/lib/writing.ts", [
    ["../../packages/contracts/src/unicode", unicodeModuleUrl],
    ["./writing-text", writingTextModuleUrl],
  ])
);
const requested = "77a216e6-e211-4358-843c-5d0f96a4abd8";
const saved = "11111111-1111-4111-8111-111111111111";
const state = {
  ready: true,
  accountRestored: true,
  settled: true,
  restoring: false,
  configured: true,
  uid: "writer-a",
  busy: false,
  exportId: requested,
};

test("an explicit private export handoff wins the previous local font reference", () => {
  assert.equal(
    requestedWritingExport(`?export=${requested}`, saved),
    requested,
  );
  assert.equal(
    requestedWritingExport(`?export=${requested.toUpperCase()}`, saved),
    requested,
  );
  assert.equal(requestedWritingExport("?text=My+own+words", saved), saved);
  assert.equal(
    requestedWritingExport(
      "?export=------------------------------------",
      saved,
    ),
    saved,
  );
  assert.equal(requestedWritingExport("?export=not-an-export"), null);
  assert.equal(requestedWritingExport(`?export=${requested}extra`), null);
});

test("font bytes wait for draft hydration, settled identity and account document restoration", () => {
  for (const blocked of [
    { ready: false },
    { accountRestored: false },
    { settled: false },
    { restoring: true },
    { configured: false },
    { uid: null },
    { busy: true },
    { exportId: null },
  ]) {
    const handoff = new WritingFontHandoff();
    assert.equal(handoff.take({ ...state, ...blocked }), null);
    assert.equal(
      handoff.take(state),
      requested,
      "a transient gate must not consume the one pending handoff",
    );
  }
});

test("completion and fetch errors cannot trigger an automatic font reload loop", () => {
  const handoff = new WritingFontHandoff();
  assert.equal(handoff.take(state), requested);
  assert.equal(handoff.take({ ...state, busy: true }), null);
  assert.equal(handoff.take(state), null);
  assert.equal(handoff.take({ ...state, ready: false }), null);
  assert.equal(
    handoff.take(state),
    null,
    "restoring busy/ready after failure does not repeatedly fetch private bytes",
  );
});

test("an explicit manual font choice cancels a delayed handoff without disabling later account restoration", () => {
  const handoff = new WritingFontHandoff();
  assert.equal(handoff.take({ ...state, settled: false }), null);
  handoff.dismiss();
  assert.equal(handoff.take(state), null);
  assert.equal(handoff.take({ ...state, exportId: saved }), null);
  handoff.reset();
  assert.equal(
    handoff.take({ ...state, accountRestored: false, uid: "writer-b" }),
    null,
  );
  assert.equal(handoff.take({ ...state, uid: "writer-b" }), requested);
});

test("opening another saved page can load its own font after outstanding work finishes", () => {
  const handoff = new WritingFontHandoff();
  assert.equal(handoff.take(state), requested);
  handoff.reset();
  const accountFont = requestedWritingExport("", saved);
  assert.equal(
    handoff.take({ ...state, exportId: accountFont, busy: true }),
    null,
  );
  assert.equal(handoff.take({ ...state, exportId: accountFont }), saved);
  assert.equal(handoff.take({ ...state, exportId: accountFont }), null);
});

test("choosing a non-generated face clears its persisted reference while retaining every word and layout control", () => {
  const original = writing.normalizeWritingDocument({
    ...writing.DEFAULT_WRITING_DOCUMENT,
    body: "Keep my actual words, punctuation and spaces.",
    title: "My private note",
    generatedExportId: requested,
    projectId: "project-a",
    ink: "#315c49",
    fontSize: 42,
    textStyles: [{ start: 0, end: 4, fontSize: 48 }],
  });
  const cleared = clearGeneratedWritingReference(original);
  assert.equal(
    original.generatedExportId,
    requested,
    "prior history stays immutable",
  );
  assert.equal(Object.hasOwn(cleared, "generatedExportId"), false);
  for (const key of Object.keys(original).filter(
    (key) => key !== "generatedExportId",
  ))
    assert.deepEqual(cleared[key], original[key]);
  const restored = writing.readWritingDraft(
    writing.serializeWritingDraft(cleared),
  );
  assert.equal(restored.generatedExportId, undefined);
  assert.equal(
    requestedWritingExport("", restored.generatedExportId),
    null,
    "refresh cannot reselect a discarded private font",
  );
  assert.equal(restored.body, original.body);
  assert.deepEqual(restored.textStyles, original.textStyles);
});

test("switching to a saved page with another generated font clears the previous face first", () => {
  const transition = writingFontReferenceTransition(requested, saved, requested);
  assert.deepEqual(transition, {
    changed: true,
    nextExport: saved,
    clearFace: true,
  });
});


test("restoring the same page keeps its loaded generated font without a reload", () => {
  assert.deepEqual(writingFontReferenceTransition(requested, requested, requested), {
    changed: false,
    nextExport: requested,
    clearFace: false,
  });
  // The target is already loaded, so even a changed reference cannot flash blank.
  assert.deepEqual(writingFontReferenceTransition(saved, requested, requested), {
    changed: true,
    nextExport: requested,
    clearFace: false,
  });
});


test("undo, redo and version restoration reconcile a removed or reinstated generated font", () => {
  // Undo back to a state without the reference: the loaded face must go.
  assert.deepEqual(writingFontReferenceTransition(requested, undefined, requested), {
    changed: true,
    nextExport: null,
    clearFace: true,
  });
  // Redo restores the reference: no face is loaded, so it may be fetched fresh.
  assert.deepEqual(writingFontReferenceTransition(undefined, saved, null), {
    changed: true,
    nextExport: saved,
    clearFace: true,
  });
  // A restoration that keeps the same font leaves the working face alone.
  assert.deepEqual(writingFontReferenceTransition(saved, saved, saved), {
    changed: false,
    nextExport: saved,
    clearFace: false,
  });
});


test("a stale loaded face is dropped even when the saved reference itself is unchanged", () => {
  assert.deepEqual(writingFontReferenceTransition(saved, saved, requested), {
    changed: false,
    nextExport: saved,
    clearFace: true,
  });
});


test("a successful device font replacement drops the private reference; an import failure leaves its previous document intact", () => {
  const original = writing.normalizeWritingDocument({
    ...writing.DEFAULT_WRITING_DOCUMENT,
    generatedExportId: requested,
    body: "Words survive a font replacement.",
  });
  // This is the same canonical update used only after importFont has loaded the new bytes.
  const imported = writing.updateWritingDocument(original, {
    projectId: undefined,
    generatedExportId: undefined,
  });
  assert.equal(imported.generatedExportId, undefined);
  assert.equal(
    writing.readWritingDraft(writing.serializeWritingDraft(imported))
      .generatedExportId,
    undefined,
  );
  assert.equal(imported.body, original.body);
  assert.equal(
    original.generatedExportId,
    requested,
    "a failed loader makes no canonical document update",
  );
});
