import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
const compiled = mkdtempSync(path.join(tmpdir(), "penforge-privacy-"));
execFileSync(
  path.resolve("node_modules/.bin/tsc"),
  [
    "--outDir",
    compiled,
    "--target",
    "ES2022",
    "--module",
    "commonjs",
    "--moduleResolution",
    "node",
    "--lib",
    "ES2022,DOM",
    "--strict",
    "--skipLibCheck",
    "src/lib/privacy-client.ts",
  ],
  { stdio: "pipe" },
);
const { createPrivacyClient } = createRequire(import.meta.url)(
  path.join(compiled, "src/lib/privacy-client.js"),
);
process.on("exit", () => rmSync(compiled, { recursive: true, force: true }));
const origin = "http://127.0.0.1:8000";
test("opening the private client makes no network request or local draft access", () => {
  let calls = 0;
  createPrivacyClient(
    origin,
    async () => "token",
    async () => {
      calls++;
      return Response.json({});
    },
  );
  assert.equal(calls, 0);
});
test("consent is explicit purpose-specific and separate from contribution", async () => {
  const calls = [];
  const client = createPrivacyClient(
    origin,
    async () => "verified-token",
    async (url, options) => {
      calls.push([url, options]);
      return Response.json({ granted: true });
    },
  );
  await client.setConsent(true, "model-training-v1");
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    purpose: "model_training",
    granted: true,
    policy_version: "model-training-v1",
  });
  assert.equal(calls[0][1].credentials, "omit");
  assert.equal(calls[0][1].redirect, "error");
  assert.equal(calls[0][1].headers.Authorization, "Bearer verified-token");
});
test("deletion preserves exact confirmation scope and idempotency key", async () => {
  const calls = [];
  const client = createPrivacyClient(
    origin,
    async () => "token",
    async (url, options) => {
      calls.push(options);
      return Response.json({ id: "operation" }, { status: 202 });
    },
  );
  const body = {
    scope: "project",
    project_id: "owned",
    expected_project_version: 3,
    confirmed: true,
  };
  await client.delete(body, "same-key");
  await client.delete(body, "same-key");
  assert.equal(calls[0].body, calls[1].body);
  assert.equal(calls[0].headers["Idempotency-Key"], "same-key");
  assert.deepEqual(JSON.parse(calls[0].body), body);
});
test("owner progress receipt works after Firebase deletion without sending bearer", async () => {
  let tokens = 0;
  const client = createPrivacyClient(
    origin,
    async () => {
      tokens++;
      throw Error("revoked");
    },
    async (url, options) => {
      assert.equal(options.headers["X-Deletion-Receipt"], "private-receipt");
      assert.equal(options.headers.Authorization, undefined);
      assert.equal(new URL(url).search, "");
      return Response.json({ state: "completed" });
    },
  );
  assert.equal(
    (await client.progress("id", "private-receipt")).state,
    "completed",
  );
  assert.equal(tokens, 0);
});
test("private errors never echo server detail or source text", async () => {
  const client = createPrivacyClient(
    origin,
    async () => "token",
    async () =>
      Response.json(
        {
          error: {
            code: "unknown_private_content",
            message: "private handwriting",
          },
        },
        { status: 500 },
      ),
  );
  await assert.rejects(client.consent(), /request_failed/);
});
test("unsafe origins and malformed bearer tokens fail before network", async () => {
  assert.throws(() =>
    createPrivacyClient("https://host.invalid/unsafe", async () => "token"),
  );
  let calls = 0;
  const client = createPrivacyClient(
    origin,
    async () => "bad token",
    async () => {
      calls++;
      return Response.json({});
    },
  );
  await assert.rejects(client.consent(), /unauthenticated/);
  assert.equal(calls, 0);
});
