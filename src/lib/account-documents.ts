/** Owner-scoped saved Writing Desk documents. Imported font files stay device-only. */
import type { components } from "../../packages/contracts/src/generated";
import {
  createAccountRequest,
  type AccountRequestOptions,
} from "./account-request";
import type { WritingDocument, WritingFormat } from "./writing";
import { normalizeWritingDocument } from "./writing";

export type WritingDocumentWire =
  components["schemas"]["WritingDocumentResponse"];
export type WritingBodyWire = components["schemas"]["WritingBody"];

export class DocumentError extends Error {
  constructor(
    readonly code:
      | "account_required"
      | "email_verification_required"
      | "terms_required"
      | "policy_changed"
      | "service_not_ready"
      | "unauthenticated"
      | "not_found"
      | "revision_conflict"
      | "precondition_required"
      | "validation_error"
      | "dependency_unavailable"
      | "network_unavailable"
      | "recovery_unavailable",
    readonly status = 0,
    readonly currentVersion?: number,
  ) {
    super("The saved document could not be confirmed.");
    this.name = "DocumentError";
  }
}

const codes = new Set([
  "account_required",
  "email_verification_required",
  "terms_required",
  "policy_changed",
  "service_not_ready",

  "unauthenticated",
  "invalid_token",
  "account_merged",
  "account_deleting",
  "account_suspended",
  "not_found",
  "revision_conflict",
  "precondition_required",
  "validation_error",
  "dependency_unavailable",
  "rate_limited",
]);

export function writingBody(document: WritingDocument): WritingBodyWire {
  return {
    paper: document.paper,
    recipient: document.recipient,
    body: document.body,
    signature: document.signature,
    ink: document.ink,
    accent: document.accent,
    font_size: document.fontSize,
    line_height: document.lineHeight,
    alignment: document.alignment,
    weight: document.weight,
    spacing: document.spacing,
    slant: document.slant,
    width_scale: document.widthScale,
    naturalness: document.naturalness,
    ...(document.projectId ? { project_id: document.projectId } : {}),
    ...(document.generatedExportId
      ? { generated_export_id: document.generatedExportId }
      : {}),
  };
}

export function documentFromWire(wire: WritingDocumentWire): WritingDocument {
  return normalizeWritingDocument({
    format: wire.format,
    paper: wire.document.paper,
    title: wire.title,
    recipient: wire.document.recipient,
    body: wire.document.body,
    signature: wire.document.signature,
    ink: wire.document.ink,
    accent: wire.document.accent,
    fontSize: wire.document.font_size,
    lineHeight: wire.document.line_height,
    alignment: wire.document.alignment,
    weight: wire.document.weight,
    spacing: wire.document.spacing,
    slant: wire.document.slant,
    naturalness: wire.document.naturalness,
    widthScale: wire.document.width_scale,
    ...(wire.document.project_id
      ? { projectId: wire.document.project_id }
      : {}),
    ...(wire.document.generated_export_id
      ? { generatedExportId: wire.document.generated_export_id }
      : {}),
  });
}

export function createDocumentsClient(
  baseUrl: string,
  token: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  const send = createAccountRequest(
    baseUrl,
    token,
    { error: DocumentError, codes },
    fetcher,
  );
  async function request<T>(
    path: string,
    options: AccountRequestOptions = {},
  ): Promise<{ data?: T; etag: string | null }> {
    const response = await send(path, options);
    if (response.status === 204) return { etag: null };
    return {
      data: (await response.json()) as T,
      etag: response.headers.get("ETag"),
    };
  }
  const base = "/api/v1/documents";
  return {
    list: async (signal?: AbortSignal): Promise<WritingDocumentWire[]> => {
      const items: WritingDocumentWire[] = [];
      let cursor: string | null = null;
      for (let page = 0; page < 20; page += 1) {
        const result: {
          data?: { items: WritingDocumentWire[]; next_cursor: string | null };
          etag: string | null;
        } = await request<{
          items: WritingDocumentWire[];
          next_cursor: string | null;
        }>(
          `${base}?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
          { signal },
        );
        items.push(...(result.data?.items ?? []));
        cursor = result.data?.next_cursor ?? null;
        if (!cursor) break;
      }
      return items;
    },
    get: async (id: string, signal?: AbortSignal) =>
      (
        await request<WritingDocumentWire>(
          `${base}/${encodeURIComponent(id)}`,
          { signal },
        )
      ).data as WritingDocumentWire,
    create: async (
      document: WritingDocument,
      format: WritingFormat,
      key: string,
      signal?: AbortSignal,
    ) =>
      (
        await request<WritingDocumentWire>(base, {
          method: "POST",
          body: {
            title: document.title,
            format,
            document: writingBody(document),
          },
          key,
          signal,
        })
      ).data as WritingDocumentWire,
    update: async (
      id: string,
      document: WritingDocument,
      format: WritingFormat,
      version: number,
      signal?: AbortSignal,
    ) =>
      (
        await request<WritingDocumentWire>(
          `${base}/${encodeURIComponent(id)}`,
          {
            method: "PATCH",
            body: {
              title: document.title,
              format,
              document: writingBody(document),
            },
            ifMatch: `"v${version}"`,
            signal,
          },
        )
      ).data as WritingDocumentWire,
    remove: async (id: string, version: number, signal?: AbortSignal) => {
      await request<void>(`${base}/${encodeURIComponent(id)}`, {
        method: "DELETE",
        ifMatch: `"v${version}"`,
        signal,
      });
    },
  };
}
export type DocumentsClient = ReturnType<typeof createDocumentsClient>;

export type DocumentSaveState =
  "idle" | "saving" | "saved" | "offline" | "error" | "conflict";

type PendingDocument = {
  schemaVersion: 1;
  ownerUid: string;
  documentId: string;
  version: number;
  body: { title: string; format: WritingFormat; document: WritingBodyWire };
  createdAt: string;
};

const DOCUMENT_OP_PREFIX = "penforge-document-op-v1:";
export const DOCUMENT_RECOVERY_PREFIX = DOCUMENT_OP_PREFIX;

function key(ownerUid: string, documentId: string) {
  return `${DOCUMENT_OP_PREFIX}${encodeURIComponent(ownerUid)}:${encodeURIComponent(documentId)}`;
}

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export class DocumentSaver {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private version: number;
  private inFlight = false;
  private queued = false;
  private disposed = false;
  private latest: { document: WritingDocument; format: WritingFormat } | null =
    null;

  constructor(
    private readonly options: {
      client: DocumentsClient;
      ownerUid: string;
      documentId: string;
      initialVersion: number;
      storage: StorageLike | null;
      onState: (state: DocumentSaveState, message: string) => void;
      onSaved: (version: number) => void;
    },
  ) {
    this.version = options.initialVersion;
  }

  schedule(document: WritingDocument, format: WritingFormat) {
    if (this.disposed) return;
    this.latest = { document, format };
    this.options.onState("saving", "Saving…");
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, 700);
  }

  private persistPending(value: PendingDocument) {
    try {
      this.options.storage?.setItem(
        key(value.ownerUid, value.documentId),
        JSON.stringify(value),
      );
    } catch {
      throw new DocumentError("recovery_unavailable");
    }
  }

  private clearPending() {
    try {
      this.options.storage?.removeItem(
        key(this.options.ownerUid, this.options.documentId),
      );
    } catch {
      /* confirmed server write remains authoritative */
    }
  }

  async flush(): Promise<void> {
    if (this.disposed) return;
    if (this.inFlight) {
      this.queued = true;
      return;
    }
    const latest = this.latest;
    if (!latest) return;
    this.inFlight = true;
    const pending: PendingDocument = {
      schemaVersion: 1,
      ownerUid: this.options.ownerUid,
      documentId: this.options.documentId,
      version: this.version,
      body: {
        title: latest.document.title || "Untitled page",
        format: latest.format,
        document: writingBody(latest.document),
      },
      createdAt: new Date().toISOString(),
    };
    try {
      this.persistPending(pending);
      const saved = await this.options.client.update(
        this.options.documentId,
        latest.document,
        latest.format,
        this.version,
      );
      this.clearPending();
      this.version = saved.row_version;
      this.options.onSaved(saved.row_version);
      this.options.onState("saved", "Saved to your account");
    } catch (cause) {
      if (
        cause instanceof DocumentError &&
        cause.code === "revision_conflict"
      ) {
        this.options.onState(
          "conflict",
          "This page changed in another tab. Keep your words or open the saved version.",
        );
      } else if (
        cause instanceof DocumentError &&
        cause.code === "network_unavailable"
      ) {
        this.options.onState(
          "offline",
          "Offline — changes kept on this device",
        );
      } else if (
        cause instanceof DocumentError &&
        cause.code === "unauthenticated"
      ) {
        this.options.onState(
          "error",
          "Sign in again — changes kept on this device.",
        );
      } else if (
        cause instanceof DocumentError &&
        cause.code === "recovery_unavailable"
      ) {
        this.options.onState(
          "error",
          "Couldn’t keep a recovery copy, so nothing was sent.",
        );
      } else {
        this.options.onState("error", "Couldn’t save — retry");
      }
    } finally {
      this.inFlight = false;
      if (this.queued && !this.disposed) {
        this.queued = false;
        void this.flush();
      }
    }
  }

  /** Explicit choice after a conflict. Local words are written onto the latest version. */
  async resolve(choice: "mine" | "saved"): Promise<void> {
    const latest = this.latest;
    if (!latest) return;
    if (choice === "saved") {
      this.clearPending();
      this.options.onState("saved", "Using the saved page");
      return;
    }
    try {
      const current = await this.options.client.get(this.options.documentId);
      this.version = current.row_version;
      this.options.onState("saving", "Saving…");
      await this.flush();
    } catch {
      this.options.onState("error", "Couldn’t save — retry");
    }
  }

  retryPending(): boolean {
    try {
      const raw = this.options.storage?.getItem(
        key(this.options.ownerUid, this.options.documentId),
      );
      if (!raw) return false;
      const pending = JSON.parse(raw) as PendingDocument;
      if (
        pending.schemaVersion !== 1 ||
        pending.ownerUid !== this.options.ownerUid
      )
        return false;
      this.version = pending.version;
      return true;
    } catch {
      return false;
    }
  }

  dispose() {
    this.disposed = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}

export function hasPendingDocument(
  ownerUid: string,
  documentId: string,
  storage: StorageLike | null,
): boolean {
  try {
    return storage?.getItem(key(ownerUid, documentId)) != null;
  } catch {
    return false;
  }
}
