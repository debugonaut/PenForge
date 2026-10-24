import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";

const moduleUrl = createTypeScriptLoader(import.meta.url);
const writingUrl = await moduleUrl("../src/lib/writing.ts", [
  ["../../packages/contracts/src/unicode", unicodeModuleUrl],
]);
const { DEFAULT_WRITING_DOCUMENT, normalizeWritingDocument } = await import(
  writingUrl
);
const {
  WRITING_PALETTES,
  WRITING_TEMPLATES,
  WRITING_SNIPPETS_KEY,
  MAX_WRITING_SNIPPETS,
  WRITING_VERSIONS_KEY,
  MAX_WRITING_VERSIONS,
  appendWritingSnippet,
  appendWritingVersion,
  applyWritingTemplate,
  filterWritingPages,
  readWritingSnippets,
  readWritingVersions,
  removeWritingSnippet,
  replaceWritingMatches,
  writingCoverageSet,
  writingMatchCount,
  writingMissingCharacters,
  writingPreviewPercent,
  writingUsedCharacters,
  writingWhenLabel,
  writingWordCount,
} = await import(
  await moduleUrl("../src/lib/writing-desk.ts", [
    ['"./writing"', JSON.stringify(writingUrl)],
    ["../../packages/contracts/src/unicode", unicodeModuleUrl],
  ])
);

test("palettes pair a distinct accent and ink without duplicating ids", () => {
  assert.equal(WRITING_PALETTES.length, 3);
  assert.equal(new Set(WRITING_PALETTES.map((item) => item.id)).size, 3);
  for (const palette of WRITING_PALETTES) {
    assert.match(palette.accent, /^#[0-9a-f]{6}$/);
    assert.match(palette.ink, /^#[0-9a-f]{6}$/);
    assert.notEqual(palette.accent, palette.ink);
  }
});

test("find and replace counts across words and inserts replacement literally", () => {
  const document = normalizeWritingDocument({
    ...DEFAULT_WRITING_DOCUMENT,
    title: "Little note",
    body: "A little note about a little thing.",
  });
  assert.equal(writingMatchCount(document, "little"), 3);
  assert.equal(writingMatchCount(document, "little", true), 2);
  assert.equal(writingMatchCount(document, ""), 0);

  const replaced = replaceWritingMatches(
    document,
    "little",
    "big $& note",
    false,
  );
  assert.ok(replaced);
  assert.equal(replaced.title, "big $& note note");
  assert.ok(replaced.body.includes("about a big $& note thing."));
  assert.equal(writingMatchCount(replaced, "little"), 0);
  assert.equal(replaceWritingMatches(document, "", "x"), null);
  assert.equal(replaceWritingMatches(document, "absent", "x"), null);
});

test("word count covers the note and sign-off, not the greeting or title", () => {
  assert.equal(writingWordCount(DEFAULT_WRITING_DOCUMENT), 24);
  assert.equal(
    writingWordCount(
      normalizeWritingDocument({ body: "  ", signature: "" }),
    ),
    0,
  );
});

test("the preview percentage follows the fitted sheet and zoom", () => {
  assert.equal(writingPreviewPercent({ height: 507 }, 650, 1), 78);
  assert.equal(writingPreviewPercent({ height: 507 }, 650, 1.25), 98);
  assert.equal(writingPreviewPercent(null, 650, 1.25), 125);
  assert.equal(writingPreviewPercent({ height: 100 }, 0, 1), 100);
});

test("local versions tolerate corrupt records and stay newest-first and capped", () => {
  assert.deepEqual(readWritingVersions(null), []);
  assert.deepEqual(readWritingVersions("{not json"), []);
  const document = normalizeWritingDocument({ body: "Kept words" });
  const older = {
    id: "older",
    at: 1,
    title: "Older",
    format: "note",
    document,
  };
  const newer = {
    id: "newer",
    at: 2,
    title: "Newer",
    format: "note",
    document: normalizeWritingDocument({ body: "New words" }),
  };
  const parsed = readWritingVersions(
    JSON.stringify([older, { nope: true }, newer]),
  );
  assert.deepEqual(
    parsed.map((item) => item.id),
    ["newer", "older"],
  );
  assert.equal(parsed[1].document.body, "Kept words");
  assert.equal(WRITING_VERSIONS_KEY, "penforge-writing-versions-v1");

  const capped = Array.from({ length: MAX_WRITING_VERSIONS + 4 }, (_, index) => ({
    id: `v${index}`,
    at: index,
    title: `v${index}`,
    format: "note",
    document: normalizeWritingDocument({ body: `Words ${index}` }),
  }));
  const stored = readWritingVersions(JSON.stringify(capped));
  assert.equal(stored.length, MAX_WRITING_VERSIONS);
  assert.equal(stored[0].id, `v${MAX_WRITING_VERSIONS + 3}`);

  const appended = appendWritingVersion(stored, {
    id: "duplicate",
    at: 99,
    title: "Duplicate",
    format: "note",
    document: stored[0].document,
  });
  assert.equal(appended[0].id, "duplicate");
  assert.equal(
    appended.filter(
      (item) =>
        JSON.stringify(item.document) === JSON.stringify(stored[0].document),
    ).length,
    1,
  );
});

test("occasion templates fill only the words and preserve the desk settings", () => {
  assert.equal(WRITING_TEMPLATES[0].id, "blank");
  assert.ok(WRITING_TEMPLATES.length >= 5);
  assert.equal(new Set(WRITING_TEMPLATES.map((t) => t.id)).size, WRITING_TEMPLATES.length);
  const document = normalizeWritingDocument({
    ...DEFAULT_WRITING_DOCUMENT,
    paper: "kraft",
    ink: "#74434b",
    accent: "#507662",
    fontSize: 56,
    slant: -6,
  });
  const filled = applyWritingTemplate(document, "thankyou");
  assert.ok(filled);
  assert.equal(filled.title, "Thank you");
  assert.ok(filled.body.includes("Thank you for"));
  assert.equal(filled.paper, "kraft");
  assert.equal(filled.ink, "#74434b");
  assert.equal(filled.accent, "#507662");
  assert.equal(filled.fontSize, 56);
  assert.equal(filled.slant, -6);
  const blank = applyWritingTemplate(document, "blank");
  assert.ok(blank);
  assert.equal(blank.body, "");
  assert.equal(blank.signature, "");
  assert.equal(blank.paper, "kraft");
  assert.equal(applyWritingTemplate(document, "nope"), null);
});

test("saved sign-offs tolerate corrupt records and stay deduplicated and capped", () => {
  assert.deepEqual(readWritingSnippets(null), []);
  assert.deepEqual(readWritingSnippets("{not json"), []);
  assert.deepEqual(readWritingSnippets('[{"text":"   "}]'), []);
  const parsed = readWritingSnippets(
    JSON.stringify([
      { id: "a", text: "With love,\nMe", savedAt: 2 },
      { text: "  Cheers, Sam  ", savedAt: 3 },
      { text: "Cheers, Sam" },
      { nope: true },
      { text: "x".repeat(300) },
    ]),
  );
  assert.equal(parsed.length, 2);
  assert.equal(parsed[0].text, "Cheers, Sam");
  assert.equal(WRITING_SNIPPETS_KEY, "penforge-writing-snippets-v1");

  let snippets = appendWritingSnippet(parsed, "Best, Ada");
  assert.equal(snippets[0].text, "Best, Ada");
  snippets = appendWritingSnippet(snippets, "Best, Ada");
  assert.equal(
    snippets.filter((snippet) => snippet.text === "Best, Ada").length,
    1,
  );
  assert.equal(appendWritingSnippet(snippets, "   ").length, snippets.length);
  const capped = Array.from(
    { length: MAX_WRITING_SNIPPETS + 3 },
    (_, index) => `Sign-off ${index}`,
  ).reduce((list, text) => appendWritingSnippet(list, text), []);
  assert.equal(capped.length, MAX_WRITING_SNIPPETS);
  // The newest sign-off leads and the oldest fall off the end.
  assert.equal(capped[0].text, `Sign-off ${MAX_WRITING_SNIPPETS + 2}`);
  const removed = removeWritingSnippet(parsed, parsed[0].id);
  assert.equal(removed.length, 1);
});

test("coverage intersects the font with the characters actually written", () => {
  const document = normalizeWritingDocument({
    ...DEFAULT_WRITING_DOCUMENT,
    title: "A note",
    recipient: "",
    body: "Café life",
    signature: "",
  });
  const used = writingUsedCharacters(document);
  assert.ok(used.includes("C"));
  assert.ok(used.includes("é"));
  assert.ok(!used.some((character) => /\s/.test(character)));
  assert.equal(new Set(used).size, used.length);

  const covered = writingCoverageSet(["ABCdef", "Café"]);
  assert.ok(covered.has("é"));
  assert.deepEqual(writingMissingCharacters(document, null), []);
  const missing = writingMissingCharacters(document, covered);
  assert.ok(missing.includes("o"));
  assert.ok(!missing.includes("C"));
  assert.ok(!missing.includes("é"));
});

test("the when-label is short, honest and bounded", () => {
  const now = 1_000_000_000_000;
  assert.equal(writingWhenLabel(0, now), "");
  assert.equal(writingWhenLabel(Number.NaN, now), "");
  assert.equal(writingWhenLabel(now - 10_000, now), "just now");
  assert.equal(writingWhenLabel(now - 5 * 60_000, now), "5 min ago");
  assert.equal(writingWhenLabel(now - 3 * 3_600_000, now), "3 h ago");
  assert.equal(writingWhenLabel(now - 26 * 3_600_000, now), "yesterday");
  assert.equal(writingWhenLabel(now - 5 * 86_400_000, now), "5 days ago");
});

test("library search matches titles and bodies; format and sort stay predictable", () => {
  const pages = [
    {
      id: "1",
      title: "Birthday note",
      format: "note",
      updatedAt: "2026-10-24T09:00:00Z",
      body: "Hope the day is kind",
    },
    {
      id: "2",
      title: "Thank you",
      format: "letter",
      updatedAt: "2026-10-23T09:00:00Z",
      body: "For the birthday cake",
    },
    {
      id: "3",
      title: "Grocery list",
      format: "card",
      updatedAt: "2026-10-22T09:00:00Z",
      body: "Milk, eggs",
    },
  ];
  assert.deepEqual(filterWritingPages(pages).map((p) => p.id), ["1", "2", "3"]);
  assert.deepEqual(
    filterWritingPages(pages, { query: "birthday" }).map((p) => p.id),
    ["1", "2"],
  );
  assert.deepEqual(
    filterWritingPages(pages, { query: "birthday", format: "letter" }).map((p) => p.id),
    ["2"],
  );
  assert.deepEqual(
    filterWritingPages(pages, { sort: "oldest" }).map((p) => p.id),
    ["3", "2", "1"],
  );
  assert.deepEqual(
    filterWritingPages(pages, { sort: "title" }).map((p) => p.id),
    ["1", "3", "2"],
  );
  assert.deepEqual(filterWritingPages(pages, { query: "zzz" }), []);
});
