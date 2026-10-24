import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

const moduleUrl = createTypeScriptLoader(import.meta.url);
const importTypeScript = async (relative) => import(await moduleUrl(relative));
const auth = await importTypeScript("../src/lib/auth-service.ts");
const transport = await importTypeScript("../packages/contracts/src/client.ts");
const hosted = {
  NODE_ENV: "production",
  NEXT_PUBLIC_PENFORGE_AUTH_BACKEND: "firebase",
  NEXT_PUBLIC_PENFORGE_ENV: "production",
  NEXT_PUBLIC_PENFORGE_API_ORIGIN: "https://api.example.test",
  NEXT_PUBLIC_FIREBASE_API_KEY: "public-client-key",
  NEXT_PUBLIC_FIREBASE_PROJECT_ID: "penforge-test",
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "penforge-test.firebaseapp.com",
  NEXT_PUBLIC_FIREBASE_APP_ID: "test-app",
};
const configuration = auth.resolveAuthConfiguration(
  hosted,
  "penforge.example.test",
);
const me = (uid, anonymous) => ({
  user_id: anonymous ? "guest-internal" : "registered-internal",
  firebase_uid: uid,
  is_anonymous: anonymous,
  sign_in_provider: anonymous ? "anonymous" : "password",
  email_verified: false,
  tier: anonymous ? "free_guest" : "free_user",
  created_at: "2026-10-19T00:00:00Z",
  guest_creations: { limit: 3, used: 2, remaining: 1, applicable: anonymous },
  quotas: { configured: false, over_limit: false, balances: [] },
});
function fixture() {
  let user = null;
  let collision = false;
  let failMe = false;
  let loaded = 0;
  const calls = [];
  const mkUser = (uid, isAnonymous) => ({
    uid,
    isAnonymous,
    getIdToken: async () => `${uid}-private-proof`,
  });
  const provider = {
    currentUser: () => user,
    ready: async () => {},
    guest: async () => {
      user = mkUser("guest", true);
    },
    register: async () => {
      if (collision)
        throw {
          code: "auth/email-already-in-use",
          message: "private@example.test password-secret",
        };
      user = mkUser(user?.uid || "new-account", false);
    },
    email: async () => {
      user = mkUser("existing-account", false);
    },
    google: async () => {
      user = mkUser(user?.uid || "google-account", false);
    },
    github: async () => {
      user = mkUser(user?.uid || "github-account", false);
    },
    sendVerification: async () => {
      calls.push("verification-sent");
    },
    refreshVerification: async () => {
      calls.push("verification-refreshed");
    },
    signOut: async () => {
      user = null;
    },
  };
  const api = {
    me: async () => {
      calls.push("me");
      if (failMe)
        throw {
          code: "dependency_unavailable",
          message: "raw-private-message",
        };
      return me(user.uid, user.isAnonymous);
    },
    claim: async (proof) => {
      calls.push(["claim", proof]);
      return { mode: "transferred", over_limit: true };
    },
  };
  const service = new auth.IdentityService(
    configuration,
    async () => {
      loaded++;
      return provider;
    },
    async () => api,
  );
  return {
    service,
    calls,
    setCollision: (value) => {
      collision = value;
    },
    setFail: (value) => {
      failMe = value;
    },
    loaded: () => loaded,
    setUser: (uid, anon) => {
      user = mkUser(uid, anon);
    },
  };
}

test("default demo and invalid configuration never load Firebase or call the account API", async () => {
  let called = 0;
  const service = new auth.IdentityService(
    auth.resolveAuthConfiguration({}),
    async () => {
      called++;
      throw new Error();
    },
  );
  assert.equal(service.getSnapshot().phase, "unavailable");
  assert.equal(called, 0);
  await service.startGuest();
  assert.equal(called, 0);
  assert.equal(service.getSnapshot().identity, null);
  assert.match(service.getSnapshot().error, /unavailable/);
  assert.equal(
    auth.resolveAuthConfiguration({
      ...hosted,
      NEXT_PUBLIC_PENFORGE_AUTH_BACKEND: "fake",
    }).state,
    "disabled",
  );
  assert.equal(
    auth.resolveAuthConfiguration({
      ...hosted,
      NEXT_PUBLIC_FIREBASE_API_KEY: "",
    }).state,
    "disabled",
  );
});

test("emulator requires explicit local development, demo project and loopback frontend/API/emulator", () => {
  const local = {
    ...hosted,
    NODE_ENV: "development",
    NEXT_PUBLIC_PENFORGE_ENV: "local",
    NEXT_PUBLIC_PENFORGE_API_ORIGIN: "http://127.0.0.1:8000",
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: "demo-penforge-identity",
    NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_URL: "http://127.0.0.1:9099",
  };
  assert.equal(
    auth.resolveAuthConfiguration(local, "localhost").state,
    "configured",
  );
  for (const [env, hostname] of [
    [{ ...local, NODE_ENV: "production" }, "localhost"],
    [{ ...local, NEXT_PUBLIC_PENFORGE_ENV: "production" }, "localhost"],
    [
      {
        ...local,
        NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_URL: "http://evil.example:9099",
      },
      "localhost",
    ],
    [
      { ...local, NEXT_PUBLIC_PENFORGE_API_ORIGIN: "https://api.example.test" },
      "localhost",
    ],
    [
      { ...local, NEXT_PUBLIC_FIREBASE_PROJECT_ID: "live-project" },
      "localhost",
    ],
    [local, "public.example.test"],
    [local, undefined],
  ])
    assert.equal(
      auth.resolveAuthConfiguration(env, hostname).state,
      "disabled",
    );
  for (const value of [
    "https://user:secret@api.test",
    "https://api.test/path",
    "https://api.test?secret=value",
    "http://api.test",
  ])
    assert.equal(
      auth.resolveAuthConfiguration(
        { ...hosted, NEXT_PUBLIC_PENFORGE_API_ORIGIN: value },
        "public.test",
      ).state,
      "disabled",
    );
});

test("configuration and opening the screen are inert; guest is verified by /me only after explicit action", async () => {
  const f = fixture();
  assert.equal(f.loaded(), 0);
  assert.deepEqual(f.calls, []);
  await f.service.startGuest();
  assert.equal(f.loaded(), 1);
  assert.equal(f.service.getSnapshot().identity.is_anonymous, true);
  assert.deepEqual(f.calls, ["me"]);
  await f.service.signOut();
  assert.equal(f.service.getSnapshot().identity, null);
  assert.match(f.service.getSnapshot().message, /local drafts/);
});

test("GitHub sign-in goes through the provider path and verifies identity through /me", async () => {
  const f = fixture();
  await f.service.signInGithub();
  assert.equal(f.loaded(), 1);
  assert.deepEqual(f.calls, ["me"]);
  assert.equal(f.service.getSnapshot().phase, "signed-in");
  assert.equal(f.service.getSnapshot().identity.firebase_uid, "github-account");
  assert.equal(f.service.getSnapshot().identity.is_anonymous, false);
});

test("same-UID linking upgrades in place and never sends a claim or local content", async () => {
  const f = fixture();
  await f.service.startGuest();
  await f.service.register("writer@example.test", "password-secret");
  assert.equal(f.service.getSnapshot().identity.firebase_uid, "guest");
  assert.equal(f.service.getSnapshot().identity.is_anonymous, false);
  assert.equal(f.service.getSnapshot().pendingClaim, false);
  assert.deepEqual(f.calls, ["me", "me"]);
  assert.equal(
    JSON.stringify(f.service.getSnapshot()).includes("private-proof"),
    false,
  );
});

test("credential collision preserves memory-only guest proof through existing account sign-in; claim needs explicit approval", async () => {
  const f = fixture();
  await f.service.startGuest();
  f.setCollision(true);
  await f.service.register("writer@example.test", "password-secret");
  assert.match(f.service.getSnapshot().error, /already has an account/);
  assert.doesNotMatch(
    f.service.getSnapshot().error,
    /private@example|password-secret/,
  );
  await f.service.signInEmail("writer@example.test", "password-secret");
  assert.equal(f.service.getSnapshot().pendingClaim, true);
  assert.equal(
    f.service.getSnapshot().identity.firebase_uid,
    "existing-account",
  );
  assert.equal(f.calls.filter(Array.isArray).length, 0);
  assert.doesNotMatch(
    JSON.stringify(f.service.getSnapshot()),
    /guest-private-proof/,
  );
  await f.service.claim(false);
  assert.match(f.service.getSnapshot().error, /Confirm/);
  assert.equal(f.calls.filter(Array.isArray).length, 0);
  await f.service.claim(true);
  assert.deepEqual(f.calls.filter(Array.isArray), [
    ["claim", "guest-private-proof"],
  ]);
  assert.equal(f.service.getSnapshot().pendingClaim, false);
  assert.match(f.service.getSnapshot().message, /exceeds a configured quota/);
  await f.service.claim(true);
  assert.equal(f.calls.filter(Array.isArray).length, 1);
});

test("failed account API never becomes a verified guest and stale UI identity is cleared after account switch", async () => {
  const f = fixture();
  f.setFail(true);
  await f.service.startGuest();
  assert.equal(f.service.getSnapshot().identity, null);
  assert.match(f.service.getSnapshot().error, /API is unavailable/);
  f.setFail(false);
  await f.service.connect();
  f.setFail(true);
  await f.service.signInEmail("writer@example.test", "secret");
  assert.equal(f.service.getSnapshot().identity, null);
  assert.equal(f.service.getSnapshot().pendingClaim, false);
  f.setFail(false);
  await f.service.connect();
  assert.equal(f.service.getSnapshot().pendingClaim, true);
});

test("claim detects destination-session changes and sign-out clears the ephemeral proof", async () => {
  const f = fixture();
  await f.service.startGuest();
  await f.service.signInEmail("writer@example.test", "secret");
  f.setUser("other-account", false);
  await f.service.claim(true);
  assert.equal(f.calls.filter(Array.isArray).length, 0);
  assert.match(f.service.getSnapshot().error, /account changed/);
  await f.service.signOut();
  await f.service.signInEmail("writer@example.test", "secret");
  assert.equal(f.service.getSnapshot().pendingClaim, false);
});

test("HTTP account client sends proofs only on explicit scoped requests with no cookies, redirects or cache", async () => {
  const calls = [];
  const client = transport.createIdentityClient(
    "https://api.example.test",
    async () => "destination-proof",
    async (url, options) => {
      calls.push([url.pathname, options]);
      return new Response(
        JSON.stringify(
          url.pathname.endsWith("/me")
            ? me("destination", false)
            : { mode: "transferred" },
        ),
        { status: 200 },
      );
    },
  );
  assert.deepEqual(calls, []);
  await client.me();
  await client.claim("guest-secret");
  assert.deepEqual(
    calls.map(([path]) => path),
    ["/api/v1/me", "/api/v1/identity/claim"],
  );
  for (const [, options] of calls) {
    assert.equal(options.headers.Authorization, "Bearer destination-proof");
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store");
    assert.equal(options.referrerPolicy, "no-referrer");
  }
  assert.equal(calls[0][1].body, undefined);
  assert.deepEqual(JSON.parse(calls[1][1].body), {
    guest_id_token: "guest-secret",
  });
});

test("API and SDK errors expose only safe codes and never raw private messages", async () => {
  for (const [body, expected] of [
    [
      { error: { code: "account_merged", message: "secret-token" } },
      "account_merged",
    ],
    [{ error: { code: "private-token" } }, "request_failed"],
  ]) {
    const client = transport.createIdentityClient(
      "https://api.example.test",
      async () => "proof",
      async () =>
        new Response(JSON.stringify(body), {
          status: 403,
          headers: { "X-Request-ID": "safe-id" },
        }),
    );
    await assert.rejects(
      client.me(),
      (error) =>
        error.code === expected &&
        error.status === 403 &&
        !error.message.includes("secret-token") &&
        !error.message.includes("private-token"),
    );
  }
  assert.doesNotMatch(
    auth.safeAuthError({ message: "secret-token writer@example.test" }),
    /secret-token|writer@/,
  );
  assert.match(auth.safeAuthError({ code: "auth/popup-blocked" }), /popup/);
});

test("same-tab account remount reuses only its in-memory service and configuration changes isolate it", () => {
  const first = auth.getAccountIdentityService(configuration);
  assert.equal(
    auth.getAccountIdentityService(structuredClone(configuration)),
    first,
  );
  const disabled = auth.getAccountIdentityService({
    state: "disabled",
    reason: "not-selected",
  });
  assert.notEqual(disabled, first);
  assert.equal(disabled.getSnapshot().phase, "unavailable");
});

test("verification is user-requested and returns to a usable signed-in state", async () => {
  const f = fixture();
  await f.service.register("synthetic@example.invalid", "test-password");
  assert.ok(!f.calls.includes("verification-sent"));
  await f.service.sendVerification();
  assert.ok(f.calls.includes("verification-sent"));
  assert.equal(f.service.getSnapshot().phase, "signed-in");
  assert.match(f.service.getSnapshot().message, /Verification email sent/);
  await f.service.refreshVerification();
  assert.ok(f.calls.includes("verification-refreshed"));
  assert.equal(f.service.getSnapshot().phase, "signed-in");
});

test("verification from a stale account screen cannot send to the next identity", async () => {
  const f = fixture();
  await f.service.register("synthetic@example.invalid", "test-password");
  f.setUser("other-account", false);
  await f.service.sendVerification();
  assert.ok(!f.calls.includes("verification-sent"));
  assert.equal(f.service.getSnapshot().identity, null);
});
