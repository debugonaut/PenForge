import assert from "node:assert/strict";
import { test } from "node:test";
import { createTypeScriptLoader } from "./helpers/typescript.mjs";

const load = createTypeScriptLoader(import.meta.url);
const photo = await import(await load("../src/lib/photo-regions.ts"));
const source = "11111111-1111-4111-8111-111111111111";
const digest = `sha256:${"a".repeat(64)}`;
const id = (index) =>
  `22222222-2222-4222-8222-${String(index).padStart(12, "0")}`;
const size = { width: 1200, height: 1600 };
const draft = (index, character = "A") => ({
  id: id(index),
  character,
  rect: { x: 100, y: 200, width: 80, height: 100 },
  baseline: 285,
});

test("original and oriented photo frames allow only exact dimensions or an EXIF side swap", () => {
  photo.validatePhotoFrame(size, size);
  photo.validatePhotoFrame(size, { width: 1600, height: 1200 });
  assert.throws(() =>
    photo.validatePhotoFrame(size, { width: 600, height: 800 }),
  );
  for (const invalid of [
    { width: 0, height: 1 },
    { width: 6001, height: 1 },
    { width: 4001, height: 4000 },
    { width: NaN, height: 1 },
  ])
    assert.throws(() => photo.validatePhotoSize(invalid));
});

test("pointer positions map a resized displayed photo back to the original oriented pixel frame", () => {
  const displayed = { left: 10, top: 20, width: 300, height: 400 };
  assert.deepEqual(photo.photoPoint(160, 220, displayed, size), {
    x: 600,
    y: 800,
  });
  assert.deepEqual(photo.photoPoint(-100, 900, displayed, size), {
    x: 0,
    y: 1600,
  });
  assert.deepEqual(
    photo.photoRectangle({ x: 180.2, y: 300.8 }, { x: 100.7, y: 200.2 }, size),
    { x: 100, y: 200, width: 81, height: 101 },
  );
  assert.throws(() => photo.photoPoint(0, 0, { ...displayed, width: 0 }, size));
  assert.throws(() =>
    photo.photoRectangle({ x: 5, y: 5 }, { x: 5, y: 5 }, size),
  );
});

test("one photo batch binds every letter to the immutable source and one measured common scale", () => {
  const drafts = [draft(1, "A"), draft(2, "g"), draft(3, "A"), draft(4, "?")];
  const before = structuredClone(drafts);
  const rows = photo.photoAssignments(source, digest, size, drafts, 100, 180);
  assert.deepEqual(drafts, before);
  assert.deepEqual(
    rows.map((row) => row.codepoints),
    [[65], [103], [65], [63]],
  );
  assert.ok(
    rows.every(
      (row) =>
        row.sample_id === source &&
        row.source_sha256 === digest &&
        row.source_width === 1200 &&
        row.source_height === 1600 &&
        row.units_per_px === 7,
    ),
  );
  assert.equal(
    rows[1].baseline_y_px,
    285,
    "baseline remains an absolute source-frame coordinate, including descenders",
  );
  rows[0].rect.x = 500;
  assert.equal(
    drafts[0].rect.x,
    100,
    "request rectangles cannot mutate the user's draft",
  );
});

test("photo assignments refuse incomplete labels, unsupported characters and unbounded crop requests", () => {
  const build = (rows, height = 100, threshold = 180) =>
    photo.photoAssignments(source, digest, size, rows, height, threshold);
  for (const label of ["", " ", "AB", "क", "😀", "\n"])
    assert.throws(() => build([draft(1, label)]));
  assert.throws(() => build([]));
  assert.throws(() => build([draft(1), draft(1)]));
  assert.throws(() => build([{ ...draft(1), baseline: Infinity }]));
  assert.throws(() => build([{ ...draft(1), baseline: 1601 }]));
  assert.throws(() =>
    build([{ ...draft(1), rect: { x: 1190, y: 200, width: 80, height: 100 } }]),
  );
  assert.throws(() =>
    build([{ ...draft(1), rect: { x: 1.5, y: 200, width: 80, height: 100 } }]),
  );
  for (const height of [0, 6, NaN, 1601])
    assert.throws(() => build([draft(1)], height));
  for (const threshold of [-1, 256, 0.5, NaN])
    assert.throws(() => build([draft(1)], 100, threshold));
  assert.throws(() =>
    photo.photoAssignments(source, "unverified", size, [draft(1)], 100, 180),
  );
  assert.throws(() =>
    build(Array.from({ length: 97 }, (_, index) => draft(index))),
  );
  const huge = Array.from({ length: 3 }, (_, index) => ({
    ...draft(index),
    rect: { x: 0, y: 0, width: 2000, height: 2000 },
  }));
  assert.throws(() =>
    photo.photoAssignments(
      source,
      digest,
      { width: 4000, height: 4000 },
      huge,
      100,
      180,
    ),
  );
});

test("ink masks preserve dots and holes, flag edge contact and use the worker's strict threshold", () => {
  const rgba = new Uint8ClampedArray(5 * 5 * 4).fill(255);
  const pixel = (x, y, color) => rgba.set(color, (y * 5 + x) * 4);
  pixel(2, 2, [0, 0, 0, 255]);
  let result = photo.photoInkMask(rgba, 5, 5, 180);
  assert.equal(result.ink, 1);
  assert.equal(result.borderInk, false);
  assert.equal(result.pixels[(2 * 5 + 2) * 4], 0);
  assert.equal(result.pixels[(2 * 5 + 1) * 4], 255);
  pixel(0, 2, [0, 0, 0, 255]);
  result = photo.photoInkMask(rgba, 5, 5, 180);
  assert.equal(result.ink, 2);
  assert.equal(result.borderInk, true);
  const equality = Uint8ClampedArray.of(180, 180, 180, 255);
  assert.equal(photo.photoInkMask(equality, 1, 1, 180).ink, 0);
  assert.equal(photo.photoInkMask(equality, 1, 1, 181).ink, 1);
});

test("transparent ink composites over white with the same integer luma as the worker", () => {
  const rgba = Uint8ClampedArray.of(0, 0, 0, 0, 0, 0, 0, 128, 255, 0, 0, 255);
  const before = rgba.slice();
  const result = photo.photoInkMask(rgba, 3, 1, 128);
  assert.equal(
    result.ink,
    2,
    "transparent black is white; half-opacity black and red are below 128",
  );
  assert.deepEqual(rgba, before);
  assert.deepEqual(
    [...result.pixels],
    [255, 255, 255, 255, 0, 0, 0, 255, 0, 0, 0, 255],
  );
  assert.throws(() => photo.photoInkMask(rgba, 2, 2, 180));
});
