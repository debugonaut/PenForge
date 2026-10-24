import assert from "node:assert/strict";
import { test } from "node:test";
import { createTypeScriptLoader } from "./helpers/typescript.mjs";

const moduleUrl = createTypeScriptLoader(import.meta.url);
const { createOwnerTokenProvider } = await import(
  await moduleUrl("../src/lib/owner-token.ts")
);

function fixture() {
  let uid = "owner-a";
  let calls = 0;
  let acquire = async () => `token-${calls}`;
  const mismatch = new Error("domain_specific_mismatch");
  const identity = {
    getSnapshot: () => ({ identity: uid ? { firebase_uid: uid } : null }),
    getAccessToken() {
      calls += 1;
      return acquire();
    },
  };
  return {
    token: createOwnerTokenProvider(identity, uid, () => mismatch),
    identity,
    mismatch,
    setUid: (value) => {
      uid = value;
    },
    setAcquire: (value) => {
      acquire = value;
    },
    calls: () => calls,
  };
}

test("owner token provider is lazy and obtains a fresh token per request", async () => {
  const current = fixture();
  assert.equal(current.calls(), 0);
  assert.equal(await current.token(), "token-1");
  assert.equal(await current.token(), "token-2");
});

test("an owner change before acquisition rejects without asking for a token", async () => {
  const current = fixture();
  current.setUid("owner-b");
  await assert.rejects(current.token(), (error) => error === current.mismatch);
  assert.equal(current.calls(), 0);
});

for (const destination of ["owner-b", null]) {
  test(`owner token is discarded when the identity changes to ${destination} during acquisition`, async () => {
    const current = fixture();
    let resolve;
    current.setAcquire(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const pending = current.token();
    assert.equal(current.calls(), 1);
    current.setUid(destination);
    resolve("must-not-be-returned");
    await assert.rejects(pending, (error) => error === current.mismatch);
  });
}

test("a missing captured owner never acquires a token", async () => {
  const current = fixture();
  for (const owner of [undefined, null, ""]) {
    const token = createOwnerTokenProvider(
      current.identity,
      owner,
      () => current.mismatch,
    );
    await assert.rejects(token(), (error) => error === current.mismatch);
  }
  assert.equal(current.calls(), 0);
});

test("token-provider failures propagate unchanged", async () => {
  const current = fixture();
  const failure = new TypeError("token service unavailable");
  current.setAcquire(async () => {
    throw failure;
  });
  await assert.rejects(current.token(), (error) => error === failure);
});

test("a provider retains the original owner when another operation is opened", async () => {
  const current = fixture();
  current.setUid("owner-b");
  const other = createOwnerTokenProvider(
    current.identity,
    "owner-b",
    () => current.mismatch,
  );
  assert.equal(await other(), "token-1");
  await assert.rejects(current.token(), (error) => error === current.mismatch);
  assert.equal(current.calls(), 1);
});
