"use client";

import { createOwnerTokenProvider } from "@/lib/owner-token";

import Link from "next/link";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import PaperThumbnail from "@/components/shared/PaperThumbnail";
import { useMotionPreference } from "@/components/shared/MotionPreference";
import type { FontProject } from "@/lib/contracts";
import { projectService } from "@/lib/mock-services";
import {
  DEFAULT_WRITING_DOCUMENT,
  SAMPLE_FONT_NAME,
  SAMPLE_FONT_CSS,
  MAX_WRITING_BODY,
  WRITING_STORAGE_KEY,
  buildWritingSvg,
  downloadWritingBlob,
  exportWritingPng,
  exportWritingSvg,
  fitWritingPage,
  layoutWritingDocument,
  writingContrastRatio,
  loadCustomWritingFont,
  loadGeneratedWritingFont,
  loadWritingFont,
  normalizeWritingDocument,
  updateWritingDocument,
  parseWritingQuery,
  readWritingDraft,
  serializeWritingDraft,
  type WritingDocument,
  type WritingFontSource,
  type WritingFormat,
  type WritingPaper,
} from "@/lib/writing";
import styles from "./WritingDesk.module.css";
import WritingSelectionControls from "./WritingSelectionControls";
import { useWritingSelection } from "./useWritingSelection";
import { styleWritingSelection, type WritingStyle } from "@/lib/writing-text";
import {
  getAccountIdentityService,
  getPenforgeAuthConfiguration,
} from "@/lib/auth-service";
import { createExportClient, exportErrorText } from "@/lib/export-client";
import {
  createDocumentsClient,
  documentFromWire,
  DocumentError,
  DocumentSaver,
  type DocumentsClient,
  type DocumentSaveState,
  type WritingDocumentWire,
} from "@/lib/account-documents";
import { useAccountSession } from "@/lib/use-account-session";
import {
  clearGeneratedWritingReference,
  requestedWritingExport,
  writingFontReferenceTransition,
  WritingFontHandoff,
} from "@/lib/writing-font-handoff";
import { createAccountProjectService } from "@/lib/account-project-service";
import {
  MAX_WRITING_VERSIONS,
  WRITING_PALETTES,
  WRITING_SNIPPETS_KEY,
  WRITING_TEMPLATES,
  WRITING_VERSIONS_KEY,
  appendWritingSnippet,
  appendWritingVersion,
  applyWritingTemplate,
  filterWritingPages,
  readWritingSnippets,
  readWritingVersions,
  removeWritingSnippet,
  replaceWritingMatches,
  writingMatchCount,
  writingMissingCharacters,
  writingPreviewPercent,
  writingWhenLabel,
  writingWordCount,
  type WritingPageSort,
  type WritingSnippet,
  type WritingTemplateId,
  type WritingVersion,
} from "@/lib/writing-desk";

const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

const formats: WritingFormat[] = ["letter", "note", "card"];
const papers: { id: WritingPaper; name: string }[] = [
  { id: "cotton", name: "Cotton" },
  { id: "ruled", name: "Ruled" },
  { id: "graph", name: "Graph" },
  { id: "kraft", name: "Kraft" },
];
const inks = [
  { value: "#192e35", name: "Navy" },
  { value: "#74434b", name: "Berry" },
  { value: "#315c49", name: "Forest" },
  { value: "#594c87", name: "Violet" },
];
const accents = [
  { value: "#d7836f", name: "Warm rose", label: "Rose" },
  { value: "#507662", name: "Forest", label: "Forest" },
  { value: "#557e95", name: "Blue", label: "Blue" },
];

type PrintSize = "A4" | "A5" | "Letter" | "DL";

const printSizes: { id: PrintSize; name: string; note: string }[] = [
  { id: "A4", name: "A4", note: "210 × 297 mm" },
  { id: "A5", name: "A5", note: "148 × 210 mm" },
  { id: "Letter", name: "Letter", note: "8.5 × 11 in" },
  { id: "DL", name: "DL", note: "110 × 220 mm" },
];
const fieldLabels = {
  letter: {
    title: "Letter heading",
    recipient: "Greeting",
    body: "Your letter",
    signature: "Sign off",
  },
  note: {
    title: "Note title",
    recipient: "To",
    body: "Your note",
    signature: "Signed",
  },
  card: {
    title: "Front message",
    recipient: "To",
    body: "Your message",
    signature: "From",
  },
};

function savedWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const iconPaths = {
  download: <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />,
  share: <path d="M12 15V3m-5 5 5-5 5 5M5 11v10h14V11" />,
  print: (
    <>
      <path d="M7 8V3h10v5M7 17H4V9h16v8h-3M7 14h10v7H7z" />
      <path d="M17 11h.01" />
    </>
  ),
  upload: <path d="M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5" />,
  trash: (
    <>
      <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
      <path d="M10 11v5M14 11v5" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  eye: (
    <>
      <path d="M2.5 12S6 6.2 12 6.2 21.5 12 21.5 12 18 17.8 12 17.8 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="2.6" />
    </>
  ),
  arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  undo: (
    <>
      <path d="M8 7 3.5 12 8 17" />
      <path d="M3.5 12H13a5.5 5.5 0 0 1 5.5 5.5V19" />
    </>
  ),
  redo: (
    <>
      <path d="m16 7 4.5 5-4.5 5" />
      <path d="M20.5 12H11a5.5 5.5 0 0 0-5.5 5.5V19" />
    </>
  ),
  fit: <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />,
  more: (
    <>
      <circle cx="5" cy="12" r="1.2" fill="currentColor" />
      <circle cx="12" cy="12" r="1.2" fill="currentColor" />
      <circle cx="19" cy="12" r="1.2" fill="currentColor" />
    </>
  ),
  copy: (
    <>
      <rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2" />
      <path d="M5 15V5.5A1.5 1.5 0 0 1 6.5 4H15" />
    </>
  ),
  duplicate: (
    <>
      <path d="M7 3h7l4 4v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" />
      <path d="M14 3v5h5M12 11v6M9 14h6" />
    </>
  ),
  folder: (
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.4-4.4" />
    </>
  ),
  history: (
    <>
      <path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.6" />
      <path d="M4 4v4.6h4.6M12 8v4.4l3 1.8" />
    </>
  ),
  check: <path d="m4.5 12.5 5 5L20 6" />,
  alert: (
    <>
      <path d="M12 4 3 20h18Z" />
      <path d="M12 10v4M12 17h.01" />
    </>
  ),
  alignLeft: <path d="M4 6h16M4 12h10M4 18h13" />,
  alignCenter: <path d="M4 6h16M7 12h10M5 18h14" />,
  alignRight: <path d="M4 6h16M10 12h10M7 18h13" />,
  chevron: <path d="m6 9 6 6 6-6" />,
  expand: <path d="M9 4H4v5M15 4h5v5M15 20h5v-5M9 20H4v-5" />,
  collapse: <path d="M4 9h5V4M20 9h-5V4M20 15h-5v5M4 15h5v5" />,
  template: (
    <>
      <path d="M5 4h14v16H5z" />
      <path d="M5 9h14M9 9v11" />
    </>
  ),
};

type IconName = keyof typeof iconPaths;

function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {iconPaths[name]}
    </svg>
  );
}

function projectStyle(
  document: WritingDocument,
  project: FontProject,
): WritingDocument {
  return normalizeWritingDocument({
    ...clearGeneratedWritingReference(document),
    projectId: project.id,
    weight: 400 + project.style.thickness * 3,
    slant: project.style.slant,
    spacing: ((project.style.spacing - 50) / 160) * document.fontSize,
    widthScale: 0.78 + project.style.proportion / 220,
  });
}

function DownloadOptions({
  svg,
  format,
  busy,
  tooTallForPng,
  onDownload,
  iconOnly = false,
}: {
  svg: string;
  format: WritingFormat;
  busy: "png" | "svg" | "share" | "copy" | "font" | null;
  tooTallForPng: boolean;
  onDownload: (format: "png" | "svg") => Promise<void>;
  iconOnly?: boolean;
}) {
  const disclosure = useRef<HTMLDetailsElement>(null);
  const thumbnailId = useId().replace(/:/g, "");
  const thumbnailSvg = useMemo(
    () => svg.replaceAll("writing-fibers", `writing-fibers-${thumbnailId}`),
    [svg, thumbnailId],
  );
  return (
    <details
      ref={disclosure}
      className={styles.download}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !disclosure.current?.open) return;
        event.preventDefault();
        event.stopPropagation();
        disclosure.current.open = false;
        disclosure.current.querySelector("summary")?.focus();
      }}
    >
      <summary
        className={
          iconOnly
            ? `${styles.downloadSummary} ${styles.summaryIcon}`
            : `${styles.primary} ${styles.downloadSummary}`
        }
        aria-label={`Download page: ${format}`}
        title={iconOnly ? "Download this page" : undefined}
      >
        <Icon name="download" />
        {iconOnly ? (
          <Icon name="chevron" className={styles.downloadCaret} />
        ) : (
          <>
            {busy === "png" || busy === "svg" ? "Preparing…" : "Download page"}
            <span aria-hidden="true">⌄</span>
          </>
        )}
      </summary>
      <div className={styles.downloadPanel}>
        <div className={styles.downloadProof}>
          <div
            className={styles.downloadThumbnail}
            aria-hidden="true"
            dangerouslySetInnerHTML={{ __html: thumbnailSvg }}
          />
          <span>Your finished {format}</span>
        </div>
        <div role="group" aria-label="Download format">
          <button
            type="button"
            onClick={() => void onDownload("png")}
            disabled={!!busy || tooTallForPng}
            aria-label={`Download your ${format} as PNG`}
          >
            <strong>PNG</strong>
            <span>
              {busy === "png" ? "Preparing…" : "An image to keep or send"}
            </span>
            <Icon name="download" />
          </button>
          <button
            type="button"
            onClick={() => void onDownload("svg")}
            disabled={!!busy}
            aria-label={`Download your ${format} as SVG`}
          >
            <strong>SVG</strong>
            <span>
              {busy === "svg" ? "Preparing…" : "A crisp, scalable page"}
            </span>
            <Icon name="download" />
          </button>
        </div>
        {tooTallForPng && (
          <p>This page is too tall for PNG. Choose SVG to keep every line.</p>
        )}
      </div>
    </details>
  );
}

export default function WritingDesk({
  compact = false,
}: {
  compact?: boolean;
}) {
  const [document, setDocument] = useState<WritingDocument>({
    ...DEFAULT_WRITING_DOCUMENT,
  });
  const [projects, setProjects] = useState<FontProject[]>([]);
  const [generatedExportId, setGeneratedExportId] = useState("");
  const fontRequest = useRef<AbortController | null>(null);
  const loadedGeneratedId = useRef<string | null>(null);
  const fontHandoff = useRef(new WritingFontHandoff());
  const requestedExport = useRef<string | null>(null);
  const [accountRestored, setAccountRestored] = useState(false);
  const restoredOwner = useRef<string | null>(null);
  const [sampleFont, setSampleFont] = useState<WritingFontSource | null>(null);
  const [customFont, setCustomFont] = useState<WritingFontSource | null>(null);
  const [ready, setReady] = useState(false);
  const [saveState, setSaveState] = useState("Local draft");
  const [status, setStatus] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<
    "png" | "svg" | "share" | "copy" | "font" | null
  >(null);
  const edited = useRef(false);
  const upload = useRef<HTMLInputElement>(null);
  const preview = useRef<HTMLDivElement>(null);
  const previewBody = useRef<HTMLDivElement>(null);
  const documentRef = useRef<WritingDocument>({ ...DEFAULT_WRITING_DOCUMENT });
  const historyRef = useRef<{
    past: WritingDocument[];
    future: WritingDocument[];
    lastKey: string | null;
    lastAt: number;
  }>({ past: [], future: [], lastKey: null, lastAt: 0 });
  const [historyCount, setHistoryCount] = useState({ past: 0, future: 0 });
  const historyActions = useRef<{
    undo: () => void;
    redo: () => void;
  }>({ undo: () => undefined, redo: () => undefined });
  const [versions, setVersions] = useState<WritingVersion[]>([]);
  const [findQuery, setFindQuery] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const pagesMenu = useRef<HTMLDetailsElement>(null);
  const statusMenu = useRef<HTMLDetailsElement>(null);
  const moreMenu = useRef<HTMLDetailsElement>(null);
  const findDialog = useRef<HTMLDialogElement>(null);
  const versionsDialog = useRef<HTMLDialogElement>(null);
  const findInput = useRef<HTMLInputElement>(null);
  const [previewFit, setPreviewFit] = useState<{
    width: number;
    height: number;
  } | null>(null);
  const [stickyPreview, setStickyPreview] = useState(false);
  const previousDesign = useRef<string | null>(null);
  const session = useAccountSession();
  const [accountPages, setAccountPages] = useState<WritingDocumentWire[]>([]);
  const [accountFonts, setAccountFonts] = useState<
    { id: string; name: string }[]
  >([]);
  const documentsClient = useRef<DocumentsClient | null>(null);
  const accountService = useRef<ReturnType<
    typeof createAccountProjectService
  > | null>(null);
  const saver = useRef<DocumentSaver | null>(null);
  const sessionGeneration = useRef(0);
  const [pageId, setPageId] = useState<string | null>(null);
  const [documentBusy, setDocumentBusy] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pageSave, setPageSave] = useState<{
    state: DocumentSaveState;
    message: string;
  }>({ state: "idle", message: "" });
  const [lastSavedAt, setLastSavedAt] = useState(0);
  const [focusMode, setFocusMode] = useState(false);
  const [guides, setGuides] = useState(false);
  const [snippets, setSnippets] = useState<WritingSnippet[]>([]);
  const [printSize, setPrintSize] = useState<PrintSize>("A4");
  const printStyle = useRef<HTMLStyleElement>(null);
  const printMenu = useRef<HTMLDetailsElement>(null);
  const [libraryQuery, setLibraryQuery] = useState("");
  const [libraryFormat, setLibraryFormat] = useState<WritingFormat | "all">(
    "all",
  );
  const [librarySort, setLibrarySort] = useState<WritingPageSort>("recent");
  const [selectedPages, setSelectedPages] = useState<string[]>([]);
  const [bulkConfirm, setBulkConfirm] = useState(false);
  const { reduced } = useMotionPreference();
  const id = useId().replace(/:/g, "");
  const textSelection = useWritingSelection(
    document,
    preview,
    pageId,
    !compact,
  );
  const font = customFont || sampleFont;
  const page = useMemo(
    () => layoutWritingDocument(document, font || {}),
    [document, font],
  );
  // Guides are editor-only: exports build their own SVG without them.
  const svgOptions = useMemo(
    () => ({
      ...(font
        ? {
            fontDataUrl: font.fontDataUrl,
            fontFamily: font.fontFamily,
            fontLicense: font.fontLicense,
            measureText: font.measureText,
          }
        : {}),
      ...(guides ? { guides: true } : {}),
      ...(textSelection.selection
        ? { selection: textSelection.selection }
        : {}),
    }),
    [font, guides, textSelection.selection],
  );
  const svg = useMemo(
    () => buildWritingSvg(document, svgOptions),
    [document, svgOptions],
  );
  const customAccentActive = !accents.some(
    (accent) => accent.value === document.accent,
  );
  const inkOnPaper = writingContrastRatio(document.ink, document.paper);
  const tuningChanged =
    document.fontSize !== DEFAULT_WRITING_DOCUMENT.fontSize ||
    document.weight !== DEFAULT_WRITING_DOCUMENT.weight ||
    document.spacing !== DEFAULT_WRITING_DOCUMENT.spacing ||
    document.slant !== DEFAULT_WRITING_DOCUMENT.slant ||
    document.lineHeight !== DEFAULT_WRITING_DOCUMENT.lineHeight ||
    document.alignment !== DEFAULT_WRITING_DOCUMENT.alignment ||
    document.widthScale !== DEFAULT_WRITING_DOCUMENT.widthScale ||
    document.naturalness !== DEFAULT_WRITING_DOCUMENT.naturalness;
  const activePalette = WRITING_PALETTES.find(
    (palette) =>
      palette.accent === document.accent && palette.ink === document.ink,
  );
  const wordCount = writingWordCount(document);
  const previewPercent = writingPreviewPercent(previewFit, page.height, zoom);
  const matches = writingMatchCount(document, findQuery, matchCase);
  // Coverage is known only for fonts whose cmap can be read; unknown stays quiet.
  const missingCharacters = writingMissingCharacters(
    document,
    customFont?.coverage ?? null,
  );
  const visiblePages = useMemo(
    () =>
      filterWritingPages(
        accountPages.map((entry) => ({
          id: entry.id,
          title: entry.title || "Untitled page",
          format: entry.format,
          updatedAt: entry.updated_at,
          body: entry.document.body ?? "",
          entry,
        })),
        { query: libraryQuery, format: libraryFormat, sort: librarySort },
      ).map((item) => item.entry),
    [accountPages, libraryQuery, libraryFormat, librarySort],
  );
  const fontSource = customFont
    ? customFont.kind === "generated"
      ? "Yours"
      : "Imported"
    : document.projectId
      ? "Draft"
      : "Sample";
  const saveLabel =
    pageSave.state === "saving"
      ? "Saving…"
      : pageSave.state === "saved"
        ? "Saved to account"
        : pageSave.state === "conflict"
          ? "Changed elsewhere"
          : pageSave.state === "error"
            ? "Not saved"
            : pageSave.state === "offline"
              ? "Offline"
              : saveState === "Saved on this device" ||
                  saveState === "Account page · device backup kept"
                ? "Saved locally"
                : saveState === "Changes stay in this tab"
                  ? "This tab only"
                  : saveState;
  const saveTone =
    pageSave.state === "saving"
      ? "busy"
      : pageSave.state === "error" || pageSave.state === "conflict"
        ? "warn"
        : pageSave.state === "offline"
          ? "offline"
          : "good";

  useEffect(() => {
    const media = window.matchMedia(
      "(min-width: 781px) and (min-height: 641px)",
    );
    const update = () => setStickyPreview(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  // The stage is fixed on desktop, so the paper is measured to fit it instead of
  // relying on width alone. Zoom multiplies this fitted size in CSS.
  useIsomorphicLayoutEffect(() => {
    const body = previewBody.current;
    if (compact || !stickyPreview || !body) {
      setPreviewFit(null);
      return;
    }
    const measure = () => {
      const style = window.getComputedStyle(body);
      const width =
        body.clientWidth -
        parseFloat(style.paddingLeft) -
        parseFloat(style.paddingRight);
      const height =
        body.clientHeight -
        parseFloat(style.paddingTop) -
        parseFloat(style.paddingBottom);
      const next = fitWritingPage(
        { width: page.width, height: page.height },
        { width, height },
      );
      if (!next) return;
      setPreviewFit((current) =>
        current &&
        Math.abs(current.width - next.width) < 1 &&
        Math.abs(current.height - next.height) < 1
          ? current
          : next,
      );
    };
    measure();
    // The window listener is the reliable signal for viewport changes; the
    // observer also catches container-only shifts such as the toolbar wrapping.
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    observer.observe(body);
    return () => {
      window.removeEventListener("resize", measure);
      observer.disconnect();
    };
  }, [compact, stickyPreview, page.width, page.height]);

  useEffect(() => {
    if (!ready) return;
    const design = `${document.format}:${document.paper}:${document.accent}`;
    const previous = previousDesign.current;
    previousDesign.current = design;
    if (previous === null || previous === design) return;
    const sheet = preview.current?.querySelector("svg");
    if (!sheet?.animate) return;
    const animation = sheet.animate(
      reduced
        ? [{ opacity: 0.84 }, { opacity: 1 }]
        : [
            {
              opacity: 0.82,
              transform: "translateY(8px) rotate(-0.6deg) scale(0.985)",
            },
            {
              opacity: 1,
              transform: "translateY(-2px) rotate(0.2deg) scale(1.003)",
              offset: 0.7,
            },
            { opacity: 1, transform: "translateY(0) rotate(0) scale(1)" },
          ],
      { duration: reduced ? 180 : 320, easing: "cubic-bezier(.2,0,0,1)" },
    );
    return () => animation.cancel();
  }, [document.format, document.paper, document.accent, ready, reduced]);

  useEffect(() => {
    documentRef.current = document;
  }, [document]);

  useEffect(() => {
    if (compact) return;
    try {
      setVersions(
        readWritingVersions(window.localStorage.getItem(WRITING_VERSIONS_KEY)),
      );
    } catch {
      /* Storage can be blocked; versions stay in memory for this tab. */
    }
    try {
      setSnippets(
        readWritingSnippets(window.localStorage.getItem(WRITING_SNIPPETS_KEY)),
      );
    } catch {
      /* Saved sign-offs are a convenience; writing never waits on them. */
    }
  }, [compact]);

  // Snapshot the page once writing pauses, so Version history survives reloads
  // without recording a version on every keystroke.
  useEffect(() => {
    if (compact || !ready || !edited.current) return;
    const timer = window.setTimeout(() => {
      const value = documentRef.current;
      setVersions((current) => {
        if (
          current[0] &&
          JSON.stringify(current[0].document) === JSON.stringify(value)
        )
          return current;
        const next = appendWritingVersion(current, {
          id: crypto.randomUUID(),
          at: Date.now(),
          title: value.title || "Untitled page",
          format: value.format,
          document: value,
        });
        try {
          window.localStorage.setItem(
            WRITING_VERSIONS_KEY,
            JSON.stringify(next),
          );
        } catch {
          /* The in-memory list still works for this tab. */
        }
        return next;
      });
    }, 3500);
    return () => window.clearTimeout(timer);
  }, [document, ready, compact]);

  useIsomorphicLayoutEffect(() => {
    historyActions.current = { undo, redo };
  });

  useEffect(() => {
    if (compact) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Dismiss the active surface before stepping out of focus mode.
        if (window.document.querySelector("dialog[open]")) return;
        const openMenu = [pagesMenu, statusMenu, moreMenu, printMenu]
          .map((menu) => menu.current)
          .find((menu) => menu?.open);
        if (openMenu) {
          event.preventDefault();
          openMenu.open = false;
          openMenu.querySelector<HTMLElement>("summary")?.focus();
          return;
        }
        setFocusMode(false);
        return;
      }
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (
        target?.closest(
          "input:not([type='range']), textarea, select, [contenteditable='true']",
        )
      )
        return;
      const key = event.key.toLowerCase();
      if (key === "f" && event.shiftKey) {
        event.preventDefault();
        setFocusMode((current) => !current);
      } else if (key === "z") {
        event.preventDefault();
        if (event.shiftKey) historyActions.current.redo();
        else historyActions.current.undo();
      } else if (key === "y") {
        event.preventDefault();
        historyActions.current.redo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [compact]);

  useEffect(() => {
    if (compact) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      for (const menu of [pagesMenu, statusMenu, moreMenu, printMenu]) {
        if (menu.current?.open && !menu.current.contains(target))
          menu.current.open = false;
      }
    };
    window.document.addEventListener("pointerdown", onPointerDown);
    return () =>
      window.document.removeEventListener("pointerdown", onPointerDown);
  }, [compact]);

  function startSaver(id: string, version: number) {
    const client = documentsClient.current;
    const ownerUid = session.uid;
    if (!client || !ownerUid) return;
    saver.current?.dispose();
    const instance: DocumentSaver = new DocumentSaver({
      client,
      ownerUid,
      documentId: id,
      initialVersion: version,
      storage: typeof window === "undefined" ? null : window.localStorage,
      onState: (state, message) => {
        // A late result from a previous account never reaches this page.
        if (saver.current !== instance) return;
        setPageSave({ state, message });
      },
      onSaved: () => {
        if (saver.current === instance) setLastSavedAt(Date.now());
      },
    });
    saver.current = instance;
  }

  function syncGeneratedFontReference(next: WritingDocument, options: {preserveRequested?: boolean; force?: boolean} = {}) {
    const transition = writingFontReferenceTransition(documentRef.current.generatedExportId, next.generatedExportId, loadedGeneratedId.current);
    if (!transition.changed && !transition.clearFace && !options.force) return;
    if (!options.preserveRequested) {
      requestedExport.current = null;
      const url = new URL(window.location.href);
      if (url.searchParams.has("export")) {
        url.searchParams.delete("export");
        window.history.replaceState(null, "", url);
      }
    }
    if (fontRequest.current) {
      fontRequest.current.abort();
      fontRequest.current = null;
      setBusy((current) => current === "font" ? null : current);
    }
    if (transition.clearFace) {
      loadedGeneratedId.current = null;
      setCustomFont(null);
    }
    fontHandoff.current.reset();
    if (transition.nextExport && loadedGeneratedId.current === transition.nextExport)
      fontHandoff.current.adopt(session.uid, transition.nextExport);
    setGeneratedExportId(transition.nextExport ?? "");
  }

  function openAccountPage(page: WritingDocumentWire, restoring = false) {
    saver.current?.dispose();
    saver.current = null;
    edited.current = false;
    const next = documentFromWire(page);
    syncGeneratedFontReference(next, {preserveRequested: restoring, force: !restoring});
    documentRef.current = next;
    setDocument(next);
    resetHistory();
    setGeneratedExportId(
      (restoring ? requestedExport.current : null) ??
        page.document.generated_export_id ??
        "",
    );
    setPageId(page.id);
    setLastSavedAt(Date.parse(page.updated_at) || Date.now());
    setPageSave({ state: "saved", message: "Saved to your account" });
    startSaver(page.id, page.row_version);
    if (!page.document.generated_export_id)
      setCustomFont((current) => current?.kind === "generated" ? null : current);
    const url = new URL(window.location.href);
    url.searchParams.set("document", page.id);
    if (!restoring) url.searchParams.delete("export");
    window.history.replaceState(null, "", url);
    setStatus(`Opened “${page.title}”.`);
  }

  // Account pages load once per identity; switching accounts drops the old shelf.
  useEffect(() => {
    if (compact) return;
    const configuration = session.configuration;
    const identity = session.identity;
    const uid = session.uid;
    const attempt = ++sessionGeneration.current;
    const current = () => sessionGeneration.current === attempt;
    saver.current?.dispose();
    saver.current = null;
    documentsClient.current = null;
    accountService.current = null;
    setAccountPages([]);
    setAccountFonts([]);
    setPageId(null);
    setPageSave({ state: "idle", message: "" });
    setAccountRestored(false);
    restoredOwner.current = null;
    fontHandoff.current.reset();
    loadedGeneratedId.current = null;
    if (fontRequest.current) {
      fontRequest.current.abort();
      fontRequest.current = null;
      setBusy((current) => (current === "font" ? null : current));
    }
    setCustomFont((current) =>
      current?.kind === "generated" ? null : current,
    );
    if (!uid || !identity || configuration?.state !== "configured") {
      setAccountRestored(true);
      return;
    }
    const client = createDocumentsClient(
      configuration.apiOrigin,
      createOwnerTokenProvider(
        identity,
        uid,
        () => new DocumentError("unauthenticated", 401),
      ),
    );
    documentsClient.current = client;
    const cancellation = new AbortController();
    void client
      .list(cancellation.signal)
      .then((items) => {
        if (!current()) return;
        setAccountPages(items);
        const requested = new URLSearchParams(window.location.search).get(
          "document",
        );
        const target = requested
          ? items.find((item) => item.id === requested)
          : undefined;
        if (target) openAccountPage(target, true);
        restoredOwner.current = uid;
        setAccountRestored(true);
      })
      .catch((cause) => {
        if (
          !current() ||
          (cause instanceof DOMException && cause.name === "AbortError")
        )
          return;
        documentsClient.current = null;
        restoredOwner.current = uid;
        setAccountRestored(true);
        setPageSave({
          state: "offline",
          message:
            "Your account pages couldn’t be reached. This page stays on this device.",
        });
      });
    try {
      const service = createAccountProjectService(identity);
      accountService.current = service;
      void service
        .list()
        .then((items) => {
          if (!current()) return;
          setAccountFonts(
            items.map((item) => ({ id: item.id, name: item.name })),
          );
        })
        .catch(() => {
          /* The typeface picker still offers the sample and device projects. */
        });
    } catch {
      accountService.current = null;
    }
    return () => {
      sessionGeneration.current += 1;
      cancellation.abort();
      saver.current?.dispose();
      saver.current = null;
    };
    // openAccountPage reads only refs and setters, so identity is the only trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compact, session.configuration, session.identity, session.uid]);

  useEffect(() => {
    let cancelled = false;
    let initial = { ...DEFAULT_WRITING_DOCUMENT };
    try {
      const raw = window.localStorage.getItem(WRITING_STORAGE_KEY);
      const saved = readWritingDraft(raw);
      if (saved) {
        initial = saved;
        setSaveState("Saved on this device");
      } else if (raw)
        setNotice(
          "The saved writing draft could not be read. Your next edit will start a fresh local draft.",
        );
    } catch {
      setSaveState("Changes stay in this tab");
    }
    requestedExport.current = compact
      ? null
      : requestedWritingExport(window.location.search);
    const query = compact ? {} : parseWritingQuery(window.location.search);
    initial = normalizeWritingDocument({ ...initial, ...query });
    edited.current = Object.keys(query).length > 0;
    documentRef.current = initial;
    setDocument(initial);
    resetHistory();
    setGeneratedExportId(
      compact
        ? (initial.generatedExportId ?? "")
        : (requestedWritingExport(
            window.location.search,
            initial.generatedExportId,
          ) ?? ""),
    );
    setReady(true);
    void loadWritingFont()
      .then((loaded) => {
        if (!cancelled) setSampleFont(loaded);
      })
      .catch((cause) => {
        if (!cancelled)
          setNotice(
            cause instanceof Error
              ? cause.message
              : "The sample typeface could not be loaded.",
          );
      });
    void projectService
      .list()
      .then((items) => {
        if (cancelled) return;
        setProjects(items);
        if (query.projectId) {
          const project = items.find((item) => item.id === query.projectId);
          if (project) {
            const styled = normalizeWritingDocument({
              ...projectStyle(initial, project),
              ...query,
              ...(requestedExport.current &&
              documentRef.current.generatedExportId
                ? { generatedExportId: documentRef.current.generatedExportId }
                : {}),
            });
            documentRef.current = styled;
            setDocument(styled);
            resetHistory();
          } else
            setNotice(
              "That font project is unavailable here. You can still write with the sample typeface.",
            );
        }
      })
      .catch(() => {
        if (!cancelled)
          setNotice(
            "Font projects could not be read. The writing desk is still available with the sample typeface.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [compact]);

  useEffect(() => {
    if (compact) return;
    const target = fontHandoff.current.take({
      ready,
      accountRestored: accountRestored && restoredOwner.current === session.uid,
      settled: session.settled,
      restoring: session.restoring,
      configured: session.configured,
      uid: session.uid,
      busy: !!busy,
      exportId: requestedExport.current ?? document.generatedExportId ?? null,
    });
    if (target) void loadGeneratedFont(target, true);
    // The handoff owns its attempt key; the loader reads current refs and keeps its owner fence.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    accountRestored,
    busy,
    compact,
    document.generatedExportId,
    pageId,
    ready,
    session.configured,
    session.restoring,
    session.settled,
    session.uid,
  ]);

  useEffect(() => {
    if (!ready || !edited.current) return;
    const timer = window.setTimeout(() => {
      try {
        window.localStorage.setItem(
          WRITING_STORAGE_KEY,
          serializeWritingDraft(document),
        );
        setSaveState(
          pageId ? "Account page · device backup kept" : "Saved on this device",
        );
      } catch {
        setSaveState("Changes stay in this tab");
      }
      // Only an existing account page is autosaved; a new page needs an explicit choice.
      if (pageId) saver.current?.schedule(document, document.format);
    }, 900);
    return () => window.clearTimeout(timer);
  }, [document, pageId, ready]);

  async function saveAccountPage() {
    const client = documentsClient.current;
    if (!client || documentBusy) return;
    setDocumentBusy(true);
    setPageSave({ state: "saving", message: "Saving…" });
    try {
      const current = documentRef.current;
      const created = await client.create(
        current,
        current.format,
        crypto.randomUUID(),
      );
      if (documentsClient.current !== client) return;
      setAccountPages((current) => [created, ...current]);
      edited.current = false;
      setPageId(created.id);
      setLastSavedAt(Date.now());
      setPageSave({ state: "saved", message: "Saved to your account" });
      startSaver(created.id, created.row_version);
      const url = new URL(window.location.href);
      url.searchParams.set("document", created.id);
      window.history.replaceState(null, "", url);
      setStatus("This page is saved to your account.");
    } catch (cause) {
      setPageSave({ state: "error", message: "Couldn’t save — retry" });
      setStatus(
        cause instanceof DocumentError && cause.code === "unauthenticated"
          ? "Sign in again to save this page to your account. Your words stay on this device."
          : "This page couldn’t be saved to your account. Your words stay on this device.",
      );
    } finally {
      setDocumentBusy(false);
    }
  }

  function newAccountPage() {
    const current = documentRef.current;
    recordVersion(current);
    saver.current?.dispose();
    saver.current = null;
    edited.current = true;
    setPageId(null);
    setLastSavedAt(0);
    setPageSave({ state: "idle", message: "Not in your account yet" });
    const next = normalizeWritingDocument({
      ...DEFAULT_WRITING_DOCUMENT,
      paper: current.paper,
      ink: current.ink,
      accent: current.accent,
      weight: current.weight,
      spacing: current.spacing,
      slant: current.slant,
      widthScale: current.widthScale,
      fontSize: current.fontSize,
      lineHeight: current.lineHeight,
      ...(current.projectId ? { projectId: current.projectId } : {}),
      ...(current.generatedExportId
        ? { generatedExportId: current.generatedExportId }
        : {}),
    });
    documentRef.current = next;
    setDocument(next);
    resetHistory();
    const url = new URL(window.location.href);
    url.searchParams.delete("document");
    window.history.replaceState(null, "", url);
    setStatus("A fresh page. Save it to your account when it’s ready.");
  }

  async function deleteAccountPage(page: WritingDocumentWire) {
    const client = documentsClient.current;
    if (!client || documentBusy) return;
    setDocumentBusy(true);
    try {
      // Read the current version so an edit in another tab is not silently lost.
      const latest = await client.get(page.id);
      await client.remove(page.id, latest.row_version);
      if (documentsClient.current !== client) return;
      setAccountPages((current) =>
        current.filter((item) => item.id !== page.id),
      );
      if (pageId === page.id) newAccountPage();
      setStatus(`Deleted “${page.title}”.`);
    } catch {
      setStatus("That page couldn’t be deleted. Nothing was removed.");
    } finally {
      setDocumentBusy(false);
    }
  }

  async function resolvePageConflict(choice: "mine" | "saved") {
    if (!saver.current || !pageId) return;
    await saver.current.resolve(choice);
    if (choice === "saved" && documentsClient.current) {
      try {
        const latest = await documentsClient.current.get(pageId);
        edited.current = false;
        const next = documentFromWire(latest);
        syncGeneratedFontReference(next);
        documentRef.current = next;
        setDocument(next);
        resetHistory();
        setPageSave({ state: "saved", message: "Using the saved page" });
      } catch {
        setStatus("The saved version couldn’t be reloaded. Refresh the page.");
      }
    }
  }

  function applyDocument(
    next: WritingDocument,
    options: { key?: string } = {},
  ) {
    const history = historyRef.current;
    const now = Date.now();
    const coalesce =
      options.key &&
      history.lastKey === options.key &&
      now - history.lastAt < 700;
    if (!coalesce) {
      history.past = [...history.past.slice(-59), documentRef.current];
    }
    history.future = [];
    history.lastKey = options.key ?? null;
    history.lastAt = now;
    documentRef.current = next;
    setDocument(next);
    setHistoryCount({
      past: history.past.length,
      future: history.future.length,
    });
  }

  function update(patch: Partial<WritingDocument>, coalesceKey?: string) {
    edited.current = true;
    applyDocument(updateWritingDocument(documentRef.current, patch), {
      key: coalesceKey,
    });
    setStatus("");
  }

  function resetHistory() {
    historyRef.current = {
      past: [],
      future: [],
      lastKey: null,
      lastAt: 0,
    };
    setHistoryCount({ past: 0, future: 0 });
  }

  function undo() {
    const history = historyRef.current;
    const previous = history.past[history.past.length - 1];
    if (!previous) return;
    syncGeneratedFontReference(previous);
    history.past = history.past.slice(0, -1);
    history.future = [documentRef.current, ...history.future].slice(0, 60);
    history.lastKey = null;
    history.lastAt = 0;
    documentRef.current = previous;
    setDocument(previous);
    setHistoryCount({
      past: history.past.length,
      future: history.future.length,
    });
    edited.current = true;
    setStatus("Undid the last change.");
  }

  function redo() {
    const history = historyRef.current;
    const next = history.future[0];
    if (!next) return;
    syncGeneratedFontReference(next);
    history.future = history.future.slice(1);
    history.past = [...history.past.slice(-59), documentRef.current];
    history.lastKey = null;
    history.lastAt = 0;
    documentRef.current = next;
    setDocument(next);
    setHistoryCount({
      past: history.past.length,
      future: history.future.length,
    });
    edited.current = true;
    setStatus("Redid the change.");
  }

  function tuneSelection(patch: WritingStyle | null, key = "reset") {
    const selection = textSelection.selection;
    if (!selection) return;
    try {
      const textStyles = styleWritingSelection(
        documentRef.current.textStyles || [],
        selection,
        patch,
      );
      update(
        { textStyles },
        `selection-${selection.field}-${selection.start}-${selection.end}-${key}`,
      );
    } catch (cause) {
      setStatus(
        cause instanceof Error
          ? cause.message
          : "This selection couldn’t be changed.",
      );
    }
  }

  function resetTuning() {
    const defaults = DEFAULT_WRITING_DOCUMENT;
    update({
      fontSize: defaults.fontSize,
      lineHeight: defaults.lineHeight,
      alignment: defaults.alignment,
      weight: defaults.weight,
      spacing: defaults.spacing,
      slant: defaults.slant,
      widthScale: defaults.widthScale,
      naturalness: defaults.naturalness,
    });
  }

  function recordVersion(value: WritingDocument) {
    setVersions((current) => {
      const next = appendWritingVersion(current, {
        id: crypto.randomUUID(),
        at: Date.now(),
        title: value.title || "Untitled page",
        format: value.format,
        document: value,
      });
      try {
        window.localStorage.setItem(WRITING_VERSIONS_KEY, JSON.stringify(next));
      } catch {
        /* A full or blocked device store keeps the in-memory list. */
      }
      return next;
    });
  }

  function closeDeskMenus() {
    for (const menu of [pagesMenu, statusMenu, moreMenu]) {
      if (menu.current) menu.current.open = false;
    }
  }

  function openFindDialog() {
    closeDeskMenus();
    findDialog.current?.showModal();
    window.setTimeout(() => findInput.current?.select(), 0);
  }

  function openVersionsDialog() {
    closeDeskMenus();
    try {
      setVersions(
        readWritingVersions(window.localStorage.getItem(WRITING_VERSIONS_KEY)),
      );
    } catch {
      /* Keep the versions already loaded in memory. */
    }
    versionsDialog.current?.showModal();
  }

  function restoreVersion(version: WritingVersion) {
    const restored = normalizeWritingDocument(version.document);
    syncGeneratedFontReference(restored);
    applyDocument(restored);
    edited.current = true;
    versionsDialog.current?.close();
    setStatus(
      `Restored the version from ${savedWhen(new Date(version.at).toISOString())}.`,
    );
  }

  function replaceAll() {
    const next = replaceWritingMatches(
      documentRef.current,
      findQuery,
      replaceText,
      matchCase,
    );
    if (!next) {
      setStatus(
        findQuery ? "No matches left to replace." : "Type the words to find.",
      );
      return;
    }
    applyDocument(next);
    edited.current = true;
    setStatus(`Replaced ${matches} ${matches === 1 ? "match" : "matches"}.`);
  }

  function fitPreview() {
    setZoom(1);
    previewBody.current?.scrollTo({ top: 0, left: 0 });
  }

  function applyTemplate(templateId: WritingTemplateId) {
    const next = applyWritingTemplate(documentRef.current, templateId);
    if (!next) return;
    edited.current = true;
    applyDocument(next);
    setStatus(
      templateId === "blank"
        ? "Words cleared. Your paper, ink and handwriting settings stay."
        : "Template added. Undo brings your own words back.",
    );
  }

  function storeSnippets(next: WritingSnippet[]) {
    setSnippets(next);
    try {
      window.localStorage.setItem(WRITING_SNIPPETS_KEY, JSON.stringify(next));
    } catch {
      /* The list still works for this tab. */
    }
  }

  function saveSnippet() {
    const text = documentRef.current.signature.trim();
    if (!text) return;
    storeSnippets(appendWritingSnippet(snippets, text));
    setStatus("Sign-off saved for next time.");
  }

  function applySnippet(snippet: WritingSnippet) {
    update({ signature: snippet.text });
    setStatus("Sign-off added.");
  }

  function forgetSnippet(snippet: WritingSnippet) {
    storeSnippets(removeWritingSnippet(snippets, snippet.id));
  }

  function useSampleLetters() {
    dismissGeneratedHandoff();
    setCustomFont(null);
    loadedGeneratedId.current = null;
    if (documentRef.current.generatedExportId) {
      edited.current = true;
      applyDocument(clearGeneratedWritingReference(documentRef.current), {
        key: "font",
      });
    }
    setStatus("Sample letters are back. Your words and layout are unchanged.");
  }

  function printPage(size: PrintSize) {
    setPrintSize(size);
    // The print stylesheet reads the chosen sheet size synchronously, so the
    // dialog always opens with the size the writer just picked.
    if (printStyle.current)
      printStyle.current.textContent = `@media print { @page { size: ${size} portrait; margin: 8mm; } }`;
    if (printMenu.current) printMenu.current.open = false;
    window.print();
  }

  function toggleSelection(page: string) {
    setBulkConfirm(false);
    setSelectedPages((current) =>
      current.includes(page)
        ? current.filter((item) => item !== page)
        : [...current, page],
    );
  }

  async function deleteSelectedPages() {
    const client = documentsClient.current;
    if (!client || documentBusy || selectedPages.length === 0) return;
    setDocumentBusy(true);
    const removed: string[] = [];
    for (const target of selectedPages) {
      const page = accountPages.find((item) => item.id === target);
      if (!page) continue;
      try {
        // Each page is read first so a newer version is never silently removed.
        const latest = await client.get(page.id);
        await client.remove(page.id, latest.row_version);
        removed.push(page.id);
      } catch {
        /* Keep going; the summary reports exactly what was removed. */
      }
    }
    if (documentsClient.current !== client) return;
    setAccountPages((current) =>
      current.filter((page) => !removed.includes(page.id)),
    );
    if (pageId && removed.includes(pageId)) newAccountPage();
    setSelectedPages((current) =>
      current.filter((id) => !removed.includes(id)),
    );
    setBulkConfirm(false);
    setDocumentBusy(false);
    setStatus(
      removed.length === selectedPages.length && removed.length > 0
        ? `Deleted ${removed.length} ${removed.length === 1 ? "page" : "pages"}.`
        : `${removed.length} of ${selectedPages.length} pages were deleted. The rest stayed.`,
    );
  }

  async function copyImage() {
    if (busy) return;
    if (tooTallForPng) {
      setStatus(
        "This page is too tall to copy as an image. Download it as SVG.",
      );
      return;
    }
    setBusy("copy");
    setStatus("");
    try {
      if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined")
        throw new Error("This browser can’t copy images.");
      const blob = await exportWritingPng(
        documentRef.current,
        font || undefined,
      );
      await navigator.clipboard.write([
        new ClipboardItem({ "image/png": blob }),
      ]);
      setStatus("Image copied. Paste it into a message or document.");
    } catch (cause) {
      setStatus(
        cause instanceof Error && cause.message
          ? cause.message
          : "The image couldn’t be copied. Download it instead.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function duplicatePage() {
    const current = documentRef.current;
    const copy = normalizeWritingDocument({
      ...current,
      title: `${current.title.trim() || "Untitled page"} copy`,
    });
    closeDeskMenus();
    recordVersion(current);
    if (pageId && documentsClient.current) {
      const client = documentsClient.current;
      setDocumentBusy(true);
      try {
        const created = await client.create(
          copy,
          copy.format,
          crypto.randomUUID(),
        );
        if (documentsClient.current !== client) return;
        setAccountPages((pages) => [created, ...pages]);
        openAccountPage(created);
        setStatus(`Duplicated “${current.title}”.`);
      } catch {
        setStatus("That page couldn’t be duplicated. Nothing was changed.");
      } finally {
        setDocumentBusy(false);
      }
      return;
    }
    resetHistory();
    documentRef.current = copy;
    setDocument(copy);
    setPageId(null);
    edited.current = true;
    setStatus(
      "Started a copy on this device. The original is in Version history.",
    );
  }

  async function chooseProject(value: string) {
    if (value === "custom") return;
    dismissGeneratedHandoff();
    setCustomFont(null);
    loadedGeneratedId.current = null;
    // Each choice is one document edit, so Undo restores the previous face
    // instead of first removing an intermediate empty state.
    const project = projects.find((item) => item.id === value);
    if (project) {
      edited.current = true;
      applyDocument(projectStyle(documentRef.current, project));
      setStatus(`${project.name} style applied to the sample typeface.`);
      return;
    }
    const accountProject = accountFonts.find((item) => item.id === value);
    if (accountProject && accountService.current) {
      setStatus(`Opening “${accountProject.name}”…`);
      try {
        const loaded = await accountService.current.open(accountProject.id);
        edited.current = true;
        applyDocument(projectStyle(documentRef.current, loaded.view));
        setStatus(
          `“${accountProject.name}” style applied. Open its generated font by export ID to type with the real letters.`,
        );
      } catch {
        setStatus(
          "That account project couldn’t be opened. The sample typeface is still in use.",
        );
      }
      return;
    }
    if (
      documentRef.current.generatedExportId ||
      documentRef.current.projectId
    ) {
      edited.current = true;
      applyDocument(
        normalizeWritingDocument({
          ...clearGeneratedWritingReference(documentRef.current),
          projectId: undefined,
        }),
        { key: "font" },
      );
    }
    setStatus("Sample handwriting selected.");
  }

  function dismissGeneratedHandoff() {
    fontHandoff.current.dismiss();
    requestedExport.current = null;
    const url = new URL(window.location.href);
    if (url.searchParams.has("export")) {
      url.searchParams.delete("export");
      window.history.replaceState(null, "", url);
    }
    if (fontRequest.current) {
      fontRequest.current.abort();
      fontRequest.current = null;
      setBusy((current) => (current === "font" ? null : current));
    }
  }

  async function loadGeneratedFont(
    exportId = generatedExportId,
    automatic = false,
  ) {
    if (busy) return;
    const id = requestedWritingExport("", exportId);
    if (!id) return;
    if (!automatic) {
      dismissGeneratedHandoff();
    }
    const generation = sessionGeneration.current;
    const cancellation = new AbortController();
    fontRequest.current?.abort();
    fontRequest.current = cancellation;
    setBusy("font");
    setStatus("");
    try {
      const session = getAccountIdentityService(
        getPenforgeAuthConfiguration(window.location.hostname),
      );
      await session.connect();
      if (session.configuration.state !== "configured")
        throw new Error("unconfigured");
      const uid = session.getSnapshot().identity?.firebase_uid;
      const client = createExportClient(
        session.configuration.apiOrigin,
        createOwnerTokenProvider(
          session,
          uid,
          () => new Error("identity_changed"),
        ),
      );
      const result = await client.font(id, cancellation.signal);
      const manifest = result.metadata.build_manifest;
      if (
        !manifest?.options ||
        typeof manifest.options.fs_type !== "number" ||
        typeof manifest.options.license_description !== "string"
      )
        throw new Error("missing_manifest");
      const loaded = await loadGeneratedWritingFont(result.blob, {
        id: result.metadata.id,
        format: result.metadata.format,
        familyName: manifest.family_name,
        license: manifest.options.license_description,
        fsType: manifest.options.fs_type,
        engineeringFixture:
          result.metadata.coverage_report?.engineering_fixture === true,
      });
      if (
        cancellation.signal.aborted ||
        sessionGeneration.current !== generation ||
        session.getSnapshot().identity?.firebase_uid !== uid
      )
        return;
      setCustomFont(loaded);
      loadedGeneratedId.current = result.metadata.id;
      // The reference is part of the saved page, so returning to it renews the same bytes.
      setGeneratedExportId(result.metadata.id);
      if (documentRef.current.generatedExportId !== result.metadata.id)
        update({ projectId: undefined, generatedExportId: result.metadata.id });
      const missing = result.metadata.coverage_report?.missing;
      setStatus(
        `${result.metadata.coverage_report?.engineering_fixture ? "Engineering fixture loaded; this is not your handwriting." : "Generated Penforge font loaded."} ${Array.isArray(missing) ? missing.length : "Unknown"} missing characters; uncovered text uses fallback. PNG and SVG embed the verified font.`,
      );
    } catch (cause) {
      if (!cancellation.signal.aborted) setStatus(exportErrorText(cause));
    } finally {
      if (fontRequest.current === cancellation) {
        fontRequest.current = null;
        if (!cancellation.signal.aborted) setBusy(null);
      }
    }
  }

  useEffect(() => () => fontRequest.current?.abort(), []);

  async function importFont(file?: File) {
    if (!file || busy) return;
    setBusy("font");
    setStatus("");
    try {
      const loaded = await loadCustomWritingFont(file);
      dismissGeneratedHandoff();
      setCustomFont(loaded);
      loadedGeneratedId.current = null;
      update({ projectId: undefined, generatedExportId: undefined });
      setStatus(`${file.name} is ready. This font stays in the current tab.`);
    } catch (cause) {
      setStatus(
        cause instanceof Error
          ? cause.message
          : "This font could not be opened.",
      );
    } finally {
      setBusy(null);
      if (upload.current) upload.current.value = "";
    }
  }

  async function exportDocument(format: "png" | "svg" | "share") {
    if (busy) return;
    setBusy(format);
    setStatus("");
    const filename = `${document.title.trim() || "a-little-note"}.${format === "svg" ? "svg" : "png"}`;
    try {
      const blob =
        format === "svg"
          ? await exportWritingSvg(document, font || undefined)
          : await exportWritingPng(document, font || undefined);
      if (format === "share") {
        const file = new File([blob], filename, { type: "image/png" });
        if (navigator.canShare?.({ files: [file] }) && navigator.share) {
          await navigator.share({
            files: [file],
            title: document.title || "A little note",
          });
          setStatus("Your note was shared.");
        } else {
          downloadWritingBlob(blob, filename);
          setStatus(
            "File sharing is unavailable here. Your PNG was downloaded.",
          );
        }
      } else {
        downloadWritingBlob(blob, filename);
        setStatus(
          format === "svg"
            ? "SVG downloaded with the typeface embedded."
            : "PNG downloaded. Your handwriting is part of the image.",
        );
      }
    } catch (cause) {
      setStatus(
        cause instanceof Error && cause.name === "AbortError"
          ? "Sharing cancelled. Your note is still here."
          : cause instanceof Error
            ? cause.message
            : "The export did not finish. Please try again.",
      );
    } finally {
      setBusy(null);
    }
  }

  const handoff = new URLSearchParams({
    format: document.format,
    title: document.title,
    to: document.recipient,
    signature: document.signature,
    alignment: document.alignment,
    widthScale: String(document.widthScale),
    text: document.body,
    paper: document.paper,
    ink: document.ink,
    accent: document.accent,
    weight: String(document.weight),
    spacing: String(document.spacing),
    slant: String(document.slant),
    fontSize: String(document.fontSize),
    lineHeight: String(document.lineHeight),
    naturalness: String(document.naturalness),
    ...(document.projectId ? { project: document.projectId } : {}),
  });
  const selectedFont = customFont ? "custom" : document.projectId || "sample";
  const tooTallForPng = page.height > 12000;
  const labels = fieldLabels[document.format];
  const zoomSteps = [0.75, 1, 1.25, 1.5];

  function stepZoom(direction: -1 | 1) {
    const index = Math.max(zoomSteps.indexOf(zoom), 0);
    const next = zoomSteps[index + direction];
    if (next) setZoom(next);
  }

  const typefaceField = (
    <section
      className={styles.appSection}
      aria-labelledby={`${id}-typeface-heading`}
    >
      <h3 className={styles.appHeading} id={`${id}-typeface-heading`}>
        Typeface
      </h3>
      <div className={styles.typefaceControl}>
        <select
          id={`${id}-font`}
          aria-label="Typeface"
          className={styles.typefaceSelect}
          value={selectedFont}
          onChange={(event) => void chooseProject(event.target.value)}
        >
          <option value="sample">{SAMPLE_FONT_NAME}</option>
          {!!accountFonts.length && (
            <optgroup label="Your account projects">
              {accountFonts.map((project) => (
                <option value={project.id} key={project.id}>
                  {project.name} · account
                </option>
              ))}
            </optgroup>
          )}
          {!!projects.length && (
            <optgroup label="Demo drafts on this device">
              {projects.map((project) => (
                <option value={project.id} key={project.id}>
                  {project.name} · draft
                </option>
              ))}
            </optgroup>
          )}
          {document.projectId &&
            !projects.some((item) => item.id === document.projectId) &&
            !accountFonts.some((item) => item.id === document.projectId) && (
              <option value={document.projectId}>
                Unavailable project · sample preview
              </option>
            )}
          {customFont && (
            <option value="custom">
              {customFont.kind === "generated"
                ? customFont.name
                : `Imported · ${customFont.name}`}
            </option>
          )}
        </select>
        <span className={styles.typefaceChip} aria-hidden="true">
          {fontSource}
        </span>
        <Icon name="chevron" className={styles.selectChevron} />
      </div>
      <details className={styles.fontLibrary}>
        <summary className={styles.fontLibrarySummary}>
          <Icon name="template" />
          <span>Browse fonts</span>
          <Icon name="chevron" className={styles.fontLibraryChevron} />
        </summary>
        <div className={styles.fontLibraryBody}>
          <button
            type="button"
            className={styles.fontRow}
            aria-pressed={!customFont && !document.projectId}
            onClick={() => void chooseProject("sample")}
          >
            <span
              className={styles.fontSample}
              style={{ fontFamily: SAMPLE_FONT_CSS }}
              aria-hidden="true"
            >
              Aa
            </span>
            <span className={styles.fontRowText}>
              <strong>{SAMPLE_FONT_NAME}</strong>
              <small>The preview typeface · every page can use it</small>
            </span>
            {!customFont && !document.projectId && (
              <Icon name="check" className={styles.fontRowCheck} />
            )}
          </button>
          {customFont && (
            <div className={styles.fontRow} data-active="true">
              <span
                className={styles.fontSample}
                style={{ fontFamily: `${customFont.fontFamily}, cursive` }}
                aria-hidden="true"
              >
                Aa
              </span>
              <span className={styles.fontRowText}>
                <strong>{customFont.name}</strong>
                <small>
                  {missingCharacters.length > 0
                    ? `Missing ${missingCharacters.length} characters on this page`
                    : customFont.kind === "generated"
                      ? "Your generated font · covers this page"
                      : "Imported font · covers this page"}
                </small>
              </span>
              <Icon name="check" className={styles.fontRowCheck} />
            </div>
          )}
          {accountFonts.map((project) => (
            <button
              key={project.id}
              type="button"
              className={styles.fontRow}
              aria-pressed={document.projectId === project.id && !customFont}
              onClick={() => void chooseProject(project.id)}
              title={`Apply the style of “${project.name}” to the sample letters`}
            >
              <span
                className={styles.fontSample}
                style={{ fontFamily: SAMPLE_FONT_CSS }}
                aria-hidden="true"
              >
                Aa
              </span>
              <span className={styles.fontRowText}>
                <strong>{project.name}</strong>
                <small>Your account font · style on the sample letters</small>
              </span>
              {document.projectId === project.id && !customFont && (
                <Icon name="check" className={styles.fontRowCheck} />
              )}
            </button>
          ))}
          {projects.map((project) => (
            <button
              key={project.id}
              type="button"
              className={styles.fontRow}
              aria-pressed={document.projectId === project.id && !customFont}
              onClick={() => void chooseProject(project.id)}
              title={`Apply the style of “${project.name}” to the sample letters`}
            >
              <span
                className={styles.fontSample}
                style={{ fontFamily: SAMPLE_FONT_CSS }}
                aria-hidden="true"
              >
                Aa
              </span>
              <span className={styles.fontRowText}>
                <strong>{project.name}</strong>
                <small>Demo draft on this device</small>
              </span>
              {document.projectId === project.id && !customFont && (
                <Icon name="check" className={styles.fontRowCheck} />
              )}
            </button>
          ))}
          <p className={styles.fontLibraryHint}>
            A generated font loads by its private export ID under Advanced. To
            install one in another app,{" "}
            <Link href="/projects">download it from the Workbench</Link>.
          </p>
        </div>
      </details>
    </section>
  );

  const paperField = (
    <fieldset className={styles.paperPicker}>
      <legend>Paper</legend>
      {papers.map((paper) => (
        <button
          key={paper.id}
          type="button"
          aria-pressed={document.paper === paper.id}
          onClick={() => update({ paper: paper.id })}
        >
          <PaperThumbnail
            paper={paper.id}
            format={document.format}
            accent={document.accent}
            className={styles.paperThumbnail}
          />
          <span>{paper.name}</span>
          {document.paper === paper.id && (
            <Icon name="check" className={styles.checkBadge} />
          )}
        </button>
      ))}
    </fieldset>
  );

  const paletteField = (
    <fieldset className={styles.palettePicker}>
      <legend>Palette</legend>
      {WRITING_PALETTES.map((palette) => (
        <button
          type="button"
          key={palette.id}
          aria-label={`${palette.name} palette`}
          aria-pressed={activePalette?.id === palette.id}
          onClick={() => update({ accent: palette.accent, ink: palette.ink })}
        >
          <span className={styles.paletteDots} aria-hidden="true">
            <span style={{ backgroundColor: palette.accent }} />
            <span style={{ backgroundColor: palette.ink }} />
          </span>
          {palette.name}
          {activePalette?.id === palette.id && (
            <Icon name="check" className={styles.checkBadge} />
          )}
        </button>
      ))}
    </fieldset>
  );

  const accentField = (
    <fieldset className={styles.accentPicker}>
      <legend>Accent</legend>
      {accents.map((accent) => (
        <button
          type="button"
          key={accent.value}
          aria-label={`${accent.name} accent`}
          aria-pressed={document.accent === accent.value}
          title={accent.name}
          onClick={() => update({ accent: accent.value })}
        >
          <span aria-hidden="true" style={{ backgroundColor: accent.value }} />
          {accent.label}
          {document.accent === accent.value && (
            <Icon name="check" className={styles.checkBadge} />
          )}
        </button>
      ))}
      <label
        className={styles.customAccent}
        data-active={customAccentActive}
        title="Choose another accent colour"
      >
        <input
          type="color"
          value={document.accent}
          aria-label="Custom accent color"
          onChange={(event) => update({ accent: event.target.value })}
        />
        <span aria-hidden="true" style={{ backgroundColor: document.accent }} />
        Custom
        {customAccentActive && (
          <Icon name="check" className={styles.checkBadge} />
        )}
      </label>
    </fieldset>
  );

  const inkField = (
    <section className={styles.inkSection}>
      <span className={styles.appHeading}>Ink</span>
      <div className={styles.inkRow}>
        <div className={styles.inkSwatches} role="group" aria-label="Ink color">
          {inks.map((ink) => (
            <button
              type="button"
              aria-label={`${ink.name} ink`}
              aria-pressed={document.ink === ink.value}
              style={{ backgroundColor: ink.value }}
              key={ink.value}
              onClick={() => update({ ink: ink.value })}
            />
          ))}
          <label className={styles.customInk} title="Choose another ink color">
            <input
              type="color"
              value={document.ink}
              aria-label="Custom ink color"
              onChange={(event) => update({ ink: event.target.value })}
            />
            <span aria-hidden="true">+</span>
          </label>
        </div>
        <span
          className={styles.contrastBadge}
          data-tone={inkOnPaper < 3 ? "low" : "good"}
          role="status"
          title={
            inkOnPaper < 3
              ? `Ink and paper contrast is ${inkOnPaper.toFixed(1)}:1. Choose a darker ink or a lighter paper.`
              : `Ink and paper contrast is ${inkOnPaper.toFixed(1)}:1.`
          }
        >
          <Icon name={inkOnPaper < 3 ? "alert" : "check"} />
          {inkOnPaper < 3 ? "Low contrast" : "Good text contrast"}
        </span>
      </div>
    </section>
  );

  const fineTuneField = (
    <section
      className={styles.fineTune}
      aria-labelledby={`${id}-tuning-heading`}
    >
      <div className={styles.fineTuneHead}>
        <h3 id={`${id}-tuning-heading`}>Fine-tune handwriting</h3>
        <button
          type="button"
          className={styles.resetTuning}
          onClick={resetTuning}
          disabled={!tuningChanged}
          hidden={textSelection.scope === "selection"}
        >
          Reset
        </button>
      </div>
      <div
        ref={textSelection.controls}
        tabIndex={-1}
        className={styles.selectionScope}
        role="group"
        aria-label="Fine-tune scope"
      >
        <button
          type="button"
          aria-pressed={textSelection.scope === "page"}
          onClick={textSelection.clear}
        >
          Whole page
        </button>
        <button
          type="button"
          aria-pressed={textSelection.scope === "selection"}
          disabled={!textSelection.selection}
          onClick={() => textSelection.setScope("selection")}
        >
          Selection
        </button>
      </div>
      {textSelection.scope === "selection" && textSelection.selection ? (
        <WritingSelectionControls
          document={document}
          selection={textSelection.selection}
          id={id}
          onChange={tuneSelection}
          onReset={() => tuneSelection(null)}
        />
      ) : (
        <>
          <p className={styles.selectionHint}>
            Select words or spaces to adjust just that part.
          </p>
          <div className={styles.tuningGrid}>
            {[
              {
                key: "fontSize" as const,
                name: "Size",
                min: 24,
                max: 72,
                step: 1,
                suffix: "px",
              },
              {
                key: "weight" as const,
                name: "Weight",
                min: 400,
                max: 700,
                step: 10,
                suffix: "",
              },
              {
                key: "spacing" as const,
                name: "Spacing",
                min: -1,
                max: 3,
                step: 0.1,
                suffix: "px",
              },
              {
                key: "slant" as const,
                name: "Slant",
                min: -15,
                max: 15,
                step: 1,
                suffix: "°",
              },
              {
                key: "lineHeight" as const,
                name: "Line height",
                min: 1,
                max: 1.8,
                step: 0.05,
                suffix: "",
              },
              {
                key: "naturalness" as const,
                name: "Naturalness",
                min: 0,
                max: 1,
                step: 0.05,
                suffix: "",
              },
            ].map((control) => (
              <label
                key={control.key}
                htmlFor={`${id}-${control.key}`}
                className={styles.tuningControl}
              >
                <span className={styles.tuningLabel}>
                  {control.name}
                  <output>
                    {Number(document[control.key].toFixed(2))}
                    {control.suffix}
                  </output>
                </span>
                <input
                  id={`${id}-${control.key}`}
                  type="range"
                  min={control.min}
                  max={control.max}
                  step={control.step}
                  value={document[control.key]}
                  onChange={(event) =>
                    update(
                      { [control.key]: Number(event.target.value) },
                      control.key,
                    )
                  }
                />
              </label>
            ))}
            <div className={styles.tuningControl}>
              <span className={styles.tuningLabel}>Guides</span>
              <label className={styles.guidesToggle}>
                <input
                  type="checkbox"
                  checked={guides}
                  onChange={(event) => setGuides(event.target.checked)}
                />
                <span>Baselines</span>
              </label>
            </div>
            <div className={styles.tuningControl}>
              <span className={styles.tuningLabel}>Alignment</span>
              <div
                className={styles.alignment}
                role="group"
                aria-label="Text alignment"
              >
                {(["left", "center", "right"] as const).map((alignment) => (
                  <button
                    key={alignment}
                    type="button"
                    aria-label={`Align ${alignment}`}
                    title={`Align ${alignment}`}
                    aria-pressed={document.alignment === alignment}
                    onClick={() => update({ alignment })}
                  >
                    <Icon
                      name={
                        alignment === "left"
                          ? "alignLeft"
                          : alignment === "center"
                            ? "alignCenter"
                            : "alignRight"
                      }
                    />
                  </button>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  );

  const advancedField = (
    <details className={styles.advanced}>
      <summary>
        Advanced font options
        <span className={styles.chevron} aria-hidden="true">
          <Icon name="chevron" />
        </span>
      </summary>
      <div className={styles.advancedBody}>
        <label htmlFor={`${id}-width`} className={styles.widthControl}>
          <span>
            Width
            <output>{Number(document.widthScale.toFixed(2))}×</output>
          </span>
          <input
            id={`${id}-width`}
            type="range"
            min={0.85}
            max={1.2}
            step={0.01}
            value={document.widthScale}
            onChange={(event) =>
              update({ widthScale: Number(event.target.value) }, "widthScale")
            }
          />
        </label>
        <div className={styles.typefaceActions}>
          <label
            className={styles.importButton}
            title="Open a font file from this device"
          >
            <Icon name="upload" />
            <span>{busy === "font" ? "Opening font…" : "Import font"}</span>
            <input
              ref={upload}
              type="file"
              accept=".ttf,.otf,.woff,.woff2"
              aria-label="Upload a font file"
              disabled={!!busy}
              onChange={(event) => void importFont(event.target.files?.[0])}
            />
          </label>
          <details className={styles.exportDisclosure}>
            <summary>Use export ID</summary>
            <div className={styles.exportBody}>
              <label htmlFor={`${id}-export`}>
                Private generated export ID
                <input
                  id={`${id}-export`}
                  value={generatedExportId}
                  onChange={(event) => setGeneratedExportId(event.target.value)}
                  placeholder="Export ID from your private build"
                />
              </label>
              <button
                type="button"
                disabled={!!busy || !/^[0-9a-f-]{36}$/i.test(generatedExportId)}
                onClick={() => void loadGeneratedFont()}
              >
                Load private generated font
              </button>
              <small>
                Owner-authorized fetch. Your words stay in this tab.
              </small>
            </div>
          </details>
        </div>
        <small className={styles.typefaceHint}>
          TTF, OTF, WOFF or WOFF2 · up to 3 MB · stays in this tab
        </small>
      </div>
    </details>
  );

  return (
    <div
      className={`${styles.desk} ${compact ? styles.compact : styles.full}`}
      data-reduced-motion={reduced}
      data-focus={focusMode && !compact ? "true" : undefined}
      style={{ "--desk-accent": document.accent } as CSSProperties}
    >
      <style
        ref={printStyle}
        dangerouslySetInnerHTML={{
          __html: `@media print { @page { size: ${printSize} portrait; margin: 8mm; } }`,
        }}
      />
      {focusMode && !compact && (
        <button
          type="button"
          className={styles.focusExit}
          onClick={() => setFocusMode(false)}
          title="Leave focus mode"
        >
          <Icon name="collapse" />
          <span>Exit focus</span>
          <kbd>Esc</kbd>
        </button>
      )}
      {!compact && (
        <div className={styles.intro} id="main-content">
          <div className={styles.introCopy}>
            <p className={styles.eyebrow}>THE WRITING DESK</p>
            <h1>Put a little of you on paper.</h1>
          </div>
          <div className={styles.introActions}>
            <details ref={pagesMenu} className={styles.headerMenu}>
              <summary className={styles.headerButton}>
                <Icon name="folder" />
                <span>My pages</span>
                <Icon name="chevron" className={styles.headerChevron} />
              </summary>
              <div className={styles.pagesPanel}>
                <div className={styles.pagesHead}>
                  <span className={styles.libraryTitle}>YOUR PAGES</span>
                  <button
                    type="button"
                    className={styles.libraryButton}
                    onClick={newAccountPage}
                    title="Start a fresh page, keeping the paper and handwriting settings"
                  >
                    <Icon name="plus" />
                    <span>New page</span>
                  </button>
                </div>
                {pageSave.state === "conflict" && (
                  <div className={styles.libraryConflict} role="status">
                    <span>This page changed in another tab.</span>
                    <button
                      className={styles.panelLinkButton}
                      type="button"
                      onClick={() => void resolvePageConflict("mine")}
                    >
                      Keep my words
                    </button>
                    <button
                      className={styles.panelLinkButton}
                      type="button"
                      onClick={() => void resolvePageConflict("saved")}
                    >
                      Open the saved page
                    </button>
                  </div>
                )}
                {(pageSave.state === "error" || pageSave.state === "offline") &&
                  pageId && (
                    <div className={styles.libraryConflict} role="status">
                      <span>{pageSave.message}</span>
                      <button
                        className={styles.panelLinkButton}
                        type="button"
                        onClick={() => void saver.current?.flush()}
                        title="Send the kept words again"
                      >
                        Try saving again
                      </button>
                    </div>
                  )}
                {!session.uid ? (
                  <p className={styles.libraryEmpty}>
                    Pages live in your account.{" "}
                    <Link href="/account">Sign in</Link> to keep more than one
                    and open them on another device.
                  </p>
                ) : accountPages.length === 0 ? (
                  <p className={styles.libraryEmpty}>
                    {documentsClient.current
                      ? "No saved pages yet. Write a little, then save it to your account."
                      : "Your account pages couldn’t be reached. This page still works on this device."}
                  </p>
                ) : (
                  <>
                    <div className={styles.libraryTools}>
                      <label className={styles.librarySearch}>
                        <Icon name="search" />
                        <input
                          type="search"
                          value={libraryQuery}
                          onChange={(event) =>
                            setLibraryQuery(event.target.value)
                          }
                          placeholder="Search your pages"
                          aria-label="Search your pages"
                        />
                      </label>
                      <div
                        className={styles.libraryFilters}
                        role="group"
                        aria-label="Filter pages by format"
                      >
                        {(["all", "letter", "note", "card"] as const).map(
                          (format) => (
                            <button
                              key={format}
                              type="button"
                              aria-pressed={libraryFormat === format}
                              onClick={() => setLibraryFormat(format)}
                            >
                              {format === "all"
                                ? "All"
                                : format[0].toUpperCase() + format.slice(1)}
                            </button>
                          ),
                        )}
                      </div>
                      <label className={styles.librarySort}>
                        Sort
                        <select
                          value={librarySort}
                          onChange={(event) =>
                            setLibrarySort(
                              event.target.value as WritingPageSort,
                            )
                          }
                        >
                          <option value="recent">Newest first</option>
                          <option value="oldest">Oldest first</option>
                          <option value="title">Title A–Z</option>
                        </select>
                      </label>
                    </div>
                    {visiblePages.length === 0 ? (
                      <p className={styles.libraryEmpty}>
                        No pages match
                        {libraryQuery ? ` “${libraryQuery}”.` : " this filter."}
                      </p>
                    ) : (
                      <ul className={styles.libraryList}>
                        {visiblePages.map((entry) => (
                          <li
                            key={entry.id}
                            className={
                              selectedPages.includes(entry.id)
                                ? styles.libraryItemSelected
                                : entry.id === pageId
                                  ? styles.libraryItemOn
                                  : styles.libraryItem
                            }
                          >
                            <label className={styles.libraryCheck}>
                              <input
                                type="checkbox"
                                checked={selectedPages.includes(entry.id)}
                                onChange={() => toggleSelection(entry.id)}
                                aria-label={`Select “${entry.title}” for bulk actions`}
                              />
                            </label>
                            <PaperThumbnail
                              paper={entry.document.paper ?? "cotton"}
                              format={entry.format}
                              accent={entry.document.accent}
                              className={styles.libraryThumb}
                            />
                            <button
                              type="button"
                              className={styles.libraryOpen}
                              onClick={() => openAccountPage(entry)}
                              aria-current={
                                entry.id === pageId ? "true" : undefined
                              }
                              title={`Open “${entry.title}”`}
                            >
                              <strong>{entry.title}</strong>
                              <small>
                                {entry.format} ·{" "}
                                {writingWhenLabel(
                                  Date.parse(entry.updated_at),
                                ) || "saved"}
                              </small>
                            </button>
                            <button
                              type="button"
                              className={styles.libraryRemove}
                              onClick={() => void deleteAccountPage(entry)}
                              disabled={documentBusy}
                              aria-label={`Delete “${entry.title}” from your account`}
                              title="Delete this page"
                            >
                              <Icon name="trash" />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    {selectedPages.length > 0 && (
                      <div className={styles.libraryBulk} role="status">
                        <span>
                          {selectedPages.length}{" "}
                          {selectedPages.length === 1 ? "page" : "pages"}{" "}
                          selected
                        </span>
                        {bulkConfirm ? (
                          <>
                            <button
                              type="button"
                              className={styles.libraryDanger}
                              onClick={() => void deleteSelectedPages()}
                              disabled={documentBusy}
                            >
                              {documentBusy
                                ? "Deleting…"
                                : `Delete ${selectedPages.length}`}
                            </button>
                            <button
                              type="button"
                              onClick={() => setBulkConfirm(false)}
                            >
                              Keep them
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              type="button"
                              onClick={() => setSelectedPages([])}
                            >
                              Clear
                            </button>
                            <button
                              type="button"
                              className={styles.libraryDanger}
                              onClick={() => setBulkConfirm(true)}
                            >
                              Delete selected
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </>
                )}
                {!pageId && session.uid && (
                  <button
                    type="button"
                    className={styles.panelSave}
                    disabled={!documentsClient.current || documentBusy}
                    onClick={() => void saveAccountPage()}
                    title="Keep this page in your account"
                  >
                    {documentBusy ? "Saving…" : "Save this page to my account"}
                  </button>
                )}
                <div className={styles.panelLinks}>
                  <Link href="/projects">Workbench</Link>
                  <Link href="/create">
                    Create a font <Icon name="arrow" />
                  </Link>
                </div>
              </div>
            </details>
            <details ref={statusMenu} className={styles.headerMenu}>
              <summary className={styles.statusButton} data-tone={saveTone}>
                <span className={styles.statusDot} aria-hidden="true" />
                {saveLabel}
                <Icon name="chevron" className={styles.headerChevron} />
              </summary>
              <div className={styles.statusPanel}>
                <strong>{saveLabel}</strong>
                <p>
                  {pageId
                    ? "This page is saved to your account and backed up on this device."
                    : session.uid
                      ? "This page lives in this browser until you save it to your account."
                      : "This page lives in this browser. Sign in to keep pages across devices."}
                </p>
                {lastSavedAt > 0 && (
                  <p className={styles.statusWhen}>
                    Last saved {writingWhenLabel(lastSavedAt)}
                  </p>
                )}
                {!pageId && session.uid && (
                  <button
                    type="button"
                    className={styles.panelSave}
                    disabled={!documentsClient.current || documentBusy}
                    onClick={() => void saveAccountPage()}
                  >
                    {documentBusy ? "Saving…" : "Save to my account"}
                  </button>
                )}
                {!session.uid && (
                  <Link href="/account" className={styles.panelSave}>
                    Sign in
                  </Link>
                )}
              </div>
            </details>
          </div>
        </div>
      )}
      <div className={styles.workspace}>
        <section className={styles.editor} aria-label="Writing controls">
          <div
            className={styles.formatPicker}
            role="group"
            aria-label="Document format"
          >
            {formats.map((format) => (
              <button
                type="button"
                key={format}
                aria-pressed={document.format === format}
                onClick={() => update({ format })}
              >
                <span
                  className={styles.formatArtwork}
                  data-format={format}
                  aria-hidden="true"
                >
                  <PaperThumbnail
                    paper={document.paper}
                    format={format}
                    accent={document.accent}
                    className={styles.formatThumbnail}
                  />
                </span>
                <span>{format[0].toUpperCase() + format.slice(1)}</span>
                {document.format === format && (
                  <Icon name="check" className={styles.checkBadge} />
                )}
              </button>
            ))}
          </div>
          {!compact && (
            <details className={styles.templates}>
              <summary>
                <Icon name="template" />
                <span>Start from a template</span>
                <Icon name="chevron" className={styles.templatesChevron} />
              </summary>
              <div className={styles.templateList}>
                {WRITING_TEMPLATES.map((template) => (
                  <button
                    key={template.id}
                    type="button"
                    onClick={() => applyTemplate(template.id)}
                    title={
                      template.id === "blank"
                        ? "Clear the words and start clean"
                        : `Fill the page with a ${template.name.toLowerCase()} opening`
                    }
                  >
                    {template.name}
                  </button>
                ))}
              </div>
              <p className={styles.templatesNote}>
                Fills the words only. Undo keeps what you had.
              </p>
            </details>
          )}
          {!compact && (
            <>
              <div className={styles.wordsSelectionHead}>
                <h2 className={styles.wordsHeading}>Your words</h2>
                <button
                  type="button"
                  className={styles.selectionAction}
                  disabled={!textSelection.selection}
                  onClick={textSelection.fineTune}
                >
                  Fine-tune selection
                </button>
              </div>
              <div className={styles.wordsRow}>
                <label htmlFor={`${id}-title`}>
                  {labels.title}
                  <input
                    id={`${id}-title`}
                    value={document.title}
                    data-writing-field="title"
                    onSelect={(event) =>
                      textSelection.capture(event.currentTarget)
                    }
                    onContextMenu={textSelection.contextMenu}
                    maxLength={80}
                    onChange={(event) =>
                      update({ title: event.target.value }, "title")
                    }
                    placeholder="A little note"
                  />
                </label>
                <label htmlFor={`${id}-recipient`}>
                  {labels.recipient}
                  <input
                    id={`${id}-recipient`}
                    value={document.recipient}
                    data-writing-field="recipient"
                    onSelect={(event) =>
                      textSelection.capture(event.currentTarget)
                    }
                    onContextMenu={textSelection.contextMenu}
                    maxLength={100}
                    onChange={(event) =>
                      update({ recipient: event.target.value }, "recipient")
                    }
                    placeholder="Dear you,"
                  />
                </label>
              </div>
            </>
          )}
          <label className={styles.bodyField} htmlFor={`${id}-body`}>
            <span>
              {labels.body}
              <small>
                {Array.from(document.body).length}/{MAX_WRITING_BODY}
              </small>
            </span>
            <textarea
              id={`${id}-body`}
              value={document.body}
              data-writing-field="body"
              onSelect={(event) => textSelection.capture(event.currentTarget)}
              onContextMenu={textSelection.contextMenu}
              maxLength={MAX_WRITING_BODY}
              rows={compact ? 4 : 6}
              onChange={(event) => update({ body: event.target.value }, "body")}
              spellCheck
              placeholder="What would you like to say?"
            />
          </label>
          {!compact && (
            <div className={styles.signatureField}>
              <span className={styles.signatureHead}>
                <label htmlFor={`${id}-signature`}>{labels.signature}</label>
                <details className={styles.snippets}>
                  <summary className={styles.snippetToggle}>Sign-offs</summary>
                  <div className={styles.snippetPanel}>
                    {snippets.length === 0 ? (
                      <p>Save a sign-off you use often and reuse it.</p>
                    ) : (
                      <ul>
                        {snippets.map((snippet) => (
                          <li key={snippet.id}>
                            <button
                              type="button"
                              onClick={() => applySnippet(snippet)}
                              title="Use this sign-off"
                            >
                              {snippet.text}
                            </button>
                            <button
                              type="button"
                              className={styles.snippetRemove}
                              aria-label={`Remove the saved sign-off “${snippet.text}”`}
                              onClick={() => forgetSnippet(snippet)}
                            >
                              <Icon name="trash" />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    <button
                      type="button"
                      className={styles.snippetSave}
                      disabled={!document.signature.trim()}
                      onClick={saveSnippet}
                    >
                      {document.signature.trim()
                        ? "Save this sign-off"
                        : "Write a sign-off first"}
                    </button>
                  </div>
                </details>
              </span>
              <textarea
                id={`${id}-signature`}
                value={document.signature}
                data-writing-field="signature"
                onSelect={(event) => textSelection.capture(event.currentTarget)}
                onContextMenu={textSelection.contextMenu}
                maxLength={100}
                rows={2}
                onChange={(event) =>
                  update({ signature: event.target.value }, "signature")
                }
                placeholder="With love,"
              />
            </div>
          )}
          {compact && (
            <div className={styles.surfaceControls}>
              {paperField}
              {accentField}
            </div>
          )}
          {compact && (
            <details className={styles.personalize}>
              <summary>
                Personalize <span aria-hidden="true">+</span>
              </summary>
              <div>
                <label htmlFor={`${id}-title`}>
                  {labels.title}
                  <input
                    id={`${id}-title`}
                    value={document.title}
                    data-writing-field="title"
                    onSelect={(event) =>
                      textSelection.capture(event.currentTarget)
                    }
                    onContextMenu={textSelection.contextMenu}
                    maxLength={80}
                    onChange={(event) => update({ title: event.target.value })}
                  />
                </label>
                <label htmlFor={`${id}-recipient`}>
                  {labels.recipient}
                  <input
                    id={`${id}-recipient`}
                    value={document.recipient}
                    data-writing-field="recipient"
                    onSelect={(event) =>
                      textSelection.capture(event.currentTarget)
                    }
                    onContextMenu={textSelection.contextMenu}
                    maxLength={100}
                    onChange={(event) =>
                      update({ recipient: event.target.value })
                    }
                  />
                </label>
                <label htmlFor={`${id}-signature`}>
                  {labels.signature}
                  <textarea
                    id={`${id}-signature`}
                    value={document.signature}
                    data-writing-field="signature"
                    onSelect={(event) =>
                      textSelection.capture(event.currentTarget)
                    }
                    onContextMenu={textSelection.contextMenu}
                    maxLength={100}
                    rows={2}
                    onChange={(event) =>
                      update({ signature: event.target.value })
                    }
                  />
                </label>
              </div>
            </details>
          )}
          {compact && inkField}
          {compact && (
            <div className={styles.compactActions}>
              <div className={styles.compactExports}>
                <DownloadOptions
                  svg={svg}
                  format={document.format}
                  busy={busy}
                  tooTallForPng={tooTallForPng}
                  onDownload={exportDocument}
                />
                <button
                  type="button"
                  className={styles.secondary}
                  disabled={!!busy || tooTallForPng}
                  onClick={() => void exportDocument("share")}
                  aria-label={`Share your ${document.format} as a PNG`}
                >
                  <Icon name="share" />
                  {busy === "share" ? "Preparing…" : "Share"}
                </button>
              </div>
              <Link href={`/write?${handoff}`}>
                Open writing desk <Icon name="arrow" />
              </Link>
            </div>
          )}
          {!compact && (
            <p className={styles.wordCount}>
              {wordCount} {wordCount === 1 ? "word" : "words"}
            </p>
          )}
          {!compact && (
            <p className={styles.deviceNote}>
              <span aria-hidden="true" />
              Your words stay on this device until you save the page to your
              account.
            </p>
          )}
        </section>
        <section
          className={styles.previewPane}
          aria-label="Your writing preview"
        >
          {compact ? (
            <div className={styles.previewLabel}>
              <span>
                {customFont
                  ? customFont.kind === "generated"
                    ? customFont.engineeringFixture
                      ? "Engineering fixture · not handwriting"
                      : "Your generated Penforge font"
                    : "Your imported typeface"
                  : "Draft preview · sample typeface"}
              </span>
              <span>
                {page.width} × {page.height}
              </span>
            </div>
          ) : (
            <div className={styles.previewToolbar}>
              <span className={styles.previewTitle}>
                <Icon name="eye" />
                Preview
                <span className={styles.livePill}>
                  <span aria-hidden="true" />
                  Live
                </span>
              </span>
              <div className={styles.previewActions}>
                <div
                  className={styles.historyGroup}
                  role="group"
                  aria-label="Change history"
                >
                  <button
                    type="button"
                    className={styles.toolButton}
                    onClick={undo}
                    disabled={!historyCount.past}
                    aria-label="Undo the last change"
                    title="Undo the last change"
                  >
                    <Icon name="undo" />
                  </button>
                  <button
                    type="button"
                    className={styles.toolButton}
                    onClick={redo}
                    disabled={!historyCount.future}
                    aria-label="Redo the change"
                    title="Redo the change"
                  >
                    <Icon name="redo" />
                  </button>
                </div>
                <button
                  type="button"
                  className={styles.toolButton}
                  onClick={fitPreview}
                  aria-label="Fit height"
                  title="Fit height"
                >
                  <Icon name="fit" />
                </button>
                <button
                  type="button"
                  className={styles.toolButton}
                  onClick={() => setFocusMode((current) => !current)}
                  aria-pressed={focusMode}
                  aria-label="Focus mode: hide the columns and keep the page"
                  title="Focus mode (⌘⇧F)"
                >
                  <Icon name="expand" />
                </button>
                <details ref={moreMenu} className={styles.moreMenu}>
                  <summary
                    className={styles.toolButton}
                    aria-label="More page actions"
                    title="More"
                  >
                    <Icon name="more" />
                  </summary>
                  <div className={styles.morePanel}>
                    <button type="button" onClick={openFindDialog}>
                      <Icon name="search" />
                      Find &amp; replace
                    </button>
                    <button
                      type="button"
                      onClick={() => void duplicatePage()}
                      disabled={documentBusy}
                    >
                      <Icon name="duplicate" />
                      Duplicate page
                    </button>
                    <button type="button" onClick={openVersionsDialog}>
                      <Icon name="history" />
                      Version history
                    </button>
                  </div>
                </details>
                <button
                  type="button"
                  className={styles.toolButton}
                  disabled={!!busy || tooTallForPng}
                  onClick={() => void copyImage()}
                  aria-label="Copy the page as an image"
                  title="Copy image"
                >
                  <Icon name="copy" />
                </button>
                <button
                  type="button"
                  className={styles.toolButton}
                  disabled={!!busy || tooTallForPng}
                  onClick={() => void exportDocument("share")}
                  aria-label={`Share your ${document.format} as a PNG`}
                  title="Share as an image"
                >
                  <Icon name="share" />
                </button>
                <details ref={printMenu} className={styles.printMenu}>
                  <summary
                    className={styles.toolButton}
                    aria-label={`Print or save your ${document.format} as a PDF`}
                    title="Print or save as PDF"
                  >
                    <Icon name="print" />
                  </summary>
                  <div className={styles.printPanel}>
                    <strong>Page size</strong>
                    <div role="group" aria-label="PDF page size">
                      {printSizes.map((size) => (
                        <button
                          key={size.id}
                          type="button"
                          aria-pressed={printSize === size.id}
                          onClick={() => printPage(size.id)}
                        >
                          <span>{size.name}</span>
                          <small>{size.note}</small>
                        </button>
                      ))}
                    </div>
                    <p>
                      Choose Save as PDF in the print dialog. Paper, ink and
                      your handwriting are preserved.
                    </p>
                  </div>
                </details>
                <div className={styles.toolbarDownload}>
                  <DownloadOptions
                    svg={svg}
                    format={document.format}
                    busy={busy}
                    tooTallForPng={tooTallForPng}
                    onDownload={exportDocument}
                    iconOnly
                  />
                </div>
              </div>
            </div>
          )}
          {!compact && missingCharacters.length > 0 && (
            <div className={styles.coverageNote} role="status">
              <Icon name="alert" />
              <span>
                {missingCharacters.length === 1
                  ? `“${missingCharacters[0]}” isn’t in this font yet.`
                  : `${missingCharacters.length} characters on this page aren’t in this font yet: ${missingCharacters.slice(0, 6).join(" ")}${missingCharacters.length > 6 ? "…" : ""}`}
              </span>
              <button type="button" onClick={useSampleLetters}>
                Use sample letters
              </button>
              <Link
                href={`/create${document.projectId ? `?project=${encodeURIComponent(document.projectId)}` : ""}`}
              >
                Fix in a font
              </Link>
            </div>
          )}
          {compact ? (
            <div
              ref={preview}
              className={styles.previewViewport}
              data-format={document.format}
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          ) : (
            <div ref={previewBody} className={styles.previewBody}>
              <div
                ref={preview}
                onContextMenu={textSelection.contextMenu}
                tabIndex={0}
                aria-label="Paper preview. Select text to fine-tune it."
                className={styles.previewViewport}
                data-format={document.format}
                data-zoomed={zoom !== 1}
                data-fitted={previewFit ? "true" : undefined}
                style={
                  {
                    "--preview-zoom": zoom,
                    ...(previewFit
                      ? {
                          "--preview-fit-width": `${previewFit.width}px`,
                          "--preview-fit-height": `${previewFit.height}px`,
                        }
                      : {}),
                  } as CSSProperties
                }
                dangerouslySetInnerHTML={{ __html: svg }}
              />
            </div>
          )}
          {!compact && (
            <div className={styles.previewFooter}>
              <span
                className={styles.exportStatus}
                data-tone={tooTallForPng ? "warn" : "good"}
              >
                <Icon name={tooTallForPng ? "alert" : "check"} />
                {tooTallForPng ? "Too tall for PNG" : "Fits export limits"}
              </span>
              <div
                className={styles.zoomGroup}
                role="group"
                aria-label="Preview zoom"
              >
                <button
                  type="button"
                  onClick={() => stepZoom(-1)}
                  disabled={zoom <= zoomSteps[0]}
                  aria-label="Zoom out"
                >
                  −
                </button>
                <button
                  type="button"
                  className={styles.zoomFit}
                  onClick={fitPreview}
                  aria-label="Fit the page to the preview"
                  title="Fit the page to the height"
                >
                  Fit height
                </button>
                <button
                  type="button"
                  onClick={() => stepZoom(1)}
                  disabled={zoom >= zoomSteps[zoomSteps.length - 1]}
                  aria-label="Zoom in"
                >
                  +
                </button>
              </div>
              <span className={styles.zoomPercent}>{previewPercent}%</span>
            </div>
          )}
        </section>
        {!compact && (
          <aside className={styles.appearance} aria-label="Appearance">
            <h2 className={styles.appearanceTitle}>Appearance</h2>
            {typefaceField}
            {paperField}
            {paletteField}
            {accentField}
            {inkField}
            {fineTuneField}
            {advancedField}
          </aside>
        )}
      </div>
      {!compact && textSelection.menu && (
        <div
          ref={textSelection.menuRef}
          className={styles.selectionMenu}
          role="menu"
          aria-label="Selected text"
          style={{ left: textSelection.menu.x, top: textSelection.menu.y }}
        >
          <button
            type="button"
            role="menuitem"
            onClick={textSelection.fineTune}
          >
            Fine-tune selection <span aria-hidden="true">↗</span>
          </button>
        </div>
      )}
      {!compact && (
        <>
          <dialog
            ref={findDialog}
            className={styles.dialog}
            aria-labelledby={`${id}-find-title`}
            onMouseDown={(event) => {
              if (event.target !== event.currentTarget) return;
              const box = event.currentTarget.getBoundingClientRect();
              if (
                event.clientX < box.left ||
                event.clientX > box.right ||
                event.clientY < box.top ||
                event.clientY > box.bottom
              )
                event.currentTarget.close();
            }}
          >
            <div className={styles.dialogHead}>
              <h2 id={`${id}-find-title`}>Find &amp; replace</h2>
              <button
                type="button"
                className={styles.dialogClose}
                onClick={() => findDialog.current?.close()}
                aria-label="Close find and replace"
              >
                ×
              </button>
            </div>
            <div className={styles.dialogFields}>
              <label htmlFor={`${id}-find`}>
                Find
                <input
                  ref={findInput}
                  id={`${id}-find`}
                  value={findQuery}
                  onChange={(event) => setFindQuery(event.target.value)}
                  placeholder="Words to find"
                  autoComplete="off"
                />
              </label>
              <label htmlFor={`${id}-replace`}>
                Replace with
                <input
                  id={`${id}-replace`}
                  value={replaceText}
                  onChange={(event) => setReplaceText(event.target.value)}
                  placeholder="New words"
                  autoComplete="off"
                />
              </label>
            </div>
            <label className={styles.dialogCheck}>
              <input
                type="checkbox"
                checked={matchCase}
                onChange={(event) => setMatchCase(event.target.checked)}
              />
              Match case
            </label>
            <div className={styles.dialogFoot}>
              <span className={styles.dialogCount} role="status">
                {findQuery
                  ? `${matches} ${matches === 1 ? "match" : "matches"}`
                  : "Type words to find across the page"}
              </span>
              <div className={styles.dialogActions}>
                <button
                  type="button"
                  className={styles.dialogGhost}
                  onClick={() => findDialog.current?.close()}
                >
                  Close
                </button>
                <button
                  type="button"
                  className={styles.dialogPrimary}
                  onClick={replaceAll}
                  disabled={!findQuery || matches === 0}
                >
                  Replace all
                </button>
              </div>
            </div>
          </dialog>

          <dialog
            ref={versionsDialog}
            className={`${styles.dialog} ${styles.versionsDialog}`}
            aria-labelledby={`${id}-versions-title`}
            onMouseDown={(event) => {
              if (event.target !== event.currentTarget) return;
              const box = event.currentTarget.getBoundingClientRect();
              if (
                event.clientX < box.left ||
                event.clientX > box.right ||
                event.clientY < box.top ||
                event.clientY > box.bottom
              )
                event.currentTarget.close();
            }}
          >
            <div className={styles.dialogHead}>
              <h2 id={`${id}-versions-title`}>Version history</h2>
              <button
                type="button"
                className={styles.dialogClose}
                onClick={() => versionsDialog.current?.close()}
                aria-label="Close version history"
              >
                ×
              </button>
            </div>
            <p className={styles.dialogNote}>
              Kept on this device. The newest {MAX_WRITING_VERSIONS} versions
              save automatically when you pause.
            </p>
            {versions.length === 0 ? (
              <p className={styles.dialogEmpty}>
                Nothing saved yet. Versions appear here after you write.
              </p>
            ) : (
              <ul className={styles.versionList}>
                {versions.map((version) => (
                  <li key={version.id}>
                    <div>
                      <strong>{version.title || "Untitled page"}</strong>
                      <small>
                        {savedWhen(new Date(version.at).toISOString())} ·{" "}
                        {version.format}
                      </small>
                    </div>
                    <button
                      type="button"
                      className={styles.dialogGhost}
                      onClick={() => restoreVersion(version)}
                    >
                      Restore
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className={styles.dialogFoot}>
              <span />
              <button
                type="button"
                className={styles.dialogPrimary}
                onClick={() => versionsDialog.current?.close()}
              >
                Done
              </button>
            </div>
          </dialog>
        </>
      )}
      <div className={styles.feedback} role="status" aria-live="polite">
        {status || notice}
      </div>
    </div>
  );
}
