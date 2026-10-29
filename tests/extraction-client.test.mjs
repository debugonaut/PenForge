import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

const url = createTypeScriptLoader(import.meta.url);
const geometry = await import(await url("../src/lib/canonical-geometry.ts"));
const foundation = await url("../packages/contracts/src/client.ts");
const { createExtractionClient, ExtractionError } = await import(
  await url("../src/lib/extraction-client.ts", [
    ["../../packages/contracts/src/client", foundation],
  ])
);

test("manual photo batch uses explicit region mode without drawing or template assignments", async () => {
  const calls = [];
  const client = createExtractionClient(
    "https://review.example.test",
    async () => "verified-token",
    async (url, options) => {
      calls.push({ url: String(url), options });
      return Response.json({ job_id: "job-a", state: "queued" });
    },
  );
  const regions = [
    { region_id: "region-a", sample_id: "sample-a", codepoints: [103] },
  ];
  await client.analyzeImageRegions(
    "project-a",
    "sample-a",
    regions,
    "stable-key",
  );
  assert.equal(calls.length, 1);
  const { options } = calls[0];
  assert.deepEqual(JSON.parse(options.body), {
    job_type: "analyze_samples",
    sample_ids: ["sample-a"],
    image_mode: "manual_regions_v1",
    image_regions: regions,
  });
  assert.equal(options.headers["Idempotency-Key"], "stable-key");
  assert.equal(options.credentials, "omit");
});
const point = (x, y) => ({ x, y });
const outline = {
  schema_version: 1,
  units_per_em: 1000,
  coordinate_system: {
    origin: "baseline",
    x_positive: "right",
    y_positive: "up",
  },
  codepoints: [103],
  mapping: "cmap",
  fill_rule: "evenodd",
  metrics: { advance_width: 600, left_side_bearing: 40 },
  contours: [
    {
      closed: true,
      start: point(40, -200),
      segments: [
        {
          type: "cubic",
          c1: point(10, 100),
          c2: point(30, 700),
          to: point(500, 500),
        },
        { type: "quadratic", c: point(550, 100), to: point(40, -200) },
      ],
    },
    {
      closed: true,
      start: point(120, 120),
      segments: [
        { type: "line", to: point(200, 300) },
        { type: "line", to: point(300, 120) },
        { type: "line", to: point(120, 120) },
      ],
    },
  ],
};

test("tighter side spacing preserves source shapes and baseline while removing artboard margins", () => {
  const centered = geometry.transformOutline(outline, {
    scale: 1,
    dx: 550,
    dy: 0,
  });
  centered.metrics.advance_width = 1710;
  const before = structuredClone(centered);
  const fitted = geometry.fitOutlineSpacing(centered);
  assert.ok(fitted.metrics.advance_width < 1710);
  assert.equal(fitted.contours.length, before.contours.length);
  const delta = fitted.contours[0].start.x - before.contours[0].start.x;
  function assertPoint(source, target) {
    assert.equal(target.x, source.x + delta);
    assert.equal(target.y, source.y);
  }
  for (let i = 0; i < before.contours.length; i++) {
    assertPoint(before.contours[i].start, fitted.contours[i].start);
    for (let j = 0; j < before.contours[i].segments.length; j++) {
      const original = before.contours[i].segments[j];
      const result = fitted.contours[i].segments[j];
      assert.equal(result.type, original.type);
      for (const key of ["to", "c", "c1", "c2"])
        if (original[key]) assertPoint(original[key], result[key]);
    }
  }
  assert.equal(
    fitted.metrics.left_side_bearing,
    before.metrics.left_side_bearing + delta,
  );
  assert.deepEqual(centered, before);
  assert.deepEqual(geometry.fitOutlineSpacing(fitted), fitted);
  assert.throws(() => geometry.fitOutlineSpacing(centered, -1));
  assert.throws(() => geometry.fitOutlineSpacing(centered, Infinity));
});

test("canonical SVG keeps holes, curves and descenders in one baseline frame", () => {
  const before = structuredClone(outline);
  const path = geometry.outlinePath(outline);
  assert.ok(path.includes("M 40 -200"));
  assert.ok(path.includes("C 10 100 30 700 500 500"));
  assert.ok(path.includes("Q 550 100 40 -200"));
  assert.equal(path.split(" Z").length - 1, 2);
  assert.equal(geometry.outlineViewBox(outline), "-150 -950 1200 1300");
  const small = structuredClone(outline);
  small.contours = small.contours.map((contour) => ({
    ...contour,
    start: point(contour.start.x / 2, contour.start.y / 2),
    segments: contour.segments.map((segment) =>
      Object.fromEntries(
        Object.entries(segment).map(([key, value]) => [
          key,
          typeof value === "object" ? point(value.x / 2, value.y / 2) : value,
        ]),
      ),
    ),
  }));
  assert.equal(
    geometry.outlineViewBox(small),
    geometry.outlineViewBox(outline),
    "short letters are not individually enlarged",
  );
  assert.deepEqual(outline, before);
});

test("font-unit edits transform every control point uniformly without mutating the source", () => {
  const before = structuredClone(outline);
  const scaled = geometry.transformOutline(outline, {
    scale: 1.25,
    dx: 20,
    dy: -15,
  });
  assert.deepEqual(scaled.contours[0].start, point(70, -265));
  assert.deepEqual(scaled.contours[0].segments[0].c1, point(32.5, 110));
  assert.deepEqual(scaled.contours[0].segments[1].c, point(707.5, 110));
  assert.deepEqual(scaled.metrics, {
    advance_width: 750,
    left_side_bearing: 70,
  });
  const restored = geometry.transformOutline(scaled, {
    scale: 0.8,
    dx: -16,
    dy: 12,
  });
  assert.deepEqual(restored, outline);
  assert.deepEqual(outline, before);
  for (const scale of [0, -1, NaN, Infinity, 5])
    assert.throws(() =>
      geometry.transformOutline(outline, { scale, dx: 0, dy: 0 }),
    );
  const broken = structuredClone(outline);
  broken.contours[0].segments[0].c1.x = NaN;
  assert.throws(() => geometry.outlinePath(broken));
  const open = structuredClone(outline);
  open.contours[0].closed = false;
  assert.throws(() => geometry.outlinePath(open));
  const wrongFrame = structuredClone(outline);
  wrongFrame.coordinate_system.y_positive = "down";
  assert.throws(() => geometry.outlinePath(wrongFrame));
});

test("space renders metrics without a fake drawn glyph", () => {
  const space = { ...outline, codepoints: [32], contours: [] };
  assert.equal(geometry.outlinePath(space), "");
  assert.equal(
    geometry.transformOutline(space, { scale: 2, dx: 0, dy: 0 }).metrics
      .advance_width,
    1200,
  );
  assert.throws(() => geometry.outlinePath({ ...space, codepoints: [65] }));
});

test("private review transports conditional owner-authenticated actions separately from approval", async () => {
  const calls = [];
  const client = createExtractionClient(
    "https://api.example.test",
    async () => "owner-token",
    async (address, options) => {
      calls.push({ address: String(address), ...options });
      return new Response(
        JSON.stringify({
          id: "glyph",
          working_revision_id: "pending",
          active_revision_id: null,
        }),
        { status: 200, headers: { ETag: '"v8"' } },
      );
    },
  );
  const selected = await client.select(
    "project",
    "glyph",
    { candidate_id: "candidate", base_revision_id: null },
    '"v7"',
    "stable-select-key",
  );
  assert.equal(selected.etag, '"v8"');
  assert.equal(selected.data.active_revision_id, null);
  assert.equal(calls.length, 1, "select does not issue an approval request");
  assert.equal(calls[0].headers["If-Match"], '"v7"');
  assert.equal(calls[0].headers["Idempotency-Key"], "stable-select-key");
  assert.equal(calls[0].headers.Authorization, "Bearer owner-token");
  assert.equal(calls[0].credentials, "omit");
  assert.equal(calls[0].redirect, "error");
  await client.patch(
    "project",
    "glyph",
    { action: "approve", base_revision_id: "pending" },
    selected.etag,
  );
  assert.deepEqual(JSON.parse(calls[1].body), {
    action: "approve",
    base_revision_id: "pending",
  });
  assert.equal(calls[1].headers["If-Match"], '"v8"');
  assert.equal(
    client.publicTemplateUrl("/api/v1/templates/latin/1/0.pdf"),
    "https://api.example.test/api/v1/templates/latin/1/0.pdf",
  );
  assert.throws(() =>
    client.publicTemplateUrl(
      "https://other.example.test/api/v1/templates/latin.svg",
    ),
  );
  assert.throws(() =>
    client.publicTemplateUrl("/api/v1/storage/objects/private"),
  );
});

test("analysis preserves explicit observed-source assignment and never submits source bytes", async () => {
  const requests = [];
  const client = createExtractionClient(
    "https://api.example.test",
    async () => "owner-token",
    async (address, options) => {
      requests.push({ address: String(address), ...options });
      return new Response(JSON.stringify({ job_id: "job", state: "queued" }));
    },
  );
  const assignments = [
    {
      sample_id: "sample",
      codepoints: [103],
      capture_transform: {
        schema_version: 1,
        units_per_em: 1000,
        baseline_y: 0.75,
        units_per_px: 2,
        origin_x_px: 0,
        pen_width_px: 3.3,
      },
    },
  ];
  await client.analyze("project", ["sample"], assignments, "analysis-key");
  assert.deepEqual(JSON.parse(requests[0].body), {
    job_type: "analyze_samples",
    sample_ids: ["sample"],
    assignments,
  });
  assert.equal(requests[0].headers["Idempotency-Key"], "analysis-key");
  assert.equal(JSON.stringify(requests[0]).includes("pressure"), false);
  assert.equal(JSON.stringify(requests[0]).includes("stroke_document"), false);
  await client.analyze(
    "project",
    ["sample"],
    assignments,
    "round-key",
    undefined,
    "fixed_round_v1",
  );
  assert.deepEqual(JSON.parse(requests[1].body), {
    job_type: "analyze_samples",
    sample_ids: ["sample"],
    assignments,
    drawing_mode: "fixed_round_v1",
  });
});

test("revision conflicts stay typed and private server text is not exposed", async () => {
  const client = createExtractionClient(
    "https://api.example.test",
    async () => "owner-token",
    async () =>
      new Response(
        JSON.stringify({
          error: { code: "revision_conflict", message: "private source text" },
        }),
        { status: 412 },
      ),
  );
  await assert.rejects(
    client.patch(
      "p",
      "g",
      { action: "edit", base_revision_id: "r", outline },
      '"v1"',
    ),
    (error) =>
      error instanceof ExtractionError &&
      error.code === "revision_conflict" &&
      !error.message.includes("private source text"),
  );
});

test("private crop evidence uses owner-authenticated scoped capabilities and verifies bytes", async () => {
  const bytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
  const digest = Buffer.from(
    await crypto.subtle.digest("SHA-256", bytes),
  ).toString("hex");
  const ref = {
    artifact_id: "crop",
    sha256: `sha256:${digest}`,
    size_bytes: bytes.length,
    media_type: "image/png",
  };
  const calls = [];
  const client = createExtractionClient(
    "https://api.example.test",
    async () => "owner-token",
    async (address, options) => {
      calls.push({ address: String(address), ...options });
      return calls.length % 2
        ? new Response(
            JSON.stringify({
              method: "GET",
              url: "/api/v1/storage/objects/private-capability",
              headers: {},
            }),
          )
        : new Response(bytes, { headers: { "Content-Type": "image/png" } });
    },
  );
  const blob = await client.evidence(ref);
  assert.equal(blob.size, bytes.length);
  assert.equal(calls[0].headers.Authorization, "Bearer owner-token");
  assert.equal(calls[1].headers.Authorization, "Bearer owner-token");
  assert.equal(calls[1].redirect, "error");
  assert.equal(calls[1].referrerPolicy, "no-referrer");
  await assert.rejects(
    client.evidence({ ...ref, sha256: `sha256:${"f".repeat(64)}` }),
    (error) => error.code === "invalid_evidence",
  );
  const forged = createExtractionClient(
    "https://api.example.test",
    async () => "owner-token",
    async () =>
      new Response(
        JSON.stringify({
          method: "GET",
          url: "https://foreign.example.test/private",
          headers: {},
        }),
      ),
  );
  await assert.rejects(
    forged.evidence(ref),
    (error) => error.code === "invalid_capability",
  );
});

test("glyph pagination loads the full ASCII inventory and rejects cycling cursors", async () => {
  const calls = [];
  const client = createExtractionClient(
    "https://api.example.test",
    async () => "owner-token",
    async (url) => {
      calls.push(String(url));
      return new Response(
        JSON.stringify(
          calls.length === 1
            ? {
                items: Array.from({ length: 52 }, (_, id) => ({
                  id: String(id),
                })),
                next_cursor: "next",
              }
            : { items: [{ id: "space" }], next_cursor: null },
        ),
      );
    },
  );
  assert.equal((await client.glyphs("p")).length, 53);
  assert.ok(calls[1].includes("cursor=next"));
  const cycling = createExtractionClient(
    "https://api.example.test",
    async () => "owner-token",
    async () =>
      new Response(JSON.stringify({ items: [], next_cursor: "same" })),
  );
  await assert.rejects(
    cycling.glyphs("p"),
    (error) => error.code === "invalid_pagination",
  );
});

test("measured style and authentic alternate actions preserve immutable inputs and ETags", async () => {
  const calls = [];
  const client = createExtractionClient(
    "http://localhost:8000",
    async () => "owner-token",
    async (url, init) => {
      calls.push({
        url: String(url),
        init,
        body: init.body ? JSON.parse(init.body) : null,
      });
      return new Response(JSON.stringify({ id: "receipt" }), {
        status: 200,
        headers: { "Content-Type": "application/json", ETag: '"v8"' },
      });
    },
  );
  await client.measureStyle("project", "snapshot", "profile-key");
  await client.addAlternate(
    "project",
    "glyph",
    "approved-source",
    "base",
    '"v7"',
    "alternate-key",
  );
  await client.enableAlternate("project", "alternate", false, '"v8"');
  assert.deepEqual(calls[0].body, { snapshot_id: "snapshot" });
  assert.equal(calls[0].init.headers["Idempotency-Key"], "profile-key");
  assert.deepEqual(calls[1].body, {
    source_revision_id: "approved-source",
    base_revision_id: "base",
  });
  assert.equal(calls[1].init.headers["If-Match"], '"v7"');
  assert.equal(calls[1].init.headers["Idempotency-Key"], "alternate-key");
  assert.deepEqual(calls[2].body, { enabled: false });
  assert.equal(calls[2].init.headers["If-Match"], '"v8"');
  assert.ok(
    calls.every(
      ({ init }) =>
        init.headers.Authorization === "Bearer owner-token" &&
        init.cache === "no-store",
    ),
  );
});
