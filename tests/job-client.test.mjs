import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
const moduleUrl = createTypeScriptLoader(import.meta.url);
const { createJobClient, JobAccessError } = await import(await moduleUrl("../src/lib/job-client.ts"));
const state = (event_id, value = "queued") => ({
  job_id: "job",
  event_id,
  state: value,
  quality_policy: "accuracy_first",
  eta_seconds: null,
  progress: { completed_units: event_id, total_units: 10 },
});
const json = (value) =>
  new Response(JSON.stringify(value), {
    headers: {
      "Content-Type": "application/json",
      ETag: String(value.event_id),
      "Retry-After": "2",
    },
  });
const frame = (value) =>
  `id: ${value.event_id}\nevent: status\ndata: ${JSON.stringify(value)}\n\n`;

test("SSE reconnect resumes unseen ordered states and suppresses duplicates", async () => {
  let streams = 0;
  const cursors = [],
    seen = [];
  const client = createJobClient(
    "http://localhost:8000",
    async () => "secret-owner",
    async (url, options) => {
      assert.equal(options.headers.Authorization, "Bearer secret-owner");
      assert(!url.href.includes("secret-owner"));
      assert.equal(options.credentials, "omit");
      if (url.pathname.endsWith("/events")) {
        cursors.push(options.headers["Last-Event-ID"]);
        streams++;
        return new Response(
          streams === 1
            ? frame(state(2, "running")) + frame(state(2, "running"))
            : frame(state(3, "succeeded")),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      }
      return json(state(streams ? 2 : 1, streams ? "running" : "queued"));
    },
    async () => {},
  );
  const final = await client.watch(
    "job",
    (value) => seen.push(value.event_id),
    new AbortController().signal,
  );
  assert.equal(final.state, "succeeded");
  assert.deepEqual(seen, [1, 2, 3]);
  assert.deepEqual(cursors, ["1", "2"]);
});

test("unavailable SSE falls back to adaptive ETag polling without resetting units", async () => {
  let polls = 0,
    streams = 0;
  const seen = [],
    transports = [],
    delays = [];
  const client = createJobClient(
    "http://localhost:8000",
    async () => "owner",
    async (url, options) => {
      if (url.pathname.endsWith("/events")) {
        streams++;
        return new Response("", { status: 503 });
      }
      polls++;
      if (polls < 4) return json(state(1));
      assert(options.headers["If-None-Match"]);
      return json(state(2, "canceled"));
    },
    async (ms) => {
      delays.push(ms);
    },
  );
  const result = await client.watch(
    "job",
    (v) => seen.push(v.event_id),
    new AbortController().signal,
    (v) => transports.push(v),
  );
  assert.equal(result.state, "canceled");
  assert.equal(streams, 2);
  assert(transports.includes("polling"));
  assert.deepEqual(seen, [1, 2]);
  assert(delays[2] > delays[0]);
});

test("revoked stream stops immediately and cancellation stays owner authenticated", async () => {
  const methods = [];
  const client = createJobClient(
    "https://example.test",
    async () => "owner",
    async (url, options) => {
      methods.push(options.method);
      if (url.pathname.endsWith("/cancel"))
        return json(state(2, "cancel_requested"));
      if (url.pathname.endsWith("/events"))
        return new Response(
          'event: revoked\ndata: {"code":"access_revoked"}\n\n',
          { headers: { "Content-Type": "text/event-stream" } },
        );
      return json(state(1));
    },
    async () => {},
  );
  await assert.rejects(
    client.watch("job", () => {}, new AbortController().signal),
    (e) => e instanceof JobAccessError && e.status === 403,
  );
  assert.equal(
    (await client.cancel("job", new AbortController().signal)).state,
    "cancel_requested",
  );
  assert.equal(methods.at(-1), "POST");
});
