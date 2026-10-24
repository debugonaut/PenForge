import { createFoundationClient } from "../../packages/contracts/src/client";

export type AccountRequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  key?: string;
  ifMatch?: string;
  signal?: AbortSignal;
};

type ErrorPolicy<Code extends string> = {
  error: new (code: Code, status?: number, currentVersion?: number) => Error;
  codes: ReadonlySet<string>;
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Domain clients keep their mappings, error allowlists and empty-response rules. */
export function createAccountRequest<Code extends string>(
  baseUrl: string,
  token: () => Promise<string>,
  { error: ErrorType, codes }: ErrorPolicy<Code>,
  fetcher: typeof fetch = fetch,
) {
  createFoundationClient(baseUrl, fetcher);
  const origin = new URL(baseUrl);

  return async function request(
    path: string,
    options: AccountRequestOptions = {},
  ): Promise<Response> {
    const accessToken = await token();
    if (!accessToken || /\s/.test(accessToken))
      throw new ErrorType("unauthenticated" as Code, 401);
    let response: Response;
    try {
      response = await fetcher(new URL(path, origin), {
        method: options.method ?? "GET",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${accessToken}`,
          ...(options.body === undefined
            ? {}
            : { "Content-Type": "application/json" }),
          ...(options.key ? { "Idempotency-Key": options.key } : {}),
          ...(options.ifMatch ? { "If-Match": options.ifMatch } : {}),
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
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError")
        throw cause;
      throw new ErrorType("network_unavailable" as Code);
    }
    if (response.ok) return response;

    let code = "dependency_unavailable";
    let currentVersion: number | undefined;
    try {
      const payload: unknown = await response.json();
      if (record(payload) && record(payload.error)) {
        if (
          typeof payload.error.code === "string" &&
          codes.has(payload.error.code)
        )
          code = payload.error.code;
        if (
          record(payload.error.context) &&
          typeof payload.error.context.current_row_version === "number"
        )
          currentVersion = payload.error.context.current_row_version;
      }
    } catch {
      /* Private response bodies never become error messages or logs. */
    }
    if (["rate_limited", "invalid_token", "account_merged"].includes(code))
      code = "dependency_unavailable";
    throw new ErrorType(code as Code, response.status, currentVersion);
  };
}
