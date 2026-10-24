/** Pure helpers behind the Writing Desk chrome: palettes, find, versions, fit. */
import { graphemeStrings } from "../../packages/contracts/src/unicode";
import {
  normalizeWritingDocument,
  type WritingDocument,
  type WritingFormat,
} from "./writing";

export type WritingPalette = {
  id: string;
  name: string;
  accent: string;
  ink: string;
};

/** Paired ink and accent choices; selecting one changes both at once. */
export const WRITING_PALETTES: WritingPalette[] = [
  { id: "classic", name: "Classic", accent: "#d7836f", ink: "#192e35" },
  { id: "woodland", name: "Woodland", accent: "#507662", ink: "#315c49" },
  { id: "berry", name: "Berry", accent: "#cf929c", ink: "#74434b" },
];

export type WritingTextField = "title" | "recipient" | "body" | "signature";

export const WRITING_TEXT_FIELDS: WritingTextField[] = [
  "title",
  "recipient",
  "body",
  "signature",
];

const escapeRegExp = (value: string) =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function searchPattern(query: string, matchCase: boolean) {
  return new RegExp(escapeRegExp(query), matchCase ? "g" : "gi");
}

/** Non-overlapping occurrences across every authored word field. */
export function writingMatchCount(
  document: WritingDocument,
  query: string,
  matchCase = false,
): number {
  if (!query) return 0;
  const pattern = searchPattern(query, matchCase);
  return WRITING_TEXT_FIELDS.reduce(
    (total, field) => total + (document[field].match(pattern)?.length ?? 0),
    0,
  );
}

/**
 * Replace every occurrence in every word field. Returns null when there is
 * nothing to search or replace, so callers do not record an empty history step.
 */
export function replaceWritingMatches(
  document: WritingDocument,
  query: string,
  replacement: string,
  matchCase = false,
): WritingDocument | null {
  if (!query) return null;
  const pattern = searchPattern(query, matchCase);
  const replace = () => replacement;
  const next = normalizeWritingDocument({
    ...document,
    title: document.title.replace(pattern, replace),
    recipient: document.recipient.replace(pattern, replace),
    body: document.body.replace(pattern, replace),
    signature: document.signature.replace(pattern, replace),
  });
  return JSON.stringify(next) === JSON.stringify(document) ? null : next;
}

/** Words in the note and its sign-off; the greeting and title are not counted. */
export function writingWordCount(document: WritingDocument): number {
  return [document.body, document.signature].reduce(
    (total, text) =>
      total + (text.trim() ? text.trim().split(/\s+/).length : 0),
    0,
  );
}

/** The fitted sheet's real size as a percentage of the laid-out page. */
export function writingPreviewPercent(
  fit: { height: number } | null,
  pageHeight: number,
  zoom: number,
): number {
  if (!fit || pageHeight <= 0) return Math.round(zoom * 100);
  return Math.round((fit.height / pageHeight) * zoom * 100);
}

export const WRITING_VERSIONS_KEY = "penforge-writing-versions-v1";
export const MAX_WRITING_VERSIONS = 12;

export type WritingVersion = {
  id: string;
  at: number;
  title: string;
  format: WritingFormat;
  document: WritingDocument;
};

const formats = new Set<WritingFormat>(["letter", "note", "card"]);

/** Tolerate drafts written by older builds and drop anything malformed. */
export function readWritingVersions(raw: string | null): WritingVersion[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .flatMap((value): WritingVersion[] => {
        if (typeof value !== "object" || value === null) return [];
        const record = value as Record<string, unknown>;
        if (
          typeof record.id !== "string" ||
          typeof record.at !== "number" ||
          !Number.isFinite(record.at) ||
          typeof record.document !== "object" ||
          record.document === null
        )
          return [];
        const document = normalizeWritingDocument(record.document);
        return [
          {
            id: record.id,
            at: record.at,
            title:
              typeof record.title === "string" && record.title.trim()
                ? record.title.slice(0, 120)
                : document.title,
            format:
              typeof record.format === "string" &&
              formats.has(record.format as WritingFormat)
                ? (record.format as WritingFormat)
                : document.format,
            document,
          },
        ];
      })
      .sort((a, b) => b.at - a.at)
      .slice(0, MAX_WRITING_VERSIONS);
  } catch {
    return [];
  }
}

/** Newest first, deduplicated by content, and capped for local storage. */
export function appendWritingVersion(
  versions: WritingVersion[],
  version: WritingVersion,
): WritingVersion[] {
  const content = JSON.stringify(version.document);
  const withoutDuplicate = versions.filter(
    (current) => JSON.stringify(current.document) !== content,
  );
  return [version, ...withoutDuplicate].slice(0, MAX_WRITING_VERSIONS);
}

export type WritingTemplateId =
  | "blank"
  | "thankyou"
  | "apology"
  | "birthday"
  | "love"
  | "reminder";

export type WritingTemplate = {
  id: WritingTemplateId;
  name: string;
  title: string;
  recipient: string;
  body: string;
  signature: string;
};

/** Occasion skeletons; the writer replaces the bracketed prompts with words. */
export const WRITING_TEMPLATES: WritingTemplate[] = [
  {
    id: "blank",
    name: "Blank",
    title: "",
    recipient: "",
    body: "",
    signature: "",
  },
  {
    id: "thankyou",
    name: "Thank you",
    title: "Thank you",
    recipient: "Dear [name],",
    body: "Thank you for [what they did].\n\nIt meant more than you know, and I wanted you to hear it from me.",
    signature: "With thanks,\n[your name]",
  },
  {
    id: "apology",
    name: "I'm sorry",
    title: "I'm sorry",
    recipient: "Dear [name],",
    body: "I've been thinking about [what happened], and I'm sorry.\n\nYou were right to be upset, and I'll do better.",
    signature: "Truly,\n[your name]",
  },
  {
    id: "birthday",
    name: "Happy birthday",
    title: "Happy birthday",
    recipient: "Dear [name],",
    body: "Happy birthday! I hope this year brings you [something they want].\n\nLet's [celebrate soon] — this note is the warm-up.",
    signature: "With love,\n[your name]",
  },
  {
    id: "love",
    name: "Love note",
    title: "For you",
    recipient: "My love,",
    body: "[One specific thing you love about them.]\n\n[Another. Take your time.])",
    signature: "Yours,\n[your name]",
  },
  {
    id: "reminder",
    name: "Little reminder",
    title: "A little reminder",
    recipient: "Hey you,",
    body: "A little reminder that [what they forget about themselves].\n\nKeep it. It's true.",
    signature: "Thinking of you,\n[your name]",
  },
];

/** A template fills the words only; paper, ink and tuning stay as they were. */
export function applyWritingTemplate(
  document: WritingDocument,
  templateId: WritingTemplateId,
): WritingDocument | null {
  const template = WRITING_TEMPLATES.find((item) => item.id === templateId);
  if (!template) return null;
  return normalizeWritingDocument({
    ...document,
    title: template.title,
    recipient: template.recipient,
    body: template.body,
    signature: template.signature,
  });
}

export const WRITING_SNIPPETS_KEY = "penforge-writing-snippets-v1";
export const MAX_WRITING_SNIPPETS = 12;

export type WritingSnippet = {
  id: string;
  text: string;
  savedAt: number;
};

/** Saved sign-offs are plain text; anything malformed is dropped. */
export function readWritingSnippets(raw: string | null): WritingSnippet[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const seen = new Set<string>();
    return parsed
      .flatMap((value): WritingSnippet[] => {
        if (typeof value !== "object" || value === null) return [];
        const record = value as Record<string, unknown>;
        if (typeof record.text !== "string") return [];
        const text = record.text.trim();
        if (!text || text.length > 200 || seen.has(text)) return [];
        seen.add(text);
        return [
          {
            id:
              typeof record.id === "string" && record.id
                ? record.id
                : crypto.randomUUID(),
            text,
            savedAt:
              typeof record.savedAt === "number" &&
              Number.isFinite(record.savedAt)
                ? record.savedAt
                : 0,
          },
        ];
      })
      .sort((a, b) => b.savedAt - a.savedAt)
      .slice(0, MAX_WRITING_SNIPPETS);
  } catch {
    return [];
  }
}

/** Newest first, deduplicated by text, and capped for local storage. */
export function appendWritingSnippet(
  snippets: WritingSnippet[],
  text: string,
): WritingSnippet[] {
  const trimmed = text.trim().slice(0, 200);
  if (!trimmed) return snippets;
  return [
    { id: crypto.randomUUID(), text: trimmed, savedAt: Date.now() },
    ...snippets.filter((snippet) => snippet.text !== trimmed),
  ].slice(0, MAX_WRITING_SNIPPETS);
}

export function removeWritingSnippet(
  snippets: WritingSnippet[],
  id: string,
): WritingSnippet[] {
  return snippets.filter((snippet) => snippet.id !== id);
}

/** Unique written characters, ignoring whitespace and control marks. */
export function writingUsedCharacters(document: WritingDocument): string[] {
  const used = new Set<string>();
  for (const field of WRITING_TEXT_FIELDS) {
    for (const character of graphemeStrings(document[field])) {
      if (/^[\s\p{P}\p{S}]$/u.test(character)) continue;
      used.add(character);
    }
  }
  return [...used];
}

/**
 * Characters the writer typed that the active font cannot draw. Coverage is
 * intersected with what is actually on the page, so a font missing digits does
 * not warn on a note that contains none.
 */
export function writingMissingCharacters(
  document: WritingDocument,
  covered: ReadonlySet<string> | null,
): string[] {
  if (!covered) return [];
  return writingUsedCharacters(document).filter(
    (character) => !covered.has(character),
  );
}

/** Cover the characters a font reports, per code point and grapheme. */
export function writingCoverageSet(characters: Iterable<string>): Set<string> {
  const covered = new Set<string>();
  for (const entry of characters) {
    for (const character of graphemeStrings(entry)) covered.add(character);
  }
  return covered;
}

/** Short, honest relative time for save chips and library rows. */
export function writingWhenLabel(at: number, now = Date.now()): string {
  if (!Number.isFinite(at) || at <= 0) return "";
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export type WritingPageSort = "recent" | "oldest" | "title";

export type WritingPageSummary = {
  id: string;
  title: string;
  format: WritingFormat;
  updatedAt: string;
  body: string;
};

/** Search text matches title and body; filters and sort stay predictable. */
export function filterWritingPages<T extends WritingPageSummary>(
  pages: T[],
  options: {
    query?: string;
    format?: WritingFormat | "all";
    sort?: WritingPageSort;
  } = {},
): T[] {
  const query = (options.query ?? "").trim().toLocaleLowerCase();
  const format = options.format ?? "all";
  const sort = options.sort ?? "recent";
  const filtered = pages.filter((page) => {
    if (format !== "all" && page.format !== format) return false;
    if (!query) return true;
    return (
      page.title.toLocaleLowerCase().includes(query) ||
      page.body.toLocaleLowerCase().includes(query)
    );
  });
  return filtered.sort((a, b) => {
    if (sort === "title")
      return a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
    const left = Date.parse(a.updatedAt) || 0;
    const right = Date.parse(b.updatedAt) || 0;
    return sort === "oldest" ? left - right : right - left;
  });
}
