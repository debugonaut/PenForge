/** Typed fetch transport over the implemented OpenAPI surface only. No automatic calls. */
import type { components, paths } from "./generated";

type GetPath = {
  [Path in keyof paths]: paths[Path] extends { get: object } ? Path : never;
}[keyof paths];
type Success<Path extends GetPath> = paths[Path] extends {
  get: { responses: { 200: { content: { "application/json": infer Body } } } };
}
  ? Body
  : never;

export class ApiResponseError extends Error {
  constructor(
    public readonly status: number,
    public readonly requestId: string | null,
  ) {
    super(`API request failed (${status}).`);
    this.name = "ApiResponseError";
  }
}

export function createFoundationClient(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
) {
  const origin = new URL(baseUrl);
  if (
    !["http:", "https:"].includes(origin.protocol) ||
    origin.username ||
    origin.password ||
    origin.search ||
    origin.hash ||
    origin.pathname !== "/"
  )
    throw new Error("invalid_api_origin");
  return {
    async get<Path extends GetPath>(
      path: Path,
      signal?: AbortSignal,
    ): Promise<Success<Path>> {
      const response = await fetcher(new URL(path, origin), {
        method: "GET",
        headers: { Accept: "application/json" },
        signal,
        credentials: "omit",
        redirect: "error",
      });
      if (!response.ok)
        throw new ApiResponseError(
          response.status,
          response.headers.get("X-Request-ID"),
        );
      return response.json() as Promise<Success<Path>>;
    },
  };
}

/** Content-free, allowlisted API failures for authenticated account operations. */
export class IdentityResponseError extends ApiResponseError {
  constructor(
    status: number,
    requestId: string | null,
    public readonly code: string,
  ) {
    super(status, requestId);
    this.name = "IdentityResponseError";
  }
}

export class ProjectsResponseError extends ApiResponseError {
  constructor(
    status: number,
    requestId: string | null,
    public readonly code: string,
    public readonly context: Record<string, unknown> | null = null,
  ) {
    super(status, requestId);
    this.name = "ProjectsResponseError";
  }
}

const identityErrorCodes = new Set([
  "unauthenticated",
  "invalid_token",
  "account_suspended",
  "account_deleting",
  "account_merged",
  "already_claimed",
  "state_conflict",
  "rate_limited",
  "dependency_unavailable",
  "stale_guest_token",
  "guest_auth_too_old",
]);

export function createIdentityClient(
  baseUrl: string,
  tokenProvider: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  // Reuse the same strict URL validation as the foundation transport.
  createFoundationClient(baseUrl, fetcher);
  const origin = new URL(baseUrl);
  async function request<Body>(
    path: string,
    guestIdToken?: string,
  ): Promise<Body> {
    const token = await tokenProvider();
    if (!token || /[\s\r\n]/.test(token))
      throw new IdentityResponseError(401, null, "unauthenticated");
    const response = await fetcher(new URL(path, origin), {
      method: guestIdToken === undefined ? "GET" : "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        ...(guestIdToken === undefined
          ? {}
          : { "Content-Type": "application/json" }),
      },
      ...(guestIdToken === undefined
        ? {}
        : { body: JSON.stringify({ guest_id_token: guestIdToken }) }),
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
    if (!response.ok) {
      let code = "request_failed";
      try {
        const payload: unknown = await response.json();
        if (
          payload &&
          typeof payload === "object" &&
          "error" in payload &&
          payload.error &&
          typeof payload.error === "object" &&
          "code" in payload.error &&
          typeof payload.error.code === "string" &&
          identityErrorCodes.has(payload.error.code)
        )
          code = payload.error.code;
      } catch {
        /* A non-JSON body is never shown or logged. */
      }
      throw new IdentityResponseError(
        response.status,
        response.headers.get("X-Request-ID"),
        code,
      );
    }
    return response.json() as Promise<Body>;
  }
  return {
    me: () =>
      request<
        paths["/api/v1/me"]["get"]["responses"][200]["content"]["application/json"]
      >("/api/v1/me"),
    claim: (guestIdToken: string) =>
      request<
        paths["/api/v1/identity/claim"]["post"]["responses"][200]["content"]["application/json"]
      >("/api/v1/identity/claim", guestIdToken),
  };
}

type ProjectWire = components["schemas"]["ProjectResponse"];
type ProjectPageWire = components["schemas"]["ProjectPage"];
type ProjectCreateWire = components["schemas"]["ProjectCreate"];
type ProjectPatchWire = components["schemas"]["ProjectPatch"];

const projectErrorCodes = new Set([
  "account_required",
  "email_verification_required",
  "terms_required",
  "policy_changed",
  "service_not_ready",

  "unauthenticated",
  "invalid_token",
  "account_suspended",
  "account_deleting",
  "account_merged",
  "guest_limit_reached",
  "not_found",
  "state_conflict",
  "idempotency_in_progress",
  "idempotency_key_reuse",
  "precondition_required",
  "revision_conflict",
  "capability_unavailable",
  "rate_limited",
  "dependency_unavailable",
  "validation_error",
]);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Authenticated project metadata transport; it never serializes local drafts. */
export function createProjectsClient(
  baseUrl: string,
  tokenProvider: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  createFoundationClient(baseUrl, fetcher);
  const origin = new URL(baseUrl);

  async function request<Body>(
    path: string,
    method: "GET" | "POST" | "PATCH" | "DELETE",
    options: {
      body?: unknown;
      idempotencyKey?: string;
      ifMatch?: string;
      signal?: AbortSignal;
    } = {},
  ): Promise<{ body: Body; response: Response }> {
    const token = await tokenProvider();
    if (!token || /[\s\r\n]/.test(token))
      throw new ProjectsResponseError(401, null, "unauthenticated");
    const response = await fetcher(new URL(path, origin), {
      method,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        ...(options.body === undefined
          ? {}
          : { "Content-Type": "application/json" }),
        ...(options.idempotencyKey === undefined
          ? {}
          : { "Idempotency-Key": options.idempotencyKey }),
        ...(options.ifMatch === undefined
          ? {}
          : { "If-Match": options.ifMatch }),
      },
      ...(options.body === undefined
        ? {}
        : { body: JSON.stringify(options.body) }),
      signal: options.signal,
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
    if (!response.ok) {
      let code = "request_failed";
      let context: Record<string, unknown> | null = null;
      try {
        const payload: unknown = await response.json();
        if (record(payload) && record(payload.error)) {
          const error = payload.error;
          if (
            typeof error.code === "string" &&
            projectErrorCodes.has(error.code)
          )
            code = error.code;
          if (record(error.context)) context = error.context;
        }
      } catch {
        /* A non-JSON body is never shown or logged. */
      }
      throw new ProjectsResponseError(
        response.status,
        response.headers.get("X-Request-ID"),
        code,
        context,
      );
    }
    if (response.status === 204) return { body: undefined as Body, response };
    return { body: (await response.json()) as Body, response };
  }

  function etag(project: ProjectWire, response: Response): string {
    const value = response.headers.get("ETag");
    if (value !== `"v${project.row_version}"`)
      throw new ProjectsResponseError(
        502,
        response.headers.get("X-Request-ID"),
        "invalid_etag",
      );
    return value;
  }

  return {
    async create(
      body: ProjectCreateWire,
      key: string,
      signal?: AbortSignal,
    ): Promise<{ project: ProjectWire; etag: string; replayed: boolean }> {
      const result = await request<ProjectWire>("/api/v1/projects", "POST", {
        body,
        idempotencyKey: key,
        signal,
      });
      return {
        project: result.body,
        etag: etag(result.body, result.response),
        replayed:
          result.response.headers.get("Idempotency-Replayed") === "true",
      };
    },
    async list(
      options: { limit?: number; cursor?: string; signal?: AbortSignal } = {},
    ): Promise<ProjectPageWire> {
      const query = new URLSearchParams();
      if (options.limit !== undefined)
        query.set("limit", String(options.limit));
      if (options.cursor) query.set("cursor", options.cursor);
      const suffix = query.size ? `?${query.toString()}` : "";
      return (
        await request<ProjectPageWire>(`/api/v1/projects${suffix}`, "GET", {
          signal: options.signal,
        })
      ).body;
    },
    async get(id: string, signal?: AbortSignal) {
      const result = await request<ProjectWire>(
        `/api/v1/projects/${encodeURIComponent(id)}`,
        "GET",
        { signal },
      );
      return { project: result.body, etag: etag(result.body, result.response) };
    },
    async update(
      id: string,
      body: ProjectPatchWire,
      ifMatch: string,
      signal?: AbortSignal,
    ) {
      const result = await request<ProjectWire>(
        `/api/v1/projects/${encodeURIComponent(id)}`,
        "PATCH",
        { body, ifMatch, signal },
      );
      return { project: result.body, etag: etag(result.body, result.response) };
    },
    async requestDeletion(id: string, ifMatch: string): Promise<void> {
      await request<void>(
        `/api/v1/projects/${encodeURIComponent(id)}`,
        "DELETE",
        {
          ifMatch,
        },
      );
    },
  };
}
