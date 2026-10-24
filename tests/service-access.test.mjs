import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createTypeScriptLoader,
  javascriptModuleUrl,
} from "./helpers/typescript.mjs";
const load = createTypeScriptLoader(import.meta.url);
const foundation = await load("../packages/contracts/src/client.ts");
const request = await load("../src/lib/account-request.ts", [
  ["../../packages/contracts/src/client", foundation],
]);
const accessUrl = await load("../src/lib/service-access-client.ts", [
  ["./account-request", request],
]);
const { createServiceAccessClient, serviceAccessMessage, ServiceAccessError } =
  await import(accessUrl);
const ownerUrl = await load("../src/lib/owner-token.ts");
const { createOwnerTokenProvider } = await import(ownerUrl);
const hash = "a".repeat(64);

test("acceptance sends the exact policy hash and both booleans with an owner-bound token", async () => {
  const calls = [];
  const client = createServiceAccessClient(
    "https://api.example.test",
    async () => "owner-proof",
    async (url, options) => {
      calls.push([url.toString(), options]);
      return Response.json({
        accepted: true,
        eligible: true,
        policy_sha256: hash,
      });
    },
  );
  await client.accept(hash);
  assert.equal(
    calls[0][0],
    "https://api.example.test/api/v1/me/service-agreements",
  );
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    policy_sha256: hash,
    terms_accepted: true,
    handwriting_rights_confirmed: true,
  });
  assert.equal(calls[0][1].headers.Authorization, "Bearer owner-proof");
  assert.equal(calls[0][1].redirect, "error");
  assert.equal(calls[0][1].credentials, "omit");
});
test("a stale account cannot submit an acceptance for the next account", async () => {
  let uid = "a",
    resolve;
  const identity = {
    getSnapshot: () => ({ identity: { firebase_uid: uid } }),
    getAccessToken: () => new Promise((done) => (resolve = done)),
  };
  let requests = 0;
  const client = createServiceAccessClient(
    "https://api.example.test",
    createOwnerTokenProvider(
      identity,
      "a",
      () => new ServiceAccessError("unauthenticated"),
    ),
    async () => {
      requests++;
      return Response.json({});
    },
  );
  const pending = client.accept(hash);
  uid = "b";
  resolve("old-owner-proof");
  await assert.rejects(pending, (error) => error.code === "unauthenticated");
  assert.equal(requests, 0);
});
test("policy changes and private backend errors become safe next-action text", async () => {
  const client = createServiceAccessClient(
    "https://api.example.test",
    async () => "owner",
    async () =>
      Response.json(
        {
          error: { code: "policy_changed", message: "private token and email" },
        },
        { status: 409 },
      ),
  );
  await assert.rejects(
    client.accept(hash),
    (error) =>
      error.code === "policy_changed" &&
      /updated version/.test(serviceAccessMessage(error)) &&
      !error.message.includes("private token"),
  );
});

const hooks = javascriptModuleUrl(
  `export const useState=(...a)=>globalThis.__serviceHook.state(...a);export const useMemo=(...a)=>globalThis.__serviceHook.memo(...a);export const useCallback=(...a)=>globalThis.__serviceHook.memo(()=>a[0],a[1]);export const useEffect=(...a)=>globalThis.__serviceHook.effect(...a);`,
);
const mockClient = javascriptModuleUrl(
  `export class ServiceAccessError extends Error {}; export const serviceAccessMessage=()=>"safe failure";export const createServiceAccessClient=()=>globalThis.__serviceHook.clients.shift();`,
);
const { useServiceAccess } = await import(
  await load("../src/lib/use-service-access.ts", [
    ["react", hooks],
    ["./service-access-client", mockClient],
    ["./owner-token", ownerUrl],
  ])
);
function deferred() {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
}
function AccessHookFixture(session) {
  return useServiceAccess(session);
}
function harness(clients) {
  const cells = [];
  let cursor = 0,
    pending = [];
  const same = (a, b) =>
    a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const h = {
    clients,
    state(initial) {
      const i = cursor++;
      if (!cells[i]) cells[i] = { value: initial };
      return [
        cells[i].value,
        (v) => {
          cells[i].value = typeof v === "function" ? v(cells[i].value) : v;
        },
      ];
    },
    memo(fn, deps) {
      const i = cursor++;
      if (!same(cells[i]?.deps, deps)) cells[i] = { deps, value: fn() };
      return cells[i].value;
    },
    effect(fn, deps) {
      const i = cursor++;
      if (!same(cells[i]?.deps, deps)) {
        cells[i]?.cleanup?.();
        cells[i] = { deps };
        pending.push(() => (cells[i].cleanup = fn()));
      }
    },
    render(session) {
      cursor = 0;
      globalThis.__serviceHook = h;
      const view = AccessHookFixture(session);
      pending.splice(0).forEach((fn) => fn());
      return view;
    },
  };
  // useCallback returns the function; useMemo invokes its factory.
  return h;
}
// The hook's refresh callback is not invoked in these lifecycle assertions.
const tick = () => new Promise((done) => setImmediate(done));
test("late access replies cannot unlock another account or a changed policy", async () => {
  const a = deferred(),
    b = deferred();
  let uid = "a";
  const client = (d) => ({
    policy: () => d.promise.then(() => ({ sha256: hash, ready: true })),
    status: () =>
      d.promise.then(() => ({ policy_sha256: hash, eligible: true })),
  });
  const h = harness([client(a), client(b)]);
  const identity = { getSnapshot: () => ({ identity: { firebase_uid: uid } }) };
  const session = () => ({
    uid,
    identity,
    configuration: config,
    auth: {
      identity: {
        firebase_uid: uid,
        email_verified: true,
        is_anonymous: false,
      },
    },
  });
  const config = { state: "configured", apiOrigin: "https://api.example.test" };
  assert.equal(h.render(session()).eligible, false);
  uid = "b";
  assert.equal(h.render(session()).eligible, false);
  a.resolve();
  await tick();
  assert.equal(h.render(session()).eligible, false);
  b.resolve();
  await tick();
  assert.equal(h.render(session()).eligible, true);
  assert.equal(
    h.render({
      ...session(),
      auth: { identity: { email_verified: false, is_anonymous: false } },
    }).eligible,
    false,
  );
});

test("an acceptance for another policy hash never unlocks creation", async () => {
  const identity = { getSnapshot: () => ({ identity: { firebase_uid: "a" } }) };
  const configuration = {
    state: "configured",
    apiOrigin: "https://api.example.test",
  };
  const session = {
    uid: "a",
    identity,
    configuration,
    auth: { identity: { email_verified: true, is_anonymous: false } },
  };
  const h = harness([
    {
      policy: async () => ({ sha256: hash, ready: true }),
      status: async () => ({ policy_sha256: "b".repeat(64), eligible: true }),
    },
  ]);
  h.render(session);
  await tick();
  assert.equal(h.render(session).eligible, false);
});
