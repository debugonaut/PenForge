import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";

const moduleUrl = createTypeScriptLoader(import.meta.url);
const {
  DEFAULT_WRITING_DOCUMENT: defaults,
  WRITING_STORAGE_KEY,
  normalizeWritingDocument,
  parseWritingQuery,
  serializeWritingDraft,
  readWritingDraft,
  wrapWritingText,
  estimateWritingWidth,
  layoutWritingDocument,
  fitWritingPage,
  writingPaperTone,
  writingContrastRatio,
  buildWritingSvg,
  exportWritingPng,
  readFontCoverage,
  writingLineSeed,
} = await import(await moduleUrl("../src/lib/writing.ts", [
  ["../../packages/contracts/src/unicode", unicodeModuleUrl],
]));

test("handoff fields are bounded and unrelated or unsafe query values are ignored", () => {
  const query = new URLSearchParams({
    weight: "900",
    spacing: "-8",
    slant: "Infinity",
    fontSize: "6",
    lineHeight: "8",
    ink: "#AB34EF",
    paper: "graph",
    project: "project-123",
    naturalness: "4",
    text: "x".repeat(900),
    title: "My handoff title",
  });
  const parsed = parseWritingQuery(query);
  assert.equal(parsed.weight, 700);
  assert.equal(parsed.naturalness, 1);
  assert.equal(parsed.spacing, -1);
  assert.equal(parsed.fontSize, 24);
  assert.equal(parsed.lineHeight, 1.8);
  assert.equal(parsed.slant, undefined);
  assert.equal(parsed.ink, "#ab34ef");
  assert.equal(parsed.paper, "graph");
  assert.equal(parsed.projectId, "project-123");
  assert.equal(parsed.body.length, 800);
  assert.equal(parsed.title, "My handoff title");
  assert.deepEqual(
    parseWritingQuery(
      "?ink=javascript:alert(1)&paper=unknown&project=../secret&weight=",
    ),
    {},
  );
});

test("writing drafts use an isolated versioned envelope and reject corrupt data", () => {
  assert.equal(WRITING_STORAGE_KEY, "penforge-writing-draft-v1");
  assert.notEqual(WRITING_STORAGE_KEY, "penforge-projects-v1");
  const saved = { ...defaults, body: "A note I want to keep", paper: "graph" };
  assert.deepEqual(readWritingDraft(serializeWritingDraft(saved)), saved);
  for (const raw of [
    "{",
    "null",
    "[]",
    JSON.stringify({ schemaVersion: 1, projects: [] }),
    JSON.stringify({ version: 2, document: saved }),
    JSON.stringify({ version: 1, document: { ...saved, body: 9 } }),
  ])
    assert.equal(readWritingDraft(raw), null);
});

test("normalization clamps numeric styles and preserves content while changing paper or format", () => {
  const content = {
    title: "My title",
    recipient: "Dear friend,",
    body: "Keep every word.\nAnd this paragraph.",
    signature: "Me",
  };
  const note = normalizeWritingDocument({
    ...defaults,
    ...content,
    weight: NaN,
    fontSize: 200,
    widthScale: 20,
    ink: '" onload="alert(1)',
  });
  assert.equal(note.weight, defaults.weight);
  assert.equal(note.fontSize, 72);
  assert.equal(note.widthScale, 1.2);
  assert.equal(note.ink, defaults.ink);
  for (const format of ["letter", "note", "card"]) {
    const changed = normalizeWritingDocument({
      ...note,
      format,
      paper: "kraft",
    });
    for (const key of Object.keys(content))
      assert.equal(changed[key], content[key]);
  }
});

test("wrapping preserves paragraphs and breaks long tokens without dropping letters", () => {
  const token = "Supercalifragilisticexpialidocious";
  const lines = wrapWritingText(token, 100, 40, 0);
  assert.ok(lines.length > 1);
  assert.equal(lines.join(""), token);
  assert.ok(lines.every((line) => estimateWritingWidth(line, 40, 0) <= 100));
  assert.deepEqual(wrapWritingText("first\n\nlast", 500, 40), [
    "first",
    "",
    "last",
  ]);
  assert.equal(
    wrapWritingText("one two three four", 100, 40).join(" "),
    "one two three four",
  );
});

test("the page grows to include all long writing and its signature", () => {
  const document = {
    ...defaults,
    format: "card",
    body: "W".repeat(800),
    fontSize: 72,
    lineHeight: 1.8,
    widthScale: 1.2,
    slant: 15,
  };
  const page = layoutWritingDocument(document);
  assert.ok(page.height > 680);
  assert.equal(page.bodyLines.join(""), document.body);
  assert.ok(
    page.signatureY + page.signatureLines.length * page.lineStep < page.height,
  );
  const available = (page.width - page.margin * 2) / document.widthScale;
  assert.ok(
    page.bodyLines.every(
      (line) =>
        estimateWritingWidth(line, document.fontSize, document.spacing) <=
        available,
    ),
  );
  const svg = buildWritingSvg(document);
  assert.ok(svg.includes(`height="${page.height}"`));
  assert.equal(
    (svg.match(/<text /g) || []).length,
    page.titleLines.length +
      page.recipientLines.length +
      page.bodyLines.length +
      page.signatureLines.length,
  );
});

test("SVG escapes all user strings and embeds supplied font and license without external resources", () => {
  const attack = '</text><script>alert("hello")</script>&';
  const svg = buildWritingSvg(
    {
      ...defaults,
      title: attack,
      recipient: attack,
      body: attack,
      signature: attack,
    },
    {
      fontDataUrl: "data:font/woff2;base64,d09GMg==",
      fontFamily: "MyEmbeddedFont",
      fontLicense: 'Copyright <font> & license "OFL"',
    },
  );
  assert.ok(!svg.includes("<script>"));
  assert.ok(svg.includes("&lt;script&gt;"));
  assert.ok(svg.includes("&amp;"));
  assert.ok(svg.includes("data:font/woff2;base64,d09GMg=="));
  assert.ok(
    svg.includes("Copyright &lt;font&gt; &amp; license &quot;OFL&quot;"),
  );
  assert.ok(!svg.includes("https://"));
  const unsafe = buildWritingSvg(defaults, {
    fontDataUrl: "javascript:alert(1)",
    fontFamily: "';}</style><script>",
  });
  assert.ok(!unsafe.includes("javascript:"));
  assert.ok(!unsafe.includes("<script>"));
});

test("graph and ruled paper have distinct geometry", () => {
  const graph = buildWritingSvg({ ...defaults, paper: "graph" });
  const ruled = buildWritingSvg({ ...defaults, paper: "ruled" });
  assert.ok(graph.includes('stroke="#a6bec2"'));
  assert.ok(graph.includes("M28 0V"));
  assert.ok(graph.includes("M0 28H"));
  assert.ok(!graph.includes('stroke="#dc9c91"'));
  assert.ok(ruled.includes('stroke="#dc9c91"'));
  assert.ok(ruled.includes('stroke="#abc6cf"'));
});

test("extreme blank-line documents remain intact and PNG fails explicitly before allocating a huge canvas", async () => {
  const document = {
    ...defaults,
    body: "\n".repeat(799),
    fontSize: 72,
    lineHeight: 1.8,
  };
  assert.equal(layoutWritingDocument(document).bodyLines.length, 800);
  await assert.rejects(exportWritingPng(document), /too tall for PNG/);
});

test("an immediate full-desk handoff carries every authored field without relying on a storage debounce", () => {
  const authored = {
    ...defaults,
    format: "card",
    title: "For you & me",
    recipient: "Dear friend,",
    body: "A tiny message.",
    signature: "Always,\nMe",
    alignment: "center",
    widthScale: 1.12,
    paper: "graph",
    ink: "#74434b",
    weight: 610,
    spacing: 1.4,
    slant: -7,
    fontSize: 51,
    lineHeight: 1.6,
    projectId: "my-project-123",
  };
  const query = new URLSearchParams({
    ...Object.fromEntries(
      Object.entries(authored)
        .filter(([key]) => !["body", "recipient", "projectId"].includes(key))
        .map(([key, value]) => [key, String(value)]),
    ),
    text: authored.body,
    to: authored.recipient,
    project: authored.projectId,
  });
  assert.deepEqual(
    normalizeWritingDocument({ ...defaults, ...parseWritingQuery(query) }),
    authored,
  );
});

test("custom font advances drive the shared SVG layout instead of Caveat estimates", () => {
  // A monospace i is approximately 0.6em, rather than the sample face's 0.23em.
  const monospaceMeasure = (value, size) =>
    Array.from(value).length * size * 0.6;
  const document = {
    ...defaults,
    body: "i".repeat(800),
    fontSize: 72,
    spacing: 2,
    widthScale: 1.2,
  };
  const estimated = layoutWritingDocument(document);
  const measured = layoutWritingDocument(document, {
    measureText: monospaceMeasure,
  });
  assert.ok(measured.bodyLines.length > estimated.bodyLines.length * 2);
  assert.equal(measured.bodyLines.join(""), document.body);
  const maximum = (measured.width - measured.margin * 2) / document.widthScale;
  assert.ok(
    measured.bodyLines.every(
      (line) =>
        monospaceMeasure(line, document.fontSize) +
          (line.length - 1) * document.spacing <=
        maximum,
    ),
  );
  const svg = buildWritingSvg(document, { measureText: monospaceMeasure });
  assert.ok(svg.includes(`height="${measured.height}"`));
  assert.equal(
    (svg.match(/<text /g) || []).length,
    measured.titleLines.length +
      measured.recipientLines.length +
      measured.bodyLines.length +
      measured.signatureLines.length,
  );
});

test("legacy drafts gain a safe accent and accent survives query and storage roundtrips", () => {
  const legacy = { ...defaults };
  delete legacy.accent;
  const migrated = readWritingDraft(
    JSON.stringify({ version: 1, document: legacy }),
  );
  assert.equal(migrated.accent, "#d7836f");
  assert.equal(migrated.body, legacy.body);
  assert.equal(migrated.signature, legacy.signature);
  assert.equal(
    normalizeWritingDocument({ ...defaults, accent: '" onload="x' }).accent,
    defaults.accent,
  );
  assert.equal(
    normalizeWritingDocument({ ...defaults, accent: "#A3C9B4" }).accent,
    "#a3c9b4",
  );
  assert.deepEqual(parseWritingQuery("accent=javascript%3Aalert(1)"), {});
  const authored = {
    ...defaults,
    accent: "#476c9b",
    title: "The color I chose",
    body: "Every word stays.",
  };
  const query = new URLSearchParams({
    accent: authored.accent,
    title: authored.title,
    text: authored.body,
  });
  assert.deepEqual(
    normalizeWritingDocument({ ...defaults, ...parseWritingQuery(query) }),
    authored,
  );
  assert.deepEqual(readWritingDraft(serializeWritingDraft(authored)), authored);
});

test("letter, note, and card are distinct designs preserving every authored field", () => {
  const authored = {
    title: "For us & our friends",
    recipient: "Dear Alex,",
    body: "Keep the first line.\n\nKeep the second line, too.",
    signature: "With all my love,\nRiver",
    accent: "#476c9b",
    ink: "#74434b",
  };
  const motifs = {
    letter: "personal-stationery",
    note: "memo-band",
    card: "greeting-frame",
  };
  const pages = {};
  for (const format of ["letter", "note", "card"]) {
    const document = { ...defaults, ...authored, format };
    const page = layoutWritingDocument(document);
    pages[format] = page;
    const svg = buildWritingSvg(document);
    for (const key of Object.keys(authored))
      assert.equal(page.document[key], authored[key]);
    assert.ok(svg.includes(`data-writing-design="${format}"`));
    assert.ok(svg.includes(`data-motif="${motifs[format]}"`));
    assert.ok(svg.includes('fill="#74434b"'));
    assert.ok(
      svg.includes('stroke="#476c9b"') || svg.includes('fill="#476c9b"'),
    );
    for (const block of page.blocks) {
      const escapedLines = [
        ...svg.matchAll(
          new RegExp(
            `<text data-field="${block.field}"[^>]*>(.*?)</text>`,
            "g",
          ),
        ),
      ].map((match) => match[1]);
      const escaped = block.lines.map((line) =>
        line.replace(
          /[&<>"']/g,
          (character) =>
            ({
              "&": "&amp;",
              "<": "&lt;",
              ">": "&gt;",
              '"': "&quot;",
              "'": "&apos;",
            })[character],
        ),
      );
      assert.deepEqual(escapedLines, escaped);
    }
    const visibleText = [...svg.matchAll(/<text\b[^>]*>(.*?)<\/text>/g)].map(
      (match) => match[1],
    );
    assert.ok(
      visibleText.every(
        (value) => !/Penforge|202\d|SUNDAY|GARDEN PARTY/.test(value),
      ),
    );
  }
  assert.ok(pages.letter.margin > pages.note.margin);
  assert.ok(pages.letter.height > pages.note.height);
  assert.ok(pages.card.width > pages.note.width);
  assert.equal(pages.letter.monogram, "R");
  assert.equal(
    pages.card.blocks.find((block) => block.field === "title").anchor,
    "middle",
  );
  assert.equal(
    pages.card.blocks.find((block) => block.field === "signature").anchor,
    "middle",
  );
  assert.equal(
    pages.card.blocks.find((block) => block.field === "body").anchor,
    "start",
  );
});

test("all formats grow around measured long content without clipping titles, body, or signature", () => {
  const measureText = (value, size, weight) =>
    Array.from(value).length * size * (0.7 + (weight - 400) / 10000);
  for (const format of ["letter", "note", "card"]) {
    for (const alignment of ["left", "center", "right"]) {
      for (const slant of [-15, 15]) {
        const document = {
          ...defaults,
          format,
          alignment,
          slant,
          title: "W".repeat(80),
          recipient: "M".repeat(100),
          body: "Wm".repeat(400),
          signature: "W".repeat(100),
          fontSize: 72,
          lineHeight: 1.8,
          weight: 700,
          widthScale: 1.2,
          spacing: 3,
        };
        const page = layoutWritingDocument(document, { measureText });
        assert.equal(page.bodyLines.join(""), document.body);
        assert.equal(page.titleLines.join(""), document.title);
        assert.equal(page.recipientLines.join(""), document.recipient);
        assert.equal(page.signatureLines.join(""), document.signature);
        for (const block of page.blocks) {
          assert.ok(
            block.bounds.left >= 0,
            `${format} ${alignment}: ${block.field} left`,
          );
          assert.ok(
            block.bounds.right <= page.width,
            `${format} ${alignment}: ${block.field} right`,
          );
          assert.ok(block.bounds.top >= 0, `${format}: ${block.field} top`);
          assert.ok(
            block.bounds.bottom < page.height,
            `${format}: ${block.field} bottom`,
          );
          for (const line of block.lines) {
            assert.ok(
              measureText(line, block.size, block.weight) +
                Math.max(0, Array.from(line).length - 1) * document.spacing <=
                page.availableWidth + 0.001,
            );
          }
        }
        const svg = buildWritingSvg(document, { measureText });
        assert.ok(svg.includes(`viewBox="0 0 ${page.width} ${page.height}"`));
        assert.equal(
          (svg.match(/data-field=/g) || []).length,
          page.blocks.reduce((count, block) => count + block.lines.length, 0),
        );
      }
    }
  }
});

test("all four paper stocks adapt to each design while accent stays separate from ink", () => {
  for (const format of ["letter", "note", "card"]) {
    for (const paper of ["cotton", "ruled", "graph", "kraft"]) {
      const svg = buildWritingSvg({
        ...defaults,
        format,
        paper,
        accent: "#315c49",
        ink: "#74434b",
      });
      assert.ok(svg.includes(`data-paper="${paper}"`));
      assert.match(svg, /data-writing-content="true"[^>]*fill="#74434b"/);
      assert.ok(
        svg.includes('stroke="#315c49"') || svg.includes('fill="#315c49"'),
      );
      if (paper === "kraft") assert.ok(svg.includes('fill="#e1c49d"'));
      if (paper === "ruled") assert.ok(svg.includes('stroke="#abc6cf"'));
      if (paper === "graph") assert.ok(svg.includes('stroke="#a6bec2"'));
      assert.equal((svg.match(/id="/g) || []).length, 1);
    }
  }
});

test("the pinned stage fits a whole sheet without stretching or cropping it", () => {
  const page = { width: 780, height: 1100 };
  const wide = fitWritingPage(page, { width: 600, height: 500 });
  assert.deepEqual(wide, { width: 354, height: 500, scale: 500 / 1100 });
  const tall = fitWritingPage(page, { width: 300, height: 900 });
  assert.deepEqual(tall, { width: 300, height: 423, scale: 300 / 780 });
  assert.ok(tall.height <= 900);
  assert.ok(Math.abs(tall.width / tall.height - page.width / page.height) < 0.003);
  // A collapsed column must not render a sliver of paper.
  assert.equal(fitWritingPage(page, { width: 39, height: 700 }), null);
  assert.equal(fitWritingPage(page, { width: 600, height: 39 }), null);
  assert.equal(fitWritingPage({ width: 0, height: 0 }, { width: 600, height: 600 }), null);
});

test("ink and paper contrast is measured so unreadable pairings can be flagged", () => {
  assert.ok(writingContrastRatio("#000000", "cotton") > 20);
  assert.ok(writingContrastRatio("#f4ead8", "kraft") < 3);
  assert.ok(writingContrastRatio("#192e35", "kraft") > 7);
  // Invalid ink falls back to the default rather than returning NaN.
  assert.ok(Number.isFinite(writingContrastRatio("not-a-colour", "ruled")));
  assert.equal(writingPaperTone("kraft"), "#e1c49d");
  assert.equal(writingPaperTone("graph"), "#fffdf4");
});

test("naturalness is bounded, normalizes, and moves lines deterministically", () => {
  assert.equal(defaults.naturalness, 0);
  assert.equal(normalizeWritingDocument({ naturalness: 4 }).naturalness, 1);
  assert.equal(normalizeWritingDocument({ naturalness: -1 }).naturalness, 0);
  assert.equal(
    normalizeWritingDocument({ naturalness: "lots" }).naturalness,
    0,
  );
  // The plain layout never drifts: every text line stays on the baseline.
  const flat = buildWritingSvg({ ...defaults, naturalness: 0 });
  assert.deepEqual(
    flat.match(/rotate\((-?[0-9.]+)\)/g).filter((turn) => turn !== "rotate(0)"),
    [],
  );
  const document = normalizeWritingDocument({
    ...defaults,
    naturalness: 0.8,
  });
  const first = buildWritingSvg(document);
  const second = buildWritingSvg(document);
  assert.equal(first, second, "the same words wobble the same way twice");
  assert.ok(first.includes("rotate("));
  // Different lines drift differently, so it reads as a hand, not a shift.
  assert.notEqual(writingLineSeed("hello", 0), writingLineSeed("hello", 1));
  assert.equal(writingLineSeed("hello", 2), writingLineSeed("hello", 2));
  assert.ok(writingLineSeed("a", 0) >= 0 && writingLineSeed("a", 0) <= 1);
  assert.equal(
    (first.match(/data-field=/g) || []).length,
    (flat.match(/data-field=/g) || []).length,
  );
});

test("font coverage reads a real cmap and refuses what it cannot read", async () => {
  const bytes = await readFile(
    new URL(
      "../research/fixtures/fonts/noto-deva/NotoSansDevanagari.ttf",
      import.meta.url,
    ),
  );
  const coverage = readFontCoverage(bytes);
  assert.ok(coverage instanceof Set);
  assert.ok(coverage.has("क"));
  assert.ok(coverage.has("द"));
  assert.ok(!coverage.has("🦉"));
  // A truncated file is reported as unknown rather than as empty coverage.
  assert.equal(readFontCoverage(new Uint8Array(8)), null);
  assert.equal(readFontCoverage(new Uint8Array(200)), null);
  // WOFF is a compressed container; the guard must not guess.
  const woff = new Uint8Array(12);
  woff.set([0x77, 0x4f, 0x46, 0x46], 0);
  assert.equal(readFontCoverage(woff), null);
});

test("writing guides are an editor option that exports never request", () => {
  const plain = buildWritingSvg(defaults);
  assert.ok(!plain.includes("data-writing-guides"));
  const guided = buildWritingSvg(defaults, { guides: true });
  assert.ok(guided.includes('data-writing-guides="true"'));
  const page = layoutWritingDocument(defaults);
  // Every text baseline gets a guide, plus the two margin rules.
  const baselines = page.blocks.reduce(
    (count, block) => count + block.lines.length,
    0,
  );
  assert.equal((guided.match(/stroke-dasharray/g) || []).length, baselines + 2);
  // Guides never add or remove a word.
  assert.equal(
    (guided.match(/data-field=/g) || []).length,
    (plain.match(/data-field=/g) || []).length,
  );
});

test("Devanagari line wrapping and limits preserve grapheme sequences", () => {
  assert.deepEqual(wrapWritingText("क्षकि", 1, 40), ["क्ष", "कि"]);
  const doc = normalizeWritingDocument({ body: "कि".repeat(801) });
  assert.equal(doc.body, "कि".repeat(800));
  assert.equal(normalizeWritingDocument({ body: "क़ क़" }).body, "क़ क़");
});
