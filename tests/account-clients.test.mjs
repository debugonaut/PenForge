import assert from "node:assert/strict";
import { test } from "node:test";
import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";
import { writingTextModuleUrl } from "./helpers/writing-text.mjs";

const moduleUrl = createTypeScriptLoader(import.meta.url);
const foundation = await moduleUrl("../packages/contracts/src/client.ts");
const requestUrl = await moduleUrl("../src/lib/account-request.ts", [
  ["../../packages/contracts/src/client", foundation],
]);
const writing = await moduleUrl("../src/lib/writing.ts", [
  ["../../packages/contracts/src/unicode", unicodeModuleUrl],
  ["./writing-text", writingTextModuleUrl],
]);
const workspace = await import(
  await moduleUrl("../src/lib/account-workspace.ts", [
    ["../../packages/contracts/src/client", foundation],
    ["./account-request", requestUrl],
  ])
);
const documents = await import(
  await moduleUrl("../src/lib/account-documents.ts", [
    ["../../packages/contracts/src/client", foundation],
    ["./account-request", requestUrl],
    ["./writing", writing],
  ])
);
const { DEFAULT_WRITING_DOCUMENT } = await import(writing);
const origin = "https://account.example.test";

test("saved-page wire mapping preserves per-selection styles and omits empty overrides", () => {
  const page = {
    ...DEFAULT_WRITING_DOCUMENT,
    body: "hello  !",
    textStyles: [
      { field: "body", start: 5, end: 7, spacing: 8 },
      { field: "body", start: 7, end: 8, fontSize: 64, baselineShift: 6 },
    ],
  };
  const wire = documents.writingBody(page);
  assert.deepEqual(wire.text_styles[1], {
    field: "body",
    start: 7,
    end: 8,
    font_size: 64,
    baseline_shift: 6,
  });
  assert.deepEqual(
    documents.documentFromWire({
      title: page.title,
      format: page.format,
      document: wire,
    }),
    page,
  );
  assert.equal(
    "text_styles" in documents.writingBody(DEFAULT_WRITING_DOCUMENT),
    false,
  );
});

const workspaceResponse = {
  project_id: "project",
  workspace: workspace.toWire(workspace.emptyWorkspace()),
  row_version: 3,
  updated_at: "2026-10-24T00:00:00Z",
};
const variants = [
  [
    "workspace",
    workspace.createWorkspaceClient,
    workspace.WorkspaceError,
    workspaceResponse,
  ],
  [
    "document",
    documents.createDocumentsClient,
    documents.DocumentError,
    { id: "document" },
  ],
];

for (const [name, create, ErrorType, body] of variants) {
  test(`${name} client validates origins without obtaining a token or fetching`, () => {
    for (const url of [
      "file:///private",
      "https://user:pass@account.test",
      `${origin}/path`,
      `${origin}?token=private`,
    ]) {
      assert.throws(
        () =>
          create(
            url,
            () => assert.fail("token"),
            () => assert.fail("fetch"),
          ),
        /invalid_api_origin/,
      );
    }
    create(
      origin,
      () => assert.fail("token"),
      () => assert.fail("fetch"),
    );
  });

  test(`${name} GET keeps private request options and encodes the resource ID`, async () => {
    const controller = new AbortController();
    let calls = 0;
    const client = create(
      origin,
      async () => "owner-token",
      async (url, options) => {
        calls += 1;
        assert.equal(url.origin, origin);
        assert.ok(url.pathname.includes("a%2Fb"));
        assert.equal(options.method, "GET");
        assert.deepEqual(options.headers, {
          Accept: "application/json",
          Authorization: "Bearer owner-token",
        });
        assert.equal("body" in options, false);
        assert.equal(options.credentials, "omit");
        assert.equal(options.redirect, "error");
        assert.equal(options.cache, "no-store");
        assert.equal(options.referrerPolicy, "no-referrer");
        assert.equal(options.signal, controller.signal);
        return Response.json(body, { headers: { ETag: '"v3"' } });
      },
    );
    const result = await client.get("a/b", controller.signal);
    assert.equal(calls, 1);
    if (name === "workspace") {
      assert.equal(result.version, 3);
      assert.equal(result.projectId, "project");
    } else assert.deepEqual(result, body);
  });

  test(`${name} rejects invalid tokens before sending a request`, async () => {
    for (const token of ["", "two words", "owner\nsecret"]) {
      const client = create(
        origin,
        async () => token,
        () => assert.fail("fetch"),
      );
      await assert.rejects(
        client.get("id"),
        (error) =>
          error instanceof ErrorType &&
          error.code === "unauthenticated" &&
          error.status === 401,
      );
    }
  });

  test(`${name} preserves token-provider and abort failures`, async () => {
    const mismatch = new Error("identity_changed");
    const client = create(
      origin,
      async () => {
        throw mismatch;
      },
      () => assert.fail("fetch"),
    );
    await assert.rejects(client.get("id"), (error) => error === mismatch);
    const abort = new DOMException("Aborted", "AbortError");
    const aborted = create(
      origin,
      async () => "token",
      async () => {
        throw abort;
      },
    );
    await assert.rejects(aborted.get("id"), (error) => error === abort);
  });

  test(`${name} reports offline failures without private network text`, async () => {
    const client = create(
      origin,
      async () => "token",
      async () => {
        throw new TypeError("private URL and token");
      },
    );
    await assert.rejects(
      client.get("id"),
      (error) =>
        error instanceof ErrorType &&
        error.code === "network_unavailable" &&
        error.status === 0 &&
        !error.message.includes("private"),
    );
  });

  test(`${name} keeps conflict version context and hides server messages`, async () => {
    const client = create(
      origin,
      async () => "token",
      async () =>
        Response.json(
          {
            error: {
              code: "revision_conflict",
              message: "private handwriting",
              context: { current_row_version: 42 },
            },
          },
          { status: 412 },
        ),
    );
    await assert.rejects(
      client.get("id"),
      (error) =>
        error instanceof ErrorType &&
        error.code === "revision_conflict" &&
        error.status === 412 &&
        error.currentVersion === 42 &&
        !error.message.includes("handwriting"),
    );
  });

  test(`${name} retains its error allowlist and existing remapping`, async () => {
    for (const code of [
      "private_unknown",
      "invalid_token",
      "account_merged",
      "rate_limited",
      "account_deleting",
      "state_conflict",
    ]) {
      const client = create(
        origin,
        async () => "token",
        async () => Response.json({ error: { code } }, { status: 409 }),
      );
      const expected =
        code === "account_deleting" ||
        (name === "workspace" && code === "state_conflict")
          ? code
          : "dependency_unavailable";
      await assert.rejects(
        client.get("id"),
        (error) =>
          error instanceof ErrorType &&
          error.code === expected &&
          error.status === 409,
      );
    }
  });

  test(`${name} hides non-JSON errors and preserves malformed-success rejection`, async () => {
    const failure = create(
      origin,
      async () => "token",
      async () => new Response("private upstream failure", { status: 503 }),
    );
    await assert.rejects(
      failure.get("id"),
      (error) =>
        error instanceof ErrorType && error.code === "dependency_unavailable",
    );
    const malformed = create(
      origin,
      async () => "token",
      async () => new Response("not JSON"),
    );
    await assert.rejects(malformed.get("id"), SyntaxError);
  });
}

test("workspace writes retain their exact conditional body and never gain retries", async () => {
  let calls = 0;
  const state = workspace.emptyWorkspace();
  const client = workspace.createWorkspaceClient(
    origin,
    async () => "token",
    async (url, options) => {
      calls += 1;
      assert.equal(url.pathname, "/api/v1/projects/project/workspace");
      assert.equal(options.method, "PUT");
      assert.equal(options.headers["If-Match"], '"v2"');
      assert.equal(options.headers["Idempotency-Key"], undefined);
      assert.equal(options.headers["Content-Type"], "application/json");
      assert.deepEqual(JSON.parse(options.body), {
        workspace: workspace.toWire(state),
      });
      return Response.json(workspaceResponse);
    },
  );
  assert.equal((await client.put("project", state, 2)).version, 3);
  assert.equal(calls, 1);
});

test("document create/update/delete retain idempotency, row versions and 204 behavior", async () => {
  const calls = [];
  const client = documents.createDocumentsClient(
    origin,
    async () => "token",
    async (url, options) => {
      calls.push([url, options]);
      return options.method === "DELETE"
        ? new Response(null, { status: 204 })
        : Response.json({ id: "document" });
    },
  );
  const page = { ...DEFAULT_WRITING_DOCUMENT, title: "My page" };
  await client.create(page, "note", "original-key");
  await client.update("a/b", page, "note", 4);
  assert.equal(await client.remove("a/b", 5), undefined);
  assert.deepEqual(
    calls.map(([, options]) => options.method),
    ["POST", "PATCH", "DELETE"],
  );
  assert.equal(calls[0][1].headers["Idempotency-Key"], "original-key");
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    title: page.title,
    format: "note",
    document: documents.writingBody(page),
  });
  assert.equal(calls[1][0].pathname, "/api/v1/documents/a%2Fb");
  assert.equal(calls[1][1].headers["If-Match"], '"v4"');
  assert.equal(calls[2][1].headers["If-Match"], '"v5"');
  assert.equal("body" in calls[2][1], false);
});

test("workspace 404 stays null while a missing document remains an error", async () => {
  const missing = async () =>
    Response.json({ error: { code: "not_found" } }, { status: 404 });
  assert.equal(
    await workspace
      .createWorkspaceClient(origin, async () => "token", missing)
      .get("id"),
    null,
  );
  await assert.rejects(
    documents
      .createDocumentsClient(origin, async () => "token", missing)
      .get("id"),
    (error) =>
      error instanceof documents.DocumentError && error.code === "not_found",
  );
});

test("workspace empty success remains malformed rather than inventing saved state", async () => {
  const client = workspace.createWorkspaceClient(
    origin,
    async () => "token",
    async () => new Response(null, { status: 204 }),
  );
  await assert.rejects(client.get("id"), SyntaxError);
});

test("document pagination retains its cursor encoding", async () => {
  const urls = [];
  const client = documents.createDocumentsClient(
    origin,
    async () => "token",
    async (url) => {
      urls.push(url);
      return Response.json(
        urls.length === 1
          ? { items: [{ id: "first" }], next_cursor: "a+b/=" }
          : { items: [{ id: "second" }], next_cursor: null },
      );
    },
  );
  assert.deepEqual(await client.list(), [{ id: "first" }, { id: "second" }]);
  assert.equal(urls[1].searchParams.get("cursor"), "a+b/=");
});
