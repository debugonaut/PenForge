import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";
const base = new URL("../packages/contracts/", import.meta.url);
const load = async (file) => readFile(new URL(file, base), "utf8");
const unicode = await import(unicodeModuleUrl);
test("Python and TypeScript preserve scalar/grapheme/normalization evidence and hashes", async () => {
  for (const row of JSON.parse(
    await load("fixtures/unicode-sequences.v1.json"),
  ))
    assert.deepEqual(await unicode.sequenceEvidence(row.text), row.evidence);
  assert.throws(() => unicode.codepoints("\ud800"));
});
test("TypeScript passes the pinned full UAX29 grapheme conformance suite", async () => {
  for (const line of (await load("unicode/17.0.0/GraphemeBreakTest.txt")).split(
    "\n",
  )) {
    const body = line.split("#")[0].trim();
    if (!body) continue;
    const expected = [];
    let current = [];
    for (const item of body.split(/\s+/)) {
      if (item === "÷") {
        if (current.length) expected.push(current);
        current = [];
      } else if (item !== "×") current.push(parseInt(item, 16));
    }
    assert.deepEqual(
      unicode.graphemes(String.fromCodePoint(...expected.flat())),
      expected,
      line,
    );
  }
});

test("capture accepts complete clusters and preserves joiners without truncation", () => {
  assert.deepEqual(
    unicode.captureCodepoints("क्ष", "Deva"),
    [2325, 2381, 2359],
  );
  assert.deepEqual(unicode.captureCodepoints("क़", "Deva"), [2392]);
  assert.throws(() => unicode.captureCodepoints("क्ष", "Latn"));
  assert.throws(() => unicode.captureCodepoints("क\u202e", "Deva"));
});
