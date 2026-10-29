/** The private review adapter never imports or mutates the local demo project service. */
import type { components } from "../../packages/contracts/src/generated";
import type { CanonicalOutline } from "./api-boundary";
import { createFoundationClient } from "../../packages/contracts/src/client";

export type TemplateReference = components["schemas"]["TemplateReference"];
export type TemplateChoice = components["schemas"]["TemplateItem"];
export type ReviewSample = components["schemas"]["SampleResponse"];
export type AnalysisAssignment = components["schemas"]["CaptureAssignment"];
export type ImageRegionAssignment =
  components["schemas"]["ImageRegionAssignment"];
export type AnalysisJob = components["schemas"]["AnalysisJobResponse"];
export type ReviewRevision = components["schemas"]["RevisionResponse"];
export type ReviewGlyph = components["schemas"]["GlyphResponse"];
export type ReviewCandidate = components["schemas"]["CandidateResponse"];
export type ReviewCoverage = components["schemas"]["CoverageResponse"];
export type FollowUp = components["schemas"]["PromptResponse"];
export type EvidenceArtifact = components["schemas"]["ChildArtifact"];
export type MeasuredStyle = components["schemas"]["StyleProfileResponse"];
export type ProcessingCapability =
  components["schemas"]["ProcessingCapabilityResponse"];

export class ExtractionError extends Error {
  constructor(
    readonly code: string,
    readonly status = 0,
  ) {
    super("Private review request failed.");
    this.name = "ExtractionError";
  }
}
const errorCodes = new Set([
  "account_required",
  "email_verification_required",
  "terms_required",
  "policy_changed",
  "service_not_ready",

  "not_found",
  "unauthenticated",
  "invalid_token",
  "account_merged",
  "account_deleting",
  "account_suspended",
  "revision_conflict",
  "precondition_required",
  "validation_error",
  "template_mismatch",
  "idempotency_key_reuse",
  "idempotency_in_progress",
  "state_conflict",
  "dependency_unavailable",
  "capacity_exceeded",
  "quota_unconfigured",
  "quota_exceeded",
]);

export function createExtractionClient(
  baseUrl: string,
  token: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  createFoundationClient(baseUrl, fetcher);
  const origin = new URL(baseUrl);
  const project = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`;
  async function request<T>(
    path: string,
    options: {
      method?: string;
      body?: unknown;
      key?: string;
      etag?: string;
      signal?: AbortSignal;
    } = {},
  ): Promise<{ data: T; etag: string | null }> {
    const accessToken = await token();
    if (!accessToken || /\s/.test(accessToken))
      throw new ExtractionError("unauthenticated", 401);
    const response = await fetcher(new URL(path, origin), {
      method: options.method ?? "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
        ...(options.body === undefined
          ? {}
          : { "Content-Type": "application/json" }),
        ...(options.key ? { "Idempotency-Key": options.key } : {}),
        ...(options.etag ? { "If-Match": options.etag } : {}),
      },
      ...(options.body === undefined
        ? {}
        : { body: JSON.stringify(options.body) }),
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal: options.signal,
    });
    if (!response.ok) {
      let code = "request_failed";
      try {
        const value = await response.json();
        if (errorCodes.has(value?.error?.code)) code = value.error.code;
      } catch {
        /* Private error bodies are never rendered. */
      }
      throw new ExtractionError(code, response.status);
    }
    return {
      data: (await response.json()) as T,
      etag: response.headers.get("ETag"),
    };
  }
  return {
    templates: async (signal?: AbortSignal, script: "Latn" | "Deva" = "Latn") =>
      (
        await request<{ items: TemplateChoice[] }>(
          script === "Latn"
            ? "/api/v1/templates"
            : "/api/v1/templates?script_code=Deva",
          {
            signal,
          },
        )
      ).data.items,
    samples: async (id: string, signal?: AbortSignal) =>
      (
        await request<{ items: ReviewSample[] }>(`${project(id)}/samples`, {
          signal,
        })
      ).data.items,
    analyze: async (
      id: string,
      sampleIds: string[],
      assignments: AnalysisAssignment[],
      key: string,
      signal?: AbortSignal,
      drawingMode?: "fixed_round_v1",
    ) =>
      (
        await request<AnalysisJob>(`${project(id)}/jobs`, {
          method: "POST",
          body: {
            job_type: "analyze_samples",
            sample_ids: sampleIds,
            assignments,
            ...(drawingMode ? { drawing_mode: drawingMode } : {}),
          },
          key,
          signal,
        })
      ).data,
    analyzeImageRegions: async (
      id: string,
      sampleId: string,
      regions: ImageRegionAssignment[],
      key: string,
      signal?: AbortSignal,
    ) =>
      (
        await request<AnalysisJob>(`${project(id)}/jobs`, {
          method: "POST",
          body: {
            job_type: "analyze_samples",
            sample_ids: [sampleId],
            image_mode: "manual_regions_v1",
            image_regions: regions,
          },
          key,
          signal,
        })
      ).data,
    job: async (id: string, jobId: string, signal?: AbortSignal) =>
      (
        await request<AnalysisJob>(
          `${project(id)}/analysis-jobs/${encodeURIComponent(jobId)}`,
          { signal },
        )
      ).data,
    glyphs: async (id: string, signal?: AbortSignal) => {
      const items: ReviewGlyph[] = [];
      const seen = new Set<string>();
      let cursor: string | null = null;
      do {
        const page: { items: ReviewGlyph[]; next_cursor: string | null } = (
          await request<{ items: ReviewGlyph[]; next_cursor: string | null }>(
            `${project(id)}/glyphs?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
            { signal },
          )
        ).data;
        items.push(...page.items);
        cursor = page.next_cursor;
        if (cursor && seen.has(cursor))
          throw new ExtractionError("invalid_pagination");
        if (cursor) seen.add(cursor);
      } while (cursor);
      return items;
    },
    glyph: (id: string, glyphId: string, signal?: AbortSignal) =>
      request<ReviewGlyph>(
        `${project(id)}/glyphs/${encodeURIComponent(glyphId)}`,
        { signal },
      ),
    candidates: async (
      id: string,
      glyphId: string,
      signal?: AbortSignal,
      cursor?: string,
    ) =>
      (
        await request<{
          items: ReviewCandidate[];
          next_cursor: string | null;
        }>(
          `${project(id)}/glyphs/${encodeURIComponent(glyphId)}/candidates${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
          {
            signal,
          },
        )
      ).data,
    select: (
      id: string,
      glyphId: string,
      body: { candidate_id: string; base_revision_id: string | null },
      etag: string,
      key: string,
      signal?: AbortSignal,
    ) =>
      request<ReviewGlyph>(
        `${project(id)}/glyphs/${encodeURIComponent(glyphId)}/select-candidate`,
        { method: "POST", body, etag, key, signal },
      ),
    patch: (
      id: string,
      glyphId: string,
      body: {
        base_revision_id: string | null;
        action: "edit" | "approve" | "reject" | "rollback";
        outline?: CanonicalOutline;
        target_revision_id?: string;
      },
      etag: string,
      signal?: AbortSignal,
    ) =>
      request<ReviewGlyph>(
        `${project(id)}/glyphs/${encodeURIComponent(glyphId)}`,
        { method: "PATCH", body, etag, signal },
      ),
    coverage: async (id: string, signal?: AbortSignal) =>
      (await request<ReviewCoverage>(`${project(id)}/coverage`, { signal }))
        .data,
    followUp: async (id: string, signal?: AbortSignal) =>
      (await request<FollowUp>(`${project(id)}/calibration/next`, { signal }))
        .data,
    styleProfile: async (id: string, signal?: AbortSignal) =>
      (await request<MeasuredStyle>(`${project(id)}/style-profile`, { signal }))
        .data,
    measureStyle: async (
      id: string,
      snapshotId: string,
      key: string,
      signal?: AbortSignal,
    ) =>
      (
        await request<MeasuredStyle>(`${project(id)}/style-profile`, {
          method: "POST",
          body: { snapshot_id: snapshotId },
          key,
          signal,
        })
      ).data,
    capabilities: async (id: string, signal?: AbortSignal) =>
      (
        await request<{ items: ProcessingCapability[] }>(
          `${project(id)}/capabilities`,
          { signal },
        )
      ).data.items,
    addAlternate: (
      id: string,
      glyphId: string,
      sourceRevisionId: string,
      baseRevisionId: string | null,
      etag: string,
      key: string,
      signal?: AbortSignal,
    ) =>
      request<ReviewGlyph>(
        `${project(id)}/glyphs/${encodeURIComponent(glyphId)}/alternates`,
        {
          method: "POST",
          body: {
            source_revision_id: sourceRevisionId,
            base_revision_id: baseRevisionId,
          },
          etag,
          key,
          signal,
        },
      ),
    enableAlternate: (
      id: string,
      glyphId: string,
      enabled: boolean,
      etag: string,
      signal?: AbortSignal,
    ) =>
      request<ReviewGlyph>(
        `${project(id)}/glyphs/${encodeURIComponent(glyphId)}/alternate-enabled`,
        {
          method: "PATCH",
          body: { enabled },
          etag,
          signal,
        },
      ),
    snapshot: async (
      id: string,
      label: string,
      key: string,
      signal?: AbortSignal,
    ) =>
      (
        await request<{ id: string; manifest_sha256: string }>(
          `${project(id)}/snapshots`,
          { method: "POST", body: { kind: "milestone", label }, key, signal },
        )
      ).data,
    space: async (
      id: string,
      advanceWidth: number,
      key: string,
      signal?: AbortSignal,
    ) =>
      (
        await request<ReviewGlyph>(`${project(id)}/glyphs/space`, {
          method: "POST",
          body: { advance_width: advanceWidth },
          key,
          signal,
        })
      ).data,
    async evidence(ref: EvidenceArtifact, signal?: AbortSignal): Promise<Blob> {
      if (
        ref.media_type !== "image/png" ||
        ref.size_bytes <= 0 ||
        ref.size_bytes > 4 * 1024 * 1024
      )
        throw new ExtractionError("invalid_evidence");
      const grant = (
        await request<components["schemas"]["CapabilityResponse"]>(
          `/api/v1/assets/${encodeURIComponent(ref.artifact_id)}/download-url`,
          { signal },
        )
      ).data;
      const url = new URL(grant.url, origin);
      if (
        url.origin !== origin.origin ||
        !url.pathname.startsWith("/api/v1/storage/objects/") ||
        grant.method !== "GET" ||
        url.username ||
        url.password
      )
        throw new ExtractionError("invalid_capability");
      const accessToken = await token();
      if (!accessToken || /\s/.test(accessToken))
        throw new ExtractionError("unauthenticated", 401);
      const response = await fetcher(url, {
        headers: { Authorization: `Bearer ${accessToken}` },
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
        referrerPolicy: "no-referrer",
        signal,
      });
      if (
        !response.ok ||
        response.headers.get("Content-Type")?.split(";")[0] !== "image/png"
      )
        throw new ExtractionError("invalid_evidence");
      const bytes = await response.arrayBuffer();
      const digest = Array.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
        (b) => b.toString(16).padStart(2, "0"),
      ).join("");
      if (
        bytes.byteLength !== ref.size_bytes ||
        `sha256:${digest}` !== ref.sha256
      )
        throw new ExtractionError("invalid_evidence");
      return new Blob([bytes], { type: "image/png" });
    },
    publicTemplateUrl(value: string): string {
      const url = new URL(value, origin);
      if (
        url.origin !== origin.origin ||
        !url.pathname.startsWith("/api/v1/templates/") ||
        url.username ||
        url.password
      )
        throw new ExtractionError("invalid_template_url");
      return url.href;
    },
  };
}
export type ExtractionClient = ReturnType<typeof createExtractionClient>;

export function extractionErrorText(error: unknown): string {
  if (error instanceof ExtractionError && error.code === "revision_conflict")
    return "This letter changed in another session. Your edit stays here. Reload the current letter before choosing or applying it again.";
  if (error instanceof ExtractionError && error.code === "template_mismatch")
    return "The page does not match the pinned template. Download its exact version and capture the full page, including markers.";
  if (error instanceof ExtractionError && error.code === "state_conflict")
    return "This action no longer matches the current review state. Reload the letter and inspect its history.";
  return "Private review could not be confirmed. Keep this page open and retry or reload the current server state.";
}
