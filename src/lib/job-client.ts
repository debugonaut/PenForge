/** Owner-token transport; durable cursors never carry credentials or source content. */
import type { components } from "../../packages/contracts/src/generated";
export type JobStatus = components["schemas"]["JobStatus"];
export const terminalJob = (state: string) =>
  ["succeeded", "failed", "canceled"].includes(state);

export class JobAccessError extends Error {
  constructor(readonly status: number) {
    super("Job access could not be confirmed.");
  }
}
export function jobDelay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted)
      return reject(new DOMException("Aborted", "AbortError"));
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}
export function createJobClient(
  baseUrl: string,
  token: () => Promise<string>,
  fetcher: typeof fetch = fetch,
  wait = jobDelay,
) {
  const origin = new URL(baseUrl);
  if (
    !["http:", "https:"].includes(origin.protocol) ||
    origin.username ||
    origin.password ||
    origin.pathname !== "/" ||
    origin.search ||
    origin.hash
  )
    throw new Error("invalid_api_origin");
  async function request(
    id: string,
    suffix: string,
    signal: AbortSignal,
    headers: Record<string, string> = {},
    method = "GET",
  ) {
    const access = await token();
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    if (!access || /\s/.test(access)) throw new JobAccessError(401);
    const response = await fetcher(
      new URL(`/api/v1/jobs/${encodeURIComponent(id)}${suffix}`, origin),
      {
        method,
        headers: { Authorization: `Bearer ${access}`, ...headers },
        signal,
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
        referrerPolicy: "no-referrer",
      },
    );
    if (!response.ok && response.status !== 304)
      throw new JobAccessError(response.status);
    return response;
  }
  const validate = (id: string, value: JobStatus) => {
    if (
      value.job_id !== id ||
      ![
        "queued",
        "running",
        "cancel_requested",
        "succeeded",
        "failed",
        "canceled",
      ].includes(value.state) ||
      value.eta_seconds !== null ||
      value.quality_policy !== "accuracy_first" ||
      !Number.isSafeInteger(value.event_id) ||
      !Number.isSafeInteger(value.progress?.completed_units) ||
      value.progress.completed_units < 0
    )
      throw new Error("invalid_job_status");
    return value;
  };
  return {
    async cancel(id: string, signal: AbortSignal) {
      return validate(
        id,
        await (
          await request(
            id,
            "/cancel",
            signal,
            { Accept: "application/json" },
            "POST",
          )
        ).json(),
      );
    },
    async watch(
      id: string,
      onStatus: (value: JobStatus) => void,
      signal: AbortSignal,
      onTransport?: (kind: "live" | "polling" | "reconnecting") => void,
    ) {
      let cursor = 0,
        etag = "",
        latest: JobStatus | null = null,
        streamFailures = 0,
        unchanged = 0;
      const accept = (value: JobStatus) => {
        validate(id, value);
        if (value.event_id! > cursor || !latest) {
          unchanged = 0;
          cursor = value.event_id!;
          latest = value;
          onStatus(value);
        } else unchanged += 1;
        return terminalJob(value.state);
      };
      // Poll once first: reload/reconnect never depends on an open stream.
      while (!signal.aborted) {
        const response = await request(id, "", signal, {
          Accept: "application/json",
          ...(etag ? { "If-None-Match": etag } : {}),
        });
        etag = response.headers.get("ETag") ?? etag;
        if (response.status !== 304 && accept(await response.json()))
          return latest!;
        if (streamFailures < 2) {
          const connection = new AbortController();
          const abort = () => connection.abort();
          signal.addEventListener("abort", abort, { once: true });
          let watchdog: ReturnType<typeof setTimeout> | undefined;
          try {
            const stream = await request(id, "/events", connection.signal, {
              Accept: "text/event-stream",
              ...(cursor ? { "Last-Event-ID": String(cursor) } : {}),
            });
            if (
              !stream.headers
                .get("Content-Type")
                ?.includes("text/event-stream") ||
              !stream.body
            )
              throw new Error("sse_unavailable");
            onTransport?.("live");
            const reader = stream.body.getReader(),
              decoder = new TextDecoder();
            let buffer = "";
            try {
              while (!signal.aborted) {
                clearTimeout(watchdog);
                watchdog = setTimeout(() => connection.abort(), 20000);
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder
                  .decode(value, { stream: true })
                  .replaceAll("\r\n", "\n");
                if (buffer.length > 1024 * 1024)
                  throw new Error("event_budget_exceeded");
                let end: number;
                while ((end = buffer.indexOf("\n\n")) >= 0) {
                  const frame = buffer.slice(0, end);
                  buffer = buffer.slice(end + 2);
                  if (frame.includes("event: revoked"))
                    throw new JobAccessError(403);
                  const data = frame
                    .split("\n")
                    .filter((line) => line.startsWith("data: "))
                    .map((line) => line.slice(6))
                    .join("\n");
                  if (data && accept(JSON.parse(data))) return latest!;
                }
              }
            } finally {
              await reader.cancel();
            }
          } catch (error) {
            if (signal.aborted) throw new DOMException("Aborted", "AbortError");
            if (
              error instanceof JobAccessError &&
              [401, 403, 404].includes(error.status)
            )
              throw error;
          } finally {
            clearTimeout(watchdog);
            connection.abort();
            signal.removeEventListener("abort", abort);
          }
          streamFailures += 1;
          onTransport?.("reconnecting");
        } else onTransport?.("polling");
        const retry = Number(response.headers.get("Retry-After"));
        const base = Number.isFinite(retry) && retry > 0 ? retry * 1000 : 2000;
        await wait(
          Math.min(15000, base * Math.max(1, Math.min(3, unchanged + 1))),
          signal,
        );
      }
      throw new DOMException("Aborted", "AbortError");
    },
  };
}
export type JobClient = ReturnType<typeof createJobClient>;
