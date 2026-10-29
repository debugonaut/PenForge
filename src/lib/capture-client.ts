/** Typed transport for the real private capture slice. No request happens on import. */
import type { components } from "../../packages/contracts/src/generated";
import { createFoundationClient } from "../../packages/contracts/src/client";

export type CaptureAsset = components["schemas"]["AssetResponse"];
export type CaptureGrant = components["schemas"]["CapabilityResponse"];
export type CaptureJob = components["schemas"]["JobStatus"];
export type CaptureSample = components["schemas"]["SampleResponse"];
export type CaptureInit = components["schemas"]["UploadInit"];
export type CaptureSampleInput =
  | components["schemas"]["AssetSampleCreate"]
  | components["schemas"]["StrokeSampleCreate"];

export class CaptureError extends Error {
  constructor(
    readonly code: string,
    readonly status = 0,
  ) {
    super("The capture request could not be completed.");
    this.name = "CaptureError";
  }
}

const codes = new Set([
  "account_required",
  "email_verification_required",
  "terms_required",
  "policy_changed",
  "service_not_ready",

  "not_found",
  "unauthenticated",
  "invalid_token",
  "account_merged",
  "account_suspended",
  "account_deleting",
  "quota_unconfigured",
  "quota_exceeded",
  "upload_expired",
  "upload_mismatch",
  "object_missing",
  "unsupported_media_type",
  "validation_error",
  "template_mismatch",
  "idempotency_key_reuse",
  "idempotency_in_progress",
  "state_conflict",
  "dependency_unavailable",
  "capacity_exceeded",
  "rate_limited",
]);

export function createCaptureClient(
  baseUrl: string,
  token: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  createFoundationClient(baseUrl, fetcher);
  const origin = new URL(baseUrl);
  async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    key?: string,
    signal?: AbortSignal,
  ): Promise<T> {
    const accessToken = await token();
    if (!accessToken || /\s/.test(accessToken))
      throw new CaptureError("unauthenticated", 401);
    const response = await fetcher(new URL(path, origin), {
      method,
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
        const payload = await response.json();
        if (codes.has(payload?.error?.code)) code = payload.error.code;
      } catch {
        /* Never expose server bodies or capability URLs in errors. */
      }
      throw new CaptureError(code, response.status);
    }
    return response.json() as Promise<T>;
  }
  return {
    init: (
      projectId: string,
      body: CaptureInit,
      key: string,
      signal?: AbortSignal,
    ) =>
      request<components["schemas"]["UploadResponse"]>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/uploads`,
        "POST",
        body,
        key,
        signal,
      ),
    renew: (assetId: string, signal?: AbortSignal) =>
      request<components["schemas"]["UploadResponse"]>(
        `/api/v1/uploads/${encodeURIComponent(assetId)}/upload-url`,
        "POST",
        undefined,
        undefined,
        signal,
      ),
    complete: (assetId: string, signal?: AbortSignal) =>
      request<components["schemas"]["UploadComplete"]>(
        `/api/v1/uploads/${encodeURIComponent(assetId)}/complete`,
        "POST",
        undefined,
        undefined,
        signal,
      ),
    asset: (assetId: string, signal?: AbortSignal) =>
      request<CaptureAsset>(
        `/api/v1/assets/${encodeURIComponent(assetId)}`,
        "GET",
        undefined,
        undefined,
        signal,
      ),
    job: (jobId: string, signal?: AbortSignal) =>
      request<CaptureJob>(
        `/api/v1/jobs/${encodeURIComponent(jobId)}`,
        "GET",
        undefined,
        undefined,
        signal,
      ),
    sample: (
      projectId: string,
      body: CaptureSampleInput,
      key: string,
      signal?: AbortSignal,
    ) =>
      request<CaptureSample>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/samples`,
        "POST",
        body,
        key,
        signal,
      ),
    /** Owner-authorized original bytes, streamed within the admitted size and digest. */
    async preview(assetId: string, signal?: AbortSignal): Promise<Blob> {
      const asset = await request<CaptureAsset>(
        `/api/v1/assets/${encodeURIComponent(assetId)}`,
        "GET",
        undefined,
        undefined,
        signal,
      );
      const limit = 20 * 1024 * 1024;
      if (
        !Number.isSafeInteger(asset.size_bytes) ||
        asset.size_bytes <= 0 ||
        asset.size_bytes > limit ||
        !/^sha256:[a-f0-9]{64}$/.test(asset.sha256 ?? "") ||
        !["image/png", "image/jpeg", "image/webp"].includes(asset.mime_type)
      )
        throw new CaptureError("invalid_preview");
      const grant = await request<CaptureGrant>(
        `/api/v1/assets/${encodeURIComponent(assetId)}/download-url`,
        "GET",
        undefined,
        undefined,
        signal,
      );
      const url = new URL(grant.url, origin);
      if (
        url.origin !== origin.origin ||
        !url.pathname.startsWith("/api/v1/storage/objects/") ||
        grant.method !== "GET" ||
        url.username ||
        url.password
      )
        throw new CaptureError("invalid_capability");
      const accessToken = await token();
      if (!accessToken || /\s/.test(accessToken))
        throw new CaptureError("unauthenticated", 401);
      const response = await fetcher(url, {
        headers: { Authorization: `Bearer ${accessToken}` },
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
        referrerPolicy: "no-referrer",
        signal,
      });
      const type = response.headers.get("Content-Type")?.split(";")[0];
      if (!response.ok || type !== asset.mime_type || !response.body)
        throw new CaptureError("invalid_preview");
      const reader = response.body.getReader();
      const chunks: Uint8Array<ArrayBuffer>[] = [];
      let size = 0;
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > asset.size_bytes || size > limit)
            throw new CaptureError("invalid_preview");
          chunks.push(new Uint8Array(value));
        }
      } finally {
        await reader.cancel();
        reader.releaseLock();
      }
      if (size !== asset.size_bytes) throw new CaptureError("invalid_preview");
      const blob = new Blob(chunks, { type: asset.mime_type });
      const digest = Array.from(
        new Uint8Array(
          await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()),
        ),
        (value) => value.toString(16).padStart(2, "0"),
      ).join("");
      if (`sha256:${digest}` !== asset.sha256)
        throw new CaptureError("invalid_preview");
      return blob;
    },
    async upload(
      grant: CaptureGrant,
      bytes: Blob,
      onProgress: (sent: number, total: number) => void,
      signal?: AbortSignal,
    ): Promise<void> {
      const url = new URL(grant.url, origin);
      if (
        url.origin !== origin.origin ||
        !url.pathname.startsWith("/api/v1/storage/objects/") ||
        url.username ||
        url.password ||
        grant.method !== "PUT"
      )
        throw new CaptureError("invalid_capability");
      const accessToken = await token();
      if (!accessToken || /\s/.test(accessToken))
        throw new CaptureError("unauthenticated", 401);
      return new Promise((resolve, reject) => {
        if (signal?.aborted) {
          reject(new DOMException("Aborted", "AbortError"));
          return;
        }
        const xhr = new XMLHttpRequest();
        const abort = () => xhr.abort();
        const finish = () => signal?.removeEventListener("abort", abort);
        xhr.open("PUT", url.href);
        xhr.withCredentials = false;
        for (const [name, value] of Object.entries(grant.headers)) {
          if (name.toLowerCase() !== "authorization")
            xhr.setRequestHeader(name, value);
        }
        xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) onProgress(event.loaded, event.total);
        };
        xhr.onload = () => {
          finish();
          if (xhr.status >= 200 && xhr.status < 300) {
            onProgress(bytes.size, bytes.size);
            resolve();
          } else
            reject(
              new CaptureError(
                xhr.status === 413 ? "upload_mismatch" : "upload_failed",
                xhr.status,
              ),
            );
        };
        xhr.onerror = () => {
          finish();
          reject(new CaptureError("network_unavailable"));
        };
        xhr.onabort = () => {
          finish();
          reject(new DOMException("Aborted", "AbortError"));
        };
        signal?.addEventListener("abort", abort, { once: true });
        xhr.send(bytes);
      });
    },
  };
}

export type CaptureClient = ReturnType<typeof createCaptureClient>;
