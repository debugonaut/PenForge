import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { unicodeModuleUrl } from "./helpers/unicode.mjs";
const compiled = createTypeScriptLoader(import.meta.url);
const foundation = await compiled("../packages/contracts/src/client.ts");
const { createExportClient, ExportError } = await import(
  await compiled("../src/lib/export-client.ts", [
    ["../../packages/contracts/src/client", foundation],
  ])
);
const bytes = new Uint8Array([0, 1, 0, 0, 1, 2, 3, 4]);
const hash = "sha256:" + createHash("sha256").update(bytes).digest("hex");
const metadata = {
  id: "e",
  status: "ready",
  format: "ttf",
  sha256: hash,
  size_bytes: bytes.length,
};
function json(value) {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
}

test("export requests require identity token and preserve body and idempotency key", async () => {
  const calls = [];
  const client = createExportClient(
    "http://localhost:8000",
    async () => "owner-token",
    async (url, options) => {
      calls.push([url, options]);
      return json({ export_ids: ["e"], job_id: "j", snapshot_id: "s" });
    },
  );
  const body = {
    snapshot_id: "s",
    formats: ["ttf"],
    family_name: "Private Name",
  };
  await client.create("p", body, "stable-key");
  assert.equal(calls[0][1].headers.Authorization, "Bearer owner-token");
  assert.equal(calls[0][1].headers["Idempotency-Key"], "stable-key");
  assert.deepEqual(JSON.parse(calls[0][1].body), body);
  assert.equal(calls[0][1].referrerPolicy, "no-referrer");
});

test("expired capability renews once with owner authentication and verifies bytes", async () => {
  let grants = 0;
  const calls = [];
  const client = createExportClient(
    "http://localhost:8000",
    async () => "owner-token",
    async (url, options) => {
      calls.push([url.toString(), options]);
      if (url.pathname === "/api/v1/exports/e") return json(metadata);
      if (url.pathname.endsWith("/download-url"))
        return json({
          method: "GET",
          url: "/api/v1/storage/objects/c" + ++grants,
        });
      if (url.pathname.endsWith("/c1"))
        return new Response(null, { status: 404 });
      return new Response(bytes, { headers: { "Content-Type": "font/ttf" } });
    },
  );
  const result = await client.font("e");
  assert.equal(grants, 2);
  assert.equal(result.blob.size, bytes.length);
  const downloads = calls.filter(([url]) => url.includes("/storage/objects/"));
  assert.ok(
    downloads.every(
      ([, options]) =>
        options.headers.Authorization === "Bearer owner-token" &&
        options.credentials === "omit" &&
        options.redirect === "error",
    ),
  );
});

test("foreign, tampered, or external capabilities never become a font", async () => {
  for (const mode of ["foreign", "tampered", "external"]) {
    const client = createExportClient(
      "http://localhost:8000",
      async () => "owner-token",
      async (url) => {
        if (mode === "foreign") return jsonError();
        if (url.pathname === "/api/v1/exports/e") return json(metadata);
        if (url.pathname.endsWith("/download-url"))
          return json({
            method: "GET",
            url:
              mode === "external"
                ? "https://evil.example/api/v1/storage/objects/c"
                : "/api/v1/storage/objects/c",
          });
        return new Response(new Uint8Array(bytes.length), {
          headers: { "Content-Type": "font/ttf" },
        });
      },
    );
    await assert.rejects(client.font("e"), ExportError);
  }
  function jsonError() {
    return new Response(JSON.stringify({ error: { code: "not_found" } }), {
      status: 404,
    });
  }
});

test("generated Writing Desk font embeds exact selected bytes and retains format fields", async () => {
  const writing = await import(
    await compiled("../src/lib/writing.ts", [
      ["../../packages/contracts/src/unicode", unicodeModuleUrl],
    ])
  );
  const source = {
    name: "Generated Penforge",
    kind: "generated",
    fontDataUrl: "data:font/ttf;base64,AAEAAAECAwQ=",
    fontFamily: "PenforgeGeneratedFixture",
    fontLicense: "Private fixture",
    measureText: (text, size) => text.length * size * 0.6,
  };
  for (const format of ["letter", "note", "card"]) {
    const document = {
      ...writing.DEFAULT_WRITING_DOCUMENT,
      format,
      title: "My title",
      recipient: "Dear friend",
      body: "Preserved body",
      signature: "Writer",
      ink: "#74434b",
      accent: "#507662",
    };
    const svg = writing.buildWritingSvg(document, source);
    assert.ok(svg.includes(source.fontDataUrl));
    assert.ok(svg.includes(source.fontFamily));
    for (const text of [
      "My title",
      "Dear friend",
      "Preserved body",
      "Writer",
      "#74434b",
      "#507662",
    ])
      assert.ok(svg.includes(text), text);
  }
});

test("concurrent export request recovery and resolution preserve every operation", async () => {
  const recovery = await import(
    await compiled("../src/lib/export-recovery.ts")
  );
  const entries = new Map();
  const storage = {
    get length() {
      return entries.size;
    },
    key(index) {
      return [...entries.keys()][index] ?? null;
    },
    getItem(key) {
      return entries.get(key) ?? null;
    },
    setItem(key, value) {
      entries.set(key, value);
    },
    removeItem(key) {
      entries.delete(key);
    },
  };
  const prefix = recovery.exportRecoveryPrefix("owner", "project");
  const a = {
    key: "00000000-0000-4000-8000-000000000001",
    body: { family_name: "A", formats: ["ttf"] },
  };
  const b = {
    key: "00000000-0000-4000-8000-000000000002",
    body: { family_name: "B", formats: ["otf"] },
  };
  recovery.saveExportRecovery(storage, prefix, a);
  recovery.saveExportRecovery(storage, prefix, b);
  assert.equal(recovery.readExportRecoveries(storage, prefix).length, 2);
  recovery.completeExportRecovery(storage, prefix, a);
  assert.deepEqual(recovery.readExportRecoveries(storage, prefix), [b]);
  assert.equal(
    recovery.readExportRecoveries(
      storage,
      recovery.exportRecoveryPrefix("other", "project"),
    ).length,
    0,
  );
});
