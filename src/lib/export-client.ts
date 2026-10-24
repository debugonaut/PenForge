/** Explicit private exports; fonts are verified before FontFace or document export. */
import type { components } from "../../packages/contracts/src/generated";
import { createFoundationClient } from "../../packages/contracts/src/client";

export type PrivateExport = components["schemas"]["ExportResponse"];
export type ExportRequest = components["schemas"]["ExportCreate"];
export type ExportReceipt = components["schemas"]["ExportCreated"];

export class ExportError extends Error {
  constructor(
    readonly code: string,
    readonly status = 0,
  ) {
    super("Private font export could not be confirmed.");
  }
}

export function createExportClient(
  baseUrl: string,
  token: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  createFoundationClient(baseUrl, fetcher);
  const origin = new URL(baseUrl);
  async function request<T>(
    path: string,
    body?: unknown,
    key?: string,
    signal?: AbortSignal,
  ): Promise<T> {
    const accessToken = await token();
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (!accessToken || /\s/.test(accessToken))
      throw new ExportError("unauthenticated", 401);
    const response = await fetcher(new URL(path, origin), {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(key ? { "Idempotency-Key": key } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal,
    });
    if (!response.ok) {
      let code = "request_failed";
      try {
        const value = await response.json();
        if (
          [
            "not_found",
            "state_conflict",
            "quota_unconfigured",
            "quota_exceeded",
            "unauthenticated",
            "account_merged",
            "account_required",
            "email_verification_required",
            "terms_required",
            "policy_changed",
            "service_not_ready",
          ].includes(value?.error?.code)
        )
          code = value.error.code;
      } catch {
        /* Private response bodies stay out of UI and logs. */
      }
      throw new ExportError(code, response.status);
    }
    return response.json() as Promise<T>;
  }
  async function get(id: string, signal?: AbortSignal) {
    return request<PrivateExport>(
      `/api/v1/exports/${encodeURIComponent(id)}`,
      undefined,
      undefined,
      signal,
    );
  }
  return {
    create: (
      projectId: string,
      body: ExportRequest,
      key: string,
      signal?: AbortSignal,
    ) =>
      request<ExportReceipt>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/exports`,
        body,
        key,
        signal,
      ),
    get,
    async font(
      id: string,
      signal?: AbortSignal,
    ): Promise<{ blob: Blob; metadata: PrivateExport }> {
      const metadata = await get(id, signal);
      if (
        metadata.status !== "ready" ||
        !metadata.sha256 ||
        !metadata.size_bytes ||
        metadata.size_bytes > 2 * 1024 * 1024
      )
        throw new ExportError("not_ready");
      // A capability is fetched afresh. One expired/revoked link is safely renewed.
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const grant = await request<components["schemas"]["ExportDownload"]>(
          `/api/v1/exports/${encodeURIComponent(id)}/download-url`,
          undefined,
          undefined,
          signal,
        );
        const url = new URL(grant.url, origin);
        if (
          url.origin !== origin.origin ||
          !url.pathname.startsWith("/api/v1/storage/objects/") ||
          url.username ||
          url.password ||
          grant.method !== "GET"
        )
          throw new ExportError("invalid_capability");
        const accessToken = await token();
        if (!accessToken || /\s/.test(accessToken))
          throw new ExportError("unauthenticated", 401);
        const response = await fetcher(url, {
          headers: { Authorization: `Bearer ${accessToken}` },
          credentials: "omit",
          redirect: "error",
          cache: "no-store",
          referrerPolicy: "no-referrer",
          signal,
        });
        if ([401, 403, 404, 410].includes(response.status) && attempt === 0)
          continue;
        if (
          !response.ok ||
          response.headers.get("Content-Type")?.split(";")[0] !==
            `font/${metadata.format}`
        )
          throw new ExportError("download_failed");
        const data = await response.arrayBuffer();
        const hash = Array.from(
          new Uint8Array(await crypto.subtle.digest("SHA-256", data)),
          (b) => b.toString(16).padStart(2, "0"),
        ).join("");
        if (
          data.byteLength !== metadata.size_bytes ||
          `sha256:${hash}` !== metadata.sha256
        )
          throw new ExportError("font_hash_mismatch");
        return {
          blob: new Blob([data], { type: `font/${metadata.format}` }),
          metadata,
        };
      }
      throw new ExportError("download_expired");
    },
  };
}
export type ExportClient = ReturnType<typeof createExportClient>;

export function exportErrorText(cause: unknown): string {
  if (
    cause instanceof ExportError &&
    [
      "terms_required",
      "account_required",
      "email_verification_required",
    ].includes(cause.code)
  )
    return "Open Your account to finish setup, then retry. Your approved font is preserved.";
  if (cause instanceof ExportError && cause.code === "service_not_ready")
    return "The hosted service is not ready for public use yet.";
  if (cause instanceof ExportError && cause.code === "quota_unconfigured")
    return "Local export budgets are not configured. Configure storage, export and compute allowances before building.";
  if (cause instanceof ExportError && cause.code === "quota_exceeded")
    return "This build exceeds a configured local allowance. Your approved snapshot is preserved.";
  if (cause instanceof ExportError && cause.code === "not_found")
    return "This private export is unavailable to the current account. Connect its owner account and try again.";
  return "The private export could not be confirmed. Retry the saved request or refresh its status; your approved shapes are preserved.";
}
