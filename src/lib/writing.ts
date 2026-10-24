import { graphemeStrings } from "../../packages/contracts/src/unicode";
export type WritingFormat = "letter" | "note" | "card";
export type WritingPaper = "cotton" | "ruled" | "graph" | "kraft";
export type WritingAlignment = "left" | "center" | "right";

export type WritingDocument = {
  format: WritingFormat;
  paper: WritingPaper;
  title: string;
  recipient: string;
  body: string;
  signature: string;
  ink: string;
  accent: string;
  fontSize: number;
  lineHeight: number;
  alignment: WritingAlignment;
  weight: number;
  spacing: number;
  slant: number;
  widthScale: number;
  /** 0–1 presentation wobble per line; an export effect, not a font property. */
  naturalness: number;
  projectId?: string;
  /** Owner-authorized generated export reference; renewed on open. */
  generatedExportId?: string;
};

export type WritingTextMeasure = (
  value: string,
  fontSize: number,
  weight: number,
) => number;

export type WritingFontSource = {
  name: string;
  engineeringFixture?: boolean;
  fontDataUrl: string;
  fontFamily: string;
  fontLicense?: string;
  kind: "sample" | "custom" | "generated";
  /** Code points the file can draw; null when the container hides its cmap. */
  coverage?: Set<string> | null;
  measureText?: WritingTextMeasure;
};

export type WritingSvgOptions = {
  fontDataUrl?: string;
  fontFamily?: string;
  fontLicense?: string;
  measureText?: WritingTextMeasure;
  /** Editor-only baseline/margin guides; never passed by exports. */
  guides?: boolean;
};

/**
 * Deterministic 0–1 hash of a line, so the same words wobble the same way in
 * the preview and in every export instead of shuffling on each render.
 */
export function writingLineSeed(value: string, index: number): number {
  let hash = 2166136261;
  const mixed = `${index}\u0000${value}`;
  for (let position = 0; position < mixed.length; position += 1) {
    hash ^= mixed.charCodeAt(position);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 1000) / 1000;
}

export const WRITING_STORAGE_KEY = "penforge-writing-draft-v1";
export const MAX_WRITING_BODY = 800;
export const DEFAULT_WRITING_DOCUMENT: WritingDocument = {
  format: "note",
  paper: "cotton",
  title: "A little note",
  recipient: "Dear you,",
  body: "Some things feel better in your own handwriting.\n\nA thought. A thank-you. A little reminder that someone is thinking of you.",
  signature: "With love,\nMe",
  ink: "#192e35",
  accent: "#d7836f",
  fontSize: 42,
  lineHeight: 1.35,
  alignment: "left",
  weight: 500,
  spacing: 0,
  slant: 0,
  widthScale: 1,
  naturalness: 0,
};

const paperTones: Record<WritingPaper, string> = {
  cotton: "#fffdf7",
  ruled: "#fffdf4",
  graph: "#fffdf4",
  kraft: "#e1c49d",
};

/** The flat stock colour behind the writing; texture and rules are decorative. */
export function writingPaperTone(paper: WritingPaper): string {
  return paperTones[paper];
}

const luminance = (hex: string) => {
  const channels = [1, 3, 5].map((start) => {
    const value = parseInt(hex.slice(start, start + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
};

/**
 * WCAG contrast of ink against its stock. Large handwriting needs 3:1, so the
 * desk can warn before someone exports a note they cannot read.
 */
export function writingContrastRatio(ink: string, paper: WritingPaper): number {
  const tone = /^#[0-9a-f]{6}$/i.test(ink)
    ? ink
    : DEFAULT_WRITING_DOCUMENT.ink;
  const a = luminance(tone);
  const b = luminance(paperTones[paper]);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const clamp = (value: unknown, min: number, max: number, fallback: number) =>
  typeof value === "number" && Number.isFinite(value)
    ? Math.max(min, Math.min(max, value))
    : fallback;
const text = (value: unknown, maximum: number, fallback: string) =>
  typeof value === "string"
    ? graphemeStrings(
        value
          .replace(/\r\n?/g, "\n")
          .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, ""),
      )
        .slice(0, maximum)
        .join("")
    : fallback;

export function normalizeWritingDocument(value: unknown): WritingDocument {
  const source = isObject(value) ? value : {};
  const defaults = DEFAULT_WRITING_DOCUMENT;
  return {
    format: ["letter", "note", "card"].includes(String(source.format))
      ? (source.format as WritingFormat)
      : defaults.format,
    paper: ["cotton", "ruled", "graph", "kraft"].includes(String(source.paper))
      ? (source.paper as WritingPaper)
      : defaults.paper,
    title: text(source.title, 80, defaults.title),
    recipient: text(source.recipient, 100, defaults.recipient),
    body: text(source.body, MAX_WRITING_BODY, defaults.body),
    signature: text(source.signature, 100, defaults.signature),
    ink:
      typeof source.ink === "string" && /^#[\da-f]{6}$/i.test(source.ink)
        ? source.ink.toLowerCase()
        : defaults.ink,
    accent:
      typeof source.accent === "string" && /^#[\da-f]{6}$/i.test(source.accent)
        ? source.accent.toLowerCase()
        : defaults.accent,
    fontSize: clamp(source.fontSize, 24, 72, defaults.fontSize),
    lineHeight: clamp(source.lineHeight, 1, 1.8, defaults.lineHeight),
    alignment: ["left", "center", "right"].includes(String(source.alignment))
      ? (source.alignment as WritingAlignment)
      : defaults.alignment,
    weight: clamp(source.weight, 400, 700, defaults.weight),
    spacing: clamp(source.spacing, -1, 3, defaults.spacing),
    slant: clamp(source.slant, -15, 15, defaults.slant),
    widthScale: clamp(source.widthScale, 0.85, 1.2, defaults.widthScale),
    naturalness: clamp(source.naturalness, 0, 1, defaults.naturalness),
    ...(typeof source.projectId === "string" &&
    /^[\w-]{1,160}$/.test(source.projectId)
      ? { projectId: source.projectId }
      : {}),
    ...(typeof source.generatedExportId === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      source.generatedExportId,
    )
      ? { generatedExportId: source.generatedExportId }
      : {}),
  };
}

/** Invalid or unrelated envelopes never become a writing draft. */
export function readWritingDraft(raw: string | null): WritingDocument | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isObject(parsed) || parsed.version !== 1 || !isObject(parsed.document))
      return null;
    const saved = parsed.document;
    if (
      typeof saved.body !== "string" ||
      typeof saved.title !== "string" ||
      !["letter", "note", "card"].includes(String(saved.format)) ||
      !["cotton", "ruled", "graph", "kraft"].includes(String(saved.paper))
    )
      return null;
    return normalizeWritingDocument(saved);
  } catch {
    return null;
  }
}

export function serializeWritingDraft(document: WritingDocument): string {
  return JSON.stringify({
    version: 1,
    document: normalizeWritingDocument(document),
  });
}

/** Only supported, bounded handoff fields can enter the local composer. */
export function parseWritingQuery(
  query: string | URLSearchParams,
): Partial<WritingDocument> {
  const params = typeof query === "string" ? new URLSearchParams(query) : query;
  const result: Partial<WritingDocument> = {};
  const numbers = {
    weight: [400, 700],
    spacing: [-1, 3],
    slant: [-15, 15],
    fontSize: [24, 72],
    lineHeight: [1, 1.8],
    widthScale: [0.85, 1.2],
    naturalness: [0, 1],
  } as const;
  for (const key of Object.keys(numbers) as (keyof typeof numbers)[]) {
    const raw = params.get(key);
    if (raw === null || raw.trim() === "") continue;
    const number = Number(raw);
    if (Number.isFinite(number))
      result[key] = Math.max(
        numbers[key][0],
        Math.min(numbers[key][1], number),
      );
  }
  const ink = params.get("ink");
  if (ink && /^#[\da-f]{6}$/i.test(ink)) result.ink = ink.toLowerCase();
  const accent = params.get("accent");
  if (accent && /^#[\da-f]{6}$/i.test(accent))
    result.accent = accent.toLowerCase();
  const body = params.get("text");
  if (body !== null) result.body = text(body, MAX_WRITING_BODY, "");
  const title = params.get("title");
  if (title !== null) result.title = text(title, 80, "");
  const recipient = params.get("to") ?? params.get("recipient");
  if (recipient !== null) result.recipient = text(recipient, 100, "");
  const signature = params.get("signature");
  if (signature !== null) result.signature = text(signature, 100, "");
  const format = params.get("format");
  if (format && ["letter", "note", "card"].includes(format))
    result.format = format as WritingFormat;
  const alignment = params.get("alignment");
  if (alignment && ["left", "center", "right"].includes(alignment))
    result.alignment = alignment as WritingAlignment;
  const paper = params.get("paper");
  if (paper && ["cotton", "ruled", "graph", "kraft"].includes(paper))
    result.paper = paper as WritingPaper;
  const project = params.get("project");
  if (project && /^[\w-]{1,160}$/.test(project)) result.projectId = project;
  return result;
}

export function escapeWritingXml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
      })[character]!,
  );
}

/** Conservative handwriting advances keep the pure layout usable without a browser. */
export function estimateWritingWidth(
  value: string,
  fontSize: number,
  spacing = 0,
): number {
  return graphemeStrings(value).reduce((width, character, index) => {
    const advance = /\s/u.test(character)
      ? 0.25
      : /[ilI.,'!:;|]/u.test(character)
        ? 0.23
        : /[mwMW@]/u.test(character)
          ? 0.72
          : /[A-Z]/u.test(character)
            ? 0.58
            : character.codePointAt(0)! > 0x2ff
              ? 1
              : 0.49;
    return width + advance * fontSize + (index ? spacing : 0);
  }, 0);
}

export function wrapWritingText(
  value: string,
  maximumWidth: number,
  fontSize: number,
  spacing = 0,
  measureText?: WritingTextMeasure,
  weight = 500,
): string[] {
  const lines: string[] = [];
  for (const paragraph of value.replace(/\r\n?/g, "\n").split("\n")) {
    let remaining = graphemeStrings(paragraph);
    if (!remaining.length) {
      lines.push("");
      continue;
    }
    while (remaining.length) {
      let count = 0;
      while (
        count < remaining.length &&
        (measureText
          ? measureText(
              remaining.slice(0, count + 1).join(""),
              fontSize,
              weight,
            ) +
            count * spacing
          : estimateWritingWidth(
              remaining.slice(0, count + 1).join(""),
              fontSize,
              spacing,
            )) <= maximumWidth
      )
        count++;
      if (!count) count = 1;
      if (count < remaining.length) {
        const lastSpace = remaining.slice(0, count).lastIndexOf(" ");
        if (lastSpace > 0) count = lastSpace;
      }
      lines.push(remaining.slice(0, count).join("").trimEnd());
      remaining = remaining.slice(count);
      while (remaining[0] === " ") remaining.shift();
    }
  }
  return lines;
}

export type WritingTextBlock = {
  field: "title" | "recipient" | "body" | "signature";
  lines: string[];
  y: number;
  size: number;
  step: number;
  weight: number;
  x: number;
  anchor: "start" | "middle" | "end";
  bounds: { left: number; right: number; top: number; bottom: number };
};

export function layoutWritingDocument(
  input: WritingDocument,
  options: Pick<WritingSvgOptions, "measureText"> = {},
) {
  const document = normalizeWritingDocument(input);
  const designs = {
    letter: {
      width: 840,
      margin: 112,
      minimum: 1120,
      top: 154,
      bottom: 112,
      titleScale: 1.34,
      titleMax: 84,
      titleGap: 26,
      signatureGap: 32,
    },
    note: {
      width: 780,
      margin: 72,
      minimum: 680,
      top: 74,
      bottom: 88,
      titleScale: 1.25,
      titleMax: 76,
      titleGap: 20,
      signatureGap: 18,
    },
    card: {
      width: 1040,
      margin: 142,
      minimum: 740,
      top: 104,
      bottom: 116,
      titleScale: 1.5,
      titleMax: 94,
      titleGap: 24,
      signatureGap: 24,
    },
  } as const;
  const design = designs[document.format];
  const { width, margin } = design;
  const titleSize = Math.min(
    design.titleMax,
    document.fontSize * design.titleScale,
  );
  const titleStep = titleSize * 1.1;
  const lineStep = document.fontSize * document.lineHeight;
  const titleWeight = Math.min(700, document.weight + 50);
  const skew = Math.tan((Math.abs(document.slant) * Math.PI) / 180);
  // Reserve real side bearings and slant room, beyond the font's measured
  // advances. The same safe text area is used for every field and every export.
  const safety = Math.ceil(
    Math.max(titleSize, document.fontSize) * (0.16 + skew * 1.15),
  );
  const contentLeft = margin + safety;
  const contentRight = width - margin - safety;
  const availableWidth = (contentRight - contentLeft) / document.widthScale;
  const wrap = (value: string, size: number, weight = document.weight) =>
    wrapWritingText(
      value,
      availableWidth,
      size,
      document.spacing,
      options.measureText,
      weight,
    );
  const titleLines = document.title
    ? wrap(document.title, titleSize, titleWeight)
    : [];
  const recipientLines = document.recipient
    ? wrap(document.recipient, document.fontSize)
    : [];
  const bodyLines = wrap(document.body, document.fontSize);
  const signatureLines = document.signature
    ? wrap(document.signature, document.fontSize)
    : [];

  let cursor: number = design.top;
  let titleY = cursor + titleSize;
  if (titleLines.length)
    cursor =
      titleY +
      (titleLines.length - 1) * titleStep +
      titleSize * 0.28 +
      design.titleGap;
  let recipientY = cursor + document.fontSize;
  if (recipientLines.length)
    cursor =
      recipientY +
      (recipientLines.length - 1) * lineStep +
      document.fontSize * 0.3 +
      24;
  let bodyY = cursor + document.fontSize;
  cursor = bodyY + (bodyLines.length - 1) * lineStep + document.fontSize * 0.4;
  let signatureY = cursor + design.signatureGap + document.fontSize * 0.85;
  const contentBottom = signatureLines.length
    ? signatureY +
      (signatureLines.length - 1) * lineStep +
      document.fontSize * 0.4
    : cursor;
  const bottomPadding = Math.max(
    design.bottom,
    lineStep + document.fontSize * 0.35,
  );
  const naturalHeight = Math.ceil(contentBottom + bottomPadding);
  const height = Math.max(design.minimum, naturalHeight);
  // A short greeting belongs in the middle of a card. Long writing grows the
  // canvas instead of shrinking text, cropping the message, or losing sign-off.
  const verticalOffset =
    document.format === "card" ? (height - naturalHeight) / 2 : 0;
  titleY += verticalOffset;
  recipientY += verticalOffset;
  bodyY += verticalOffset;
  signatureY += verticalOffset;

  const block = (
    field: WritingTextBlock["field"],
    lines: string[],
    y: number,
    size: number,
    step: number,
    alignment = document.alignment,
    weight = document.weight,
  ): WritingTextBlock => {
    const x =
      alignment === "center"
        ? width / 2
        : alignment === "right"
          ? contentRight
          : contentLeft;
    const anchor =
      alignment === "center"
        ? "middle"
        : alignment === "right"
          ? "end"
          : "start";
    const longest =
      Math.max(
        0,
        ...lines.map((line) =>
          options.measureText
            ? options.measureText(line, size, weight) +
              Math.max(0, graphemeStrings(line).length - 1) * document.spacing
            : estimateWritingWidth(line, size, document.spacing),
        ),
      ) * document.widthScale;
    const overhang = size * (0.12 + skew * 1.1);
    const left =
      anchor === "middle"
        ? x - longest / 2
        : anchor === "end"
          ? x - longest
          : x;
    return {
      field,
      lines,
      y,
      size,
      step,
      weight,
      x,
      anchor,
      bounds: {
        left: left - overhang,
        right: left + longest + overhang,
        top: y - size * 1.08,
        bottom: y + Math.max(0, lines.length - 1) * step + size * 0.4,
      },
    };
  };
  const cardAlignment =
    document.format === "card" ? "center" : document.alignment;
  const blocks = [
    block(
      "title",
      titleLines,
      titleY,
      titleSize,
      titleStep,
      cardAlignment,
      titleWeight,
    ),
    block(
      "recipient",
      recipientLines,
      recipientY,
      document.fontSize,
      lineStep,
      cardAlignment,
    ),
    block("body", bodyLines, bodyY, document.fontSize, lineStep),
    block(
      "signature",
      signatureLines,
      signatureY,
      document.fontSize,
      lineStep,
      cardAlignment,
    ),
  ];
  const personalLine =
    document.signature
      .split("\n")
      .filter((line) => line.trim())
      .at(-1) ||
    document.title ||
    document.recipient;
  const monogram =
    document.format === "letter"
      ? (
          graphemeStrings(personalLine).find((character) =>
            /[\p{L}\p{N}]/u.test(character),
          ) || ""
        ).toUpperCase()
      : "";
  const initialWidth = monogram
    ? (options.measureText?.(monogram, 27, document.weight) ??
      estimateWritingWidth(monogram, 27))
    : 0;
  const monogramSize = initialWidth > 27 ? (27 * 27) / initialWidth : 27;

  return {
    document,
    width,
    height,
    margin,
    titleSize,
    titleStep,
    titleLines,
    recipientLines,
    bodyLines,
    signatureLines,
    lineStep,
    titleY,
    recipientY,
    bodyY,
    signatureY,
    contentLeft,
    contentRight,
    availableWidth,
    blocks,
    monogram,
    monogramSize,
  };
}

/**
 * The pinned preview measures the paper against a fixed stage. Keep the sheet's
 * aspect ratio, never crop it, and leave tiny stages unfitted so a collapsed or
 * hidden column cannot render a sliver of paper.
 */
export function fitWritingPage(
  page: { width: number; height: number },
  stage: { width: number; height: number },
): { width: number; height: number; scale: number } | null {
  if (!(page.width > 0) || !(page.height > 0)) return null;
  if (!(stage.width >= 40) || !(stage.height >= 40)) return null;
  const scale = Math.min(stage.width / page.width, stage.height / page.height);
  return {
    width: Math.floor(page.width * scale),
    height: Math.floor(page.height * scale),
    scale,
  };
}

/** Pure SVG renderer. Export helpers supply an embedded font and its license. */
export function buildWritingSvg(
  input: WritingDocument,
  options: WritingSvgOptions = {},
): string {
  const page = layoutWritingDocument(input, options);
  const { document, width, height, margin } = page;
  const family =
    options.fontFamily && /^[\w-]{1,80}$/.test(options.fontFamily)
      ? options.fontFamily
      : "PenforgeWriting";
  const embedded =
    options.fontDataUrl &&
    /^data:font\/(?:woff2?|ttf|otf);base64,[\w+/=]+$/.test(options.fontDataUrl)
      ? options.fontDataUrl
      : undefined;
  const background =
    document.paper === "kraft"
      ? "#e1c49d"
      : document.paper === "ruled"
        ? "#fffdf4"
        : "#fffdf7";
  const n = (value: number) => Number(value.toFixed(3));
  const font = `'${embedded ? family : "Caveat Variable"}',cursive`;
  const naturalness = document.naturalness;
  const content = page.blocks
    .map((block) =>
      block.lines
        .map((line, index) => {
          // A tiny per-line drift sells handwriting without moving the layout.
          const seed = writingLineSeed(line, index + block.field.length);
          const drift = naturalness > 0 ? (seed - 0.5) * 2 * naturalness : 0;
          const rise = drift * block.size * 0.05;
          const turn = drift * 1.6;
          return `<text data-field="${block.field}" xml:space="preserve" transform="translate(${n(block.x)} ${n(block.y + index * block.step + rise)}) rotate(${n(turn)}) skewX(${-document.slant}) scale(${document.widthScale} 1)" text-anchor="${block.anchor}" font-size="${n(block.size)}" font-weight="${block.weight}">${escapeWritingXml(line)}</text>`;
        })
        .join(""),
    )
    .join("");
  const inset = document.format === "card" ? 38 : 0;
  const stockTop = document.format === "card" ? 34 : 0;
  const stockRight = width - inset;
  const stockBottom = height - stockTop;
  const stockWidth = width - inset * 2;
  const stockHeight = height - stockTop * 2;
  let rules = "";
  if (document.paper === "ruled") {
    const first = page.bodyY + document.fontSize * 0.25;
    const count = Math.max(
      0,
      Math.floor((stockBottom - 24 - first) / page.lineStep) + 1,
    );
    rules = `<path d="M${inset + 56} ${stockTop + 24}V${stockBottom - 24}" stroke="#dc9c91" stroke-opacity=".4"/><g stroke="#abc6cf" stroke-opacity=".42">${Array.from({ length: count }, (_, index) => `<path d="M${inset + 24} ${n(first + index * page.lineStep)}H${stockRight - 24}"/>`).join("")}</g>`;
  } else if (document.paper === "graph") {
    rules = `<g stroke="#a6bec2" stroke-opacity=".32" stroke-width=".7">${Array.from({ length: Math.floor(stockWidth / 28) + 1 }, (_, index) => `<path d="M${inset + index * 28} ${stockTop}V${stockBottom}"/>`).join("")}${Array.from({ length: Math.floor(stockHeight / 28) + 1 }, (_, index) => `<path d="M${inset} ${stockTop + index * 28}H${stockRight}"/>`).join("")}</g>`;
  }

  let decoration = "";
  if (document.format === "letter") {
    decoration = `<g data-motif="personal-stationery"><g fill="none" stroke="${document.accent}" stroke-width="1.4"><path d="M${margin} 78H${width / 2 - 45}m90 0H${width - margin}"/><circle cx="${width / 2}" cy="78" r="24"/><path d="M${margin} ${height - 64}H${width - margin}" stroke-opacity=".5"/></g>${page.monogram ? `<text data-decoration="monogram" x="${width / 2}" y="86" text-anchor="middle" font-family="${font}" font-size="${n(page.monogramSize)}" font-weight="${document.weight}" fill="${document.ink}">${escapeWritingXml(page.monogram)}</text>` : ""}</g>`;
  } else if (document.format === "note") {
    const edge = Array.from(
      { length: Math.floor((width - 24) / 24) + 1 },
      (_, index) => `${12 + index * 24},${height - (index % 2 ? 13 : 17)}`,
    ).join(" ");
    decoration = `<g data-motif="memo-band"><rect width="${width}" height="25" fill="${document.accent}"/><path d="M${width - 58} 0V58H${width}" fill="${background}" stroke="${document.accent}" stroke-width="1.2"/><path d="m${width - 58} 0 58 58V0Z" fill="${document.accent}" fill-opacity=".14"/><polyline points="${edge}" fill="none" stroke="${document.accent}" stroke-opacity=".35" stroke-width="1"/></g>`;
  } else {
    const ornament = `<path d="M0 34C0 15 15 0 34 0M0 22C13 22 22 13 22 0"/><circle cx="0" cy="34" r="1.8" fill="${document.accent}" stroke="none"/>`;
    const titleRule = page.titleLines.length
      ? `<path d="M${width / 2 - 36} ${n(page.titleY + (page.titleLines.length - 1) * page.titleStep + page.titleSize * 0.36)}h72" stroke-width="2"/>`
      : "";
    decoration = `<g data-motif="greeting-frame" fill="none" stroke="${document.accent}" stroke-width="1.5"><rect x="62" y="58" width="${width - 124}" height="${height - 116}" rx="1" stroke-opacity=".5"/><path d="M57 52V${height - 52}" stroke-opacity=".18"/><g transform="translate(84 80)">${ornament}</g><g transform="translate(${width - 84} ${height - 80}) rotate(180)">${ornament}</g>${titleRule}</g>`;
  }

  let guides = "";
  if (options.guides) {
    const guideStroke = `stroke="#557e95" stroke-opacity=".35" stroke-dasharray="6 5"`;
    const marginLines = `<path d="M${page.contentLeft} ${margin / 2}V${height - margin / 2}" ${guideStroke}/><path d="M${page.contentRight} ${margin / 2}V${height - margin / 2}" ${guideStroke}/>`;
    const baselines = page.blocks
      .filter((block) => block.lines.length)
      .map((block) =>
        block.lines
          .map(
            (_, index) =>
              `<path d="M${page.contentLeft} ${n(block.y + index * block.step)}H${page.contentRight}" ${guideStroke}/>`,
          )
          .join(""),
      )
      .join("");
    guides = `<g data-writing-guides="true" fill="none" stroke-width="1">${marginLines}${baselines}</g>`;
  }

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" data-writing-design="${document.format}" role="img" aria-label="${escapeWritingXml(document.title || "Personal writing")}"><title>${escapeWritingXml(document.title || "Personal writing")}</title><metadata>${escapeWritingXml(options.fontLicense || "Penforge writing document. Preview may use the Caveat sample typeface.")}</metadata><defs><style>${embedded ? `@font-face{font-family:'${family}';src:url('${embedded}');font-weight:400 700;font-style:normal;}` : ""}</style><pattern id="writing-fibers" width="90" height="90" patternUnits="userSpaceOnUse"><path d="m9 12 2 1m34 39 1-2m34 18-2 1M24 79l1-1M71 8l1 2" stroke="#806d4a" stroke-width=".65" opacity=".13"/></pattern></defs><rect width="${width}" height="${height}" fill="${document.format === "card" ? document.accent : background}"/><g data-paper="${document.paper}"><rect x="${inset}" y="${stockTop}" width="${stockWidth}" height="${stockHeight}" rx="${document.format === "card" ? 2 : 0}" fill="${background}"/><rect x="${inset + 1}" y="${stockTop + 1}" width="${stockWidth - 2}" height="${stockHeight - 2}" fill="url(#writing-fibers)" stroke="#b7a58d" stroke-opacity=".3"/>${rules}</g>${decoration}${guides}<g data-writing-content="true" font-family="${font}" fill="${document.ink}" letter-spacing="${document.spacing}">${content}</g></svg>`;
}

function createWritingFontMetrics(family: string): WritingTextMeasure {
  const context = document.createElement("canvas").getContext("2d");
  if (!context)
    throw new Error("This browser cannot measure this typeface for export.");
  return (value, fontSize, weight) => {
    context.font = `${weight} ${fontSize}px "${family}"`;
    return context.measureText(value).width;
  };
}

let sampleFont: Promise<WritingFontSource> | undefined;
export function loadWritingFont(): Promise<WritingFontSource> {
  if (!sampleFont)
    sampleFont = Promise.all([
      fetch("/fonts/CaveatVariable.woff2").then((response) => {
        if (!response.ok)
          throw new Error(
            "The sample typeface could not be loaded. Please try again.",
          );
        return response.blob();
      }),
      fetch("/fonts/OFL-Caveat.txt").then((response) => {
        if (!response.ok)
          throw new Error(
            "The typeface license could not be loaded. Please try again.",
          );
        return response.text();
      }),
    ])
      .then(async ([blob, license]) => {
        const fontDataUrl = await writingBlobDataUrl(
          new Blob([blob], { type: "font/woff2" }),
        );
        const family = "PenforgeWriting";
        const face = new FontFace(family, `url("${fontDataUrl}")`, {
          weight: "400 700",
        });
        await face.load();
        document.fonts.add(face);
        return {
          name: "Caveat sample",
          fontDataUrl,
          fontFamily: family,
          fontLicense: license,
          kind: "sample" as const,
          measureText: createWritingFontMetrics(family),
        };
      })
      .catch((cause) => {
        sampleFont = undefined;
        throw cause;
      });
  return sampleFont;
}

export function writingBlobDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      typeof reader.result === "string"
        ? resolve(reader.result)
        : reject(new Error("This file could not be read."));
    reader.onerror = () => reject(new Error("This file could not be read."));
    reader.readAsDataURL(blob);
  });
}

/**
 * Which characters a font file actually draws, read from its cmap table.
 * Returns null for compressed containers (WOFF/WOFF2) and malformed files, so
 * callers warn only when the answer is known rather than guessing.
 */
export function readFontCoverage(
  source: ArrayBuffer | Uint8Array,
): Set<string> | null {
  const bytes = source instanceof Uint8Array ? source : new Uint8Array(source);
  if (bytes.byteLength < 12) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const version = view.getUint32(0);
  // 0x00010000 (TrueType), 'OTTO' (CFF) and 'true' carry an uncompressed cmap.
  if (version !== 0x00010000 && version !== 0x4f54544f && version !== 0x74727565)
    return null;
  const tables = view.getUint16(4);
  let cmap = -1;
  for (let index = 0; index < tables; index += 1) {
    const record = 12 + index * 16;
    if (record + 16 > bytes.byteLength) return null;
    if (view.getUint32(record) === 0x636d6170) {
      cmap = view.getUint32(record + 8);
      break;
    }
  }
  if (cmap < 0 || cmap + 4 > bytes.byteLength) return null;
  const characters = new Set<string>();
  const add = (codePoint: number) => {
    if (codePoint <= 0 || codePoint > 0x10ffff) return;
    if (codePoint >= 0xd800 && codePoint <= 0xdfff) return;
    characters.add(String.fromCodePoint(codePoint));
  };
  let parsed = false;
  const subtables = view.getUint16(cmap + 2);
  for (let index = 0; index < subtables; index += 1) {
    const record = cmap + 4 + index * 8;
    if (record + 8 > bytes.byteLength) break;
    const offset = cmap + view.getUint32(record + 4);
    if (offset + 4 > bytes.byteLength) continue;
    const format = view.getUint16(offset);
    if (format === 12 && offset + 16 <= bytes.byteLength) {
      const groups = view.getUint32(offset + 12);
      for (let group = 0; group < groups; group += 1) {
        const base = offset + 16 + group * 12;
        if (base + 12 > bytes.byteLength) break;
        const start = view.getUint32(base);
        const end = view.getUint32(base + 4);
        const glyph = view.getUint32(base + 8);
        // A range that starts at glyph 0 is a hole, not coverage.
        if (glyph === 0 || end < start) continue;
        for (let point = start; point <= end; point += 1) add(point);
      }
      parsed = true;
    } else if (format === 4 && offset + 14 <= bytes.byteLength) {
      const segments = view.getUint16(offset + 6) / 2;
      const ends = offset + 14;
      const starts = ends + segments * 2 + 2;
      const deltas = starts + segments * 2;
      const rangeOffsets = deltas + segments * 2;
      if (rangeOffsets + segments * 2 > bytes.byteLength) continue;
      for (let segment = 0; segment < segments; segment += 1) {
        const end = view.getUint16(ends + segment * 2);
        const start = view.getUint16(starts + segment * 2);
        if (end === 0xffff || end < start) continue;
        const delta = view.getInt16(deltas + segment * 2);
        const rangeOffset = view.getUint16(rangeOffsets + segment * 2);
        for (let point = start; point <= end; point += 1) {
          let glyph: number;
          if (rangeOffset === 0) {
            glyph = (point + delta) & 0xffff;
          } else {
            const address =
              rangeOffsets +
              segment * 2 +
              rangeOffset +
              (point - start) * 2;
            if (address + 2 > bytes.byteLength) continue;
            glyph = view.getUint16(address);
            if (glyph !== 0) glyph = (glyph + delta) & 0xffff;
          }
          if (glyph !== 0) add(point);
        }
      }
      parsed = true;
    }
  }
  return parsed ? characters : null;
}

export async function loadCustomWritingFont(
  file: File,
): Promise<WritingFontSource> {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (!extension || !["ttf", "otf", "woff", "woff2"].includes(extension))
    throw new Error("Choose a TTF, OTF, WOFF, or WOFF2 font file.");
  if (!file.size || file.size > 3 * 1024 * 1024)
    throw new Error("Choose a font smaller than 3 MB.");
  const buffer = await file.arrayBuffer();
  if (buffer.byteLength < 4)
    throw new Error("That file does not contain a supported font.");
  const signature = new DataView(buffer).getUint32(0);
  if (
    ![0x00010000, 0x4f54544f, 0x774f4646, 0x774f4632, 0x74727565].includes(
      signature,
    )
  )
    throw new Error("That file does not contain a supported font.");
  const family = `PenforgeLocal${Date.now()}`;
  const face = new FontFace(family, buffer, { weight: "400 700" });
  try {
    await face.load();
  } catch {
    throw new Error("This font could not be opened. Try another font file.");
  }
  document.fonts.add(face);
  return {
    name: file.name,
    fontDataUrl: await writingBlobDataUrl(
      new Blob([buffer], { type: `font/${extension}` }),
    ),
    fontFamily: family,
    fontLicense: `Font supplied locally by the writer: ${file.name}.`,
    kind: "custom",
    coverage: readFontCoverage(buffer),
    measureText: createWritingFontMetrics(family),
  };
}

/** Bytes were owner-authorized and hash-verified by the private export adapter. */
export async function loadGeneratedWritingFont(
  blob: Blob,
  metadata: {
    id: string;
    format: string;
    familyName: string;
    license: string;
    fsType: number;
    engineeringFixture?: boolean;
  },
): Promise<WritingFontSource> {
  if (metadata.fsType === 2)
    throw new Error(
      "This font restricts embedding. Download it for permitted desktop use; choose an embeddable build for stationery export.",
    );
  const loaded = await loadCustomWritingFont(
    new File([blob], `penforge-${metadata.id}.${metadata.format}`, {
      type: blob.type,
    }),
  );
  return {
    ...loaded,
    name: `${metadata.engineeringFixture ? "Engineering fixture" : "Generated Penforge"} · ${metadata.familyName}`,
    engineeringFixture: metadata.engineeringFixture,
    kind: "generated",
    fontLicense: metadata.license,
  };
}

export async function exportWritingSvg(
  document: WritingDocument,
  font?: WritingFontSource,
): Promise<Blob> {
  return new Blob(
    [buildWritingSvg(document, font || (await loadWritingFont()))],
    { type: "image/svg+xml;charset=utf-8" },
  );
}

export async function exportWritingPng(
  document: WritingDocument,
  font?: WritingFontSource,
): Promise<Blob> {
  // Reject pathological blank-line pages before loading any browser resources.
  const preliminary = layoutWritingDocument(document);
  if (preliminary.height > 12000)
    throw new Error(
      "This page is too tall for PNG. Use SVG, or remove some blank lines.",
    );
  const loadedFont = font || (await loadWritingFont());
  const page = layoutWritingDocument(document, loadedFont);
  if (page.height > 12000)
    throw new Error(
      "This page is too tall for PNG. Use SVG, or remove some blank lines.",
    );
  const svg = await exportWritingSvg(document, loadedFont);
  const url = URL.createObjectURL(svg);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const scale = page.height > 3000 ? 1 : 2;
    const canvas = window.document.createElement("canvas");
    canvas.width = page.width * scale;
    canvas.height = page.height * scale;
    const context = canvas.getContext("2d");
    if (!context)
      throw new Error(
        "PNG export is unavailable in this browser. Use SVG instead.",
      );
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (blob) =>
          blob
            ? resolve(blob)
            : reject(new Error("PNG export did not finish. Try SVG instead.")),
        "image/png",
      ),
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function downloadWritingBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download =
    filename.replace(/[^\w. -]/g, "").slice(0, 100) || "penforge-note";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
