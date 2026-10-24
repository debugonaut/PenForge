/** Explicit private actions only; this module never reads local writing or drafts. */
import type { components } from "../../packages/contracts/src/generated";
import { createFoundationClient } from "../../packages/contracts/src/client";

export type Consent = components["schemas"]["ConsentView"];
export type Deletion = components["schemas"]["DeletionView"];
export type DeletionInput = components["schemas"]["DeletionCreate"];

export function createPrivacyClient(
  baseUrl: string,
  token: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  createFoundationClient(baseUrl, fetcher);
  async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    key?: string,
    receipt?: string,
    signal?: AbortSignal,
  ): Promise<T> {
    const access = receipt ? null : await token();
    if (!receipt && (!access || /\s/.test(access)))
      throw new Error("unauthenticated");
    const response = await fetcher(new URL(path, baseUrl), {
      method,
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal,
      headers: {
        Accept: "application/json",
        ...(receipt
          ? { "X-Deletion-Receipt": receipt }
          : { Authorization: `Bearer ${access}` }),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(key ? { "Idempotency-Key": key } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const value = await response.json();
    if (!response.ok) {
      const allowed = new Set([
        "recent_auth_required",
        "consent_required",
        "consent_policy_conflict",
        "consent_ineligible",
        "not_found",
        "account_deleting",
        "revision_conflict",
        "dependency_unavailable",
        "capability_unavailable",
        "invalid_token",
        "idempotency_key_reuse",
      ]);
      throw new Error(
        allowed.has(value?.error?.code) ? value.error.code : "request_failed",
      );
    }
    return value as T;
  }
  return {
    consent: (signal?: AbortSignal) =>
      request<Consent>(
        "/api/v1/consent",
        "GET",
        undefined,
        undefined,
        undefined,
        signal,
      ),
    setConsent: (granted: boolean, policy: string) =>
      request<Consent>("/api/v1/consent", "PUT", {
        purpose: "model_training",
        granted,
        policy_version: policy,
      }),
    delete: (body: DeletionInput, key: string) =>
      request<Deletion>("/api/v1/privacy/deletion-requests", "POST", body, key),
    progress: (id: string, receipt?: string, signal?: AbortSignal) =>
      request<Deletion>(
        `/api/v1/privacy/deletion-requests/${encodeURIComponent(id)}`,
        "GET",
        undefined,
        undefined,
        receipt,
        signal,
      ),
    contribute: (
      sample: string,
      consent: string,
      policy: string,
      key: string,
    ) =>
      request<components["schemas"]["ContributionView"]>(
        "/api/v1/privacy/contributions",
        "POST",
        {
          sample_id: sample,
          consent_id: consent,
          policy_version: policy,
          confirmed: true,
        },
        key,
      ),
    projects: () =>
      request<components["schemas"]["ProjectPage"]>(
        "/api/v1/projects?limit=100",
      ),
    samples: (project: string) =>
      request<components["schemas"]["SamplePage"]>(
        `/api/v1/projects/${encodeURIComponent(project)}/samples`,
      ),
  };
}
