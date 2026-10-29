import assert from "node:assert/strict";
import { test } from "node:test";
import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";
import { writingTextModuleUrl } from "./helpers/writing-text.mjs";
const moduleUrl = createTypeScriptLoader(import.meta.url);
const {
  writingSelection,
  styleWritingSelection,
  normalizeWritingStyles,
  rebaseWritingStyles,
} = await import(writingTextModuleUrl);
const {
  DEFAULT_WRITING_DOCUMENT: defaults,
  updateWritingDocument,
  normalizeWritingDocument,
  serializeWritingDraft,
  readWritingDraft,
  layoutWritingDocument,
  buildWritingSvg,
  exportWritingSvg,
} = await import(
  await moduleUrl("../src/lib/writing.ts", [
    ["../../packages/contracts/src/unicode", unicodeModuleUrl],
    ["./writing-text", writingTextModuleUrl],
  ])
);
const selection = (start, end, field = "body") => ({ field, start, end });
const values = (body) => ({ title: "", recipient: "", body, signature: "" });
const measure = (text, size) =>
  [...text].reduce((sum, char) => sum + (char === " " ? 0.3 : 0.55) * size, 0);

test("selection uses native UTF-16 offsets without splitting emoji or Indic clusters", () => {
  const text = "a👩🏽‍💻क्षिe\u0301!";
  const emojiEnd = 1 + "👩🏽‍💻".length;
  assert.deepEqual(
    writingSelection(values(text), selection(2, 3)),
    selection(1, emojiEnd),
  );
  assert.deepEqual(
    writingSelection(values(text), selection(emojiEnd + 1, emojiEnd + 2)),
    selection(emojiEnd, emojiEnd + "क्षि".length),
  );
  assert.equal(writingSelection(values(text), selection(3, 3)), null);
  assert.equal(
    normalizeWritingStyles([{ ...selection(2, 3), weight: 600 }], values(text))
      .length,
    0,
  );
});

test("overlapping edits split and merge, preserving unrelated properties and fields", () => {
  let styles = styleWritingSelection([], selection(0, 8), { weight: 600 });
  styles = styleWritingSelection(styles, selection(3, 5), { spacing: 2 });
  assert.deepEqual(styles, [
    { ...selection(0, 3), weight: 600 },
    { ...selection(3, 5), weight: 600, spacing: 2 },
    { ...selection(5, 8), weight: 600 },
  ]);
  styles = styleWritingSelection(styles, selection(0, 8), { spacing: 2 });
  assert.deepEqual(styles, [{ ...selection(0, 8), weight: 600, spacing: 2 }]);
  styles = styleWritingSelection(styles, selection(1, 2, "title"), {
    fontSize: 70,
  });
  styles = styleWritingSelection(styles, selection(2, 6), null);
  assert.deepEqual(styles, [
    { ...selection(1, 2, "title"), fontSize: 70 },
    { ...selection(0, 2), weight: 600, spacing: 2 },
    { ...selection(6, 8), weight: 600, spacing: 2 },
  ]);
});

test("range overflow fails before replacing existing edits; corrupt styles never execute", () => {
  const styles = Array.from({ length: 128 }, (_, i) => ({
    ...selection(i * 2, i * 2 + 1),
    weight: 600,
  }));
  assert.throws(
    () => styleWritingSelection(styles, selection(300, 301), { spacing: 3 }),
    /selection limit/,
  );
  assert.equal(styles.length, 128);
  assert.deepEqual(
    normalizeWritingStyles(
      [
        { ...selection(0, 1), spacing: Infinity },
        { ...selection(1, 2), fontSize: 121 },
        { ...selection(2, 3), baselineShift: -25 },
      ],
      values("abc"),
    ),
    [],
  );
});

test("text edits shift later styles and inherit only inside a styled range", () => {
  const styles = [{ ...selection(2, 6), weight: 600 }];
  assert.deepEqual(
    rebaseWritingStyles(styles, "body", "abcdefgh", "ZZabcdefgh"),
    [{ ...selection(4, 8), weight: 600 }],
  );
  assert.deepEqual(
    rebaseWritingStyles(styles, "body", "abcdefgh", "abcdZZefgh"),
    [{ ...selection(2, 8), weight: 600 }],
  );
  assert.deepEqual(
    rebaseWritingStyles(styles, "body", "abcdefgh", "abZZcdefgh"),
    [{ ...selection(4, 8), weight: 600 }],
  );
  assert.deepEqual(rebaseWritingStyles(styles, "body", "abcdefgh", "abgh"), []);
  const doc = { ...defaults, body: "abcdefgh", textStyles: styles };
  const next = updateWritingDocument(doc, {
    body: "Zabcdefgh",
    format: "card",
  });
  assert.deepEqual(next.textStyles, [{ ...selection(3, 7), weight: 600 }]);
  assert.equal(doc.body, "abcdefgh");
});

test("local draft and format switches preserve only valid selection styles", () => {
  const doc = normalizeWritingDocument({
    ...defaults,
    body: "hello !",
    textStyles: [
      { ...selection(5, 6), spacing: 10 },
      { ...selection(6, 7), baselineShift: 8 },
    ],
  });
  assert.deepEqual(readWritingDraft(serializeWritingDraft(doc)), doc);
  assert.deepEqual(
    updateWritingDocument(doc, { format: "letter" }).textStyles,
    doc.textStyles,
  );
  assert.equal(
    normalizeWritingDocument({ ...defaults, textStyles: [] }).textStyles,
    undefined,
  );
});

test("a selected single or repeated space changes advances without changing text", () => {
  const doc = {
    ...defaults,
    title: "",
    recipient: "",
    signature: "",
    body: "a  b",
  };
  const base = layoutWritingDocument(doc, { measureText: measure });
  const styled = layoutWritingDocument(
    { ...doc, textStyles: [{ ...selection(1, 3), spacing: 8 }] },
    { measureText: measure },
  );
  assert.equal(
    styled.blocks[2].layout[0].width - base.blocks[2].layout[0].width,
    16,
  );
  assert.equal(styled.bodyLines[0], "a  b");
  const svg = buildWritingSvg(styled.document, { measureText: measure });
  assert.match(svg, /letter-spacing="8">  <\/tspan>/);
});

test("large raised and lowered selections reflow and expand every stationery format", () => {
  for (const format of ["note", "letter", "card"]) {
    const doc = {
      ...defaults,
      format,
      body: "Wide text. ".repeat(50),
      textStyles: [
        { ...selection(0, 300), fontSize: 120, baselineShift: 24 },
        { ...selection(300, 550), fontSize: 110, baselineShift: -24 },
      ],
    };
    const page = layoutWritingDocument(doc, { measureText: measure });
    assert.ok(
      page.bodyLines.length >
        layoutWritingDocument(
          { ...doc, textStyles: [] },
          { measureText: measure },
        ).bodyLines.length,
    );
    for (const block of page.blocks.filter((block) => block.lines.length)) {
      assert.ok(block.bounds.top > 25, `${format} ${block.field} top`);
      assert.ok(
        block.bounds.bottom < page.height - 30,
        `${format} ${block.field} bottom`,
      );
      assert.ok(
        block.bounds.left >= 20 && block.bounds.right <= page.width - 20,
      );
    }
    const body = page.blocks[2];
    assert.ok(body.bounds.bottom < page.blocks[3].bounds.top);
  }
});

test("preview selection marks never enter SVG exports and style runs escape text", async () => {
  const doc = {
    ...defaults,
    body: "hello <&",
    textStyles: [{ ...selection(6, 8), fontSize: 64, weight: 700 }],
  };
  const preview = buildWritingSvg(doc, {
    measureText: measure,
    selection: selection(0, 5),
  });
  assert.match(preview, /data-writing-selection="true"/);
  assert.match(preview, /&lt;&amp;<\/tspan>/);
  const font = {
    fontDataUrl: "data:font/woff2;base64,YQ==",
    fontFamily: "TestFont",
    measureText: measure,
  };
  const exported = await (await exportWritingSvg(doc, font)).text();
  assert.doesNotMatch(exported, /data-writing-selection|data-writing-guides/);
  assert.match(exported, /font-size="64" font-weight="700"/);
});
