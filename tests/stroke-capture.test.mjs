import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

const moduleUrl = createTypeScriptLoader(import.meta.url);
const capture = await import(await moduleUrl("../src/lib/stroke-capture.ts"));
const rect = { left: 100, top: 200, width: 700, height: 290 };
const epoch = 1700000000000;

function stroke(pointerType, start, end) {
  const first = capture.observedPointerPoint(
    { clientX: 170, clientY: 229, timeStamp: start, pointerType },
    rect,
    epoch,
  );
  const result = capture.newObservedStroke(pointerType, first);
  result.points.push(
    capture.observedPointerPoint(
      { clientX: 240, clientY: 258, timeStamp: end, pointerType },
      rect,
      epoch,
    ),
  );
  return result;
}

test("pointer coordinates use actual CSS dimensions and only pen input carries observed pen metadata", () => {
  const event = {
    clientX: 170,
    clientY: 229,
    timeStamp: 10,
    pointerType: "mouse",
    pressure: 0.5,
    tiltX: 0,
    tiltY: 0,
    twist: 0,
  };
  const mouse = capture.observedPointerPoint(event, rect, epoch);
  assert.deepEqual(mouse, { x: 0.1, y: 0.1, t_ms: epoch + 10 });
  const touch = capture.observedPointerPoint(
    { ...event, pointerType: "touch" },
    rect,
    epoch,
  );
  assert.equal("pressure" in touch, false);
  const pen = capture.observedPointerPoint(
    {
      ...event,
      pointerType: "pen",
      pressure: 0,
      tiltX: -20,
      tiltY: 14,
      twist: 48,
    },
    rect,
    epoch,
  );
  assert.deepEqual(pen, {
    x: 0.1,
    y: 0.1,
    t_ms: epoch + 10,
    pressure: 0,
    tilt_x: -20,
    tilt_y: 14,
    twist: 48,
  });
  const unsupported = capture.observedPointerPoint(
    { clientX: 170, clientY: 229, timeStamp: 10, pointerType: "pen" },
    rect,
    epoch,
  );
  assert.equal("pressure" in unsupported, false);
  assert.equal("tilt_x" in unsupported, false);
  assert.deepEqual(capture.captureSize({ width: 699.6, height: 290.2 }), {
    width: 700,
    height: 290,
  });
});

test("capture timestamps remain observed across remount/reload and preserve the source artboard", () => {
  const original = stroke("mouse", 100, 120);
  const before = structuredClone(original);
  const first = capture.appendObservedStroke(
    null,
    original,
    capture.captureSize(rect),
  );
  assert.deepEqual(original, before);
  assert.equal(first.captureStartedAtMs, epoch + 100);
  assert.deepEqual(
    first.document.strokes[0].points.map((point) => point.t_ms),
    [0, 20],
  );
  assert.equal(first.document.timing, "observed");
  // Reload has a new navigation timeOrigin. Persisted origin retains the actual observed gap.
  const next = capture.newObservedStroke("keyboard", {
    x: 0.3,
    y: 0.4,
    t_ms: capture.observedTimeMs(40, epoch + 10000),
  });
  next.points.push({
    x: 0.4,
    y: 0.4,
    t_ms: capture.observedTimeMs(80, epoch + 10000),
  });
  const recovered = JSON.parse(JSON.stringify(first));
  const second = capture.appendObservedStroke(
    recovered.document,
    next,
    { width: 390, height: 290 },
    recovered.captureStartedAtMs,
  );
  assert.deepEqual(
    second.document.strokes[1].points.map((point) => point.t_ms),
    [9940, 9980],
  );
  assert.deepEqual(second.document.capture_size_px, {
    width: 700,
    height: 290,
  });
  assert.equal(
    second.document.strokes[0].stroke_id,
    first.document.strokes[0].stroke_id,
  );
  assert.notEqual(next.stroke_id, original.stroke_id);
  assert.equal("pressure" in second.document.strokes[1].points[0], false);
  assert.equal(
    first.document.strokes.length,
    1,
    "appending creates a new document without altering its source",
  );
  assert.equal(
    capture.observedTimeMs(epoch + 200, epoch),
    epoch + 200,
    "epoch event clocks are not added twice",
  );
});

test("backward clocks, duplicate IDs, mixed legacy metadata and missing time origins fail without fabricating data", () => {
  const first = capture.appendObservedStroke(
    null,
    stroke("mouse", 100, 120),
    capture.captureSize(rect),
  );
  const backward = stroke("mouse", 110, 115);
  assert.throws(
    () =>
      capture.appendObservedStroke(
        first.document,
        backward,
        capture.captureSize(rect),
        first.captureStartedAtMs,
      ),
    /capture_clock_moved_backwards/,
  );
  assert.throws(
    () =>
      capture.appendObservedStroke(
        first.document,
        stroke("touch", 130, 140),
        capture.captureSize(rect),
      ),
    /capture_time_origin_missing/,
  );
  assert.throws(
    () =>
      capture.appendObservedStroke(
        first.document,
        {
          ...stroke("pen", 130, 140),
          stroke_id: first.document.strokes[0].stroke_id,
        },
        capture.captureSize(rect),
        first.captureStartedAtMs,
      ),
    /duplicate_stroke_id/,
  );
  const legacy = {
    ...first.document,
    timing: "unavailable_legacy",
    capture_size_px: null,
    strokes: [
      {
        stroke_id: "legacy-0",
        pointer_type: null,
        points: [{ x: 0.2, y: 0.3, t_ms: null }],
      },
    ],
  };
  const bytes = JSON.stringify(legacy);
  assert.throws(
    () =>
      capture.appendObservedStroke(
        legacy,
        stroke("pen", 130, 140),
        capture.captureSize(rect),
      ),
    /legacy_capture_requires_new_source/,
  );
  assert.equal(JSON.stringify(legacy), bytes);
  assert.throws(
    () =>
      capture.appendObservedStroke(
        null,
        { stroke_id: "missing-time", points: [{ x: 0, y: 0 }] },
        capture.captureSize(rect),
      ),
    /requires_timestamps/,
  );
  assert.throws(
    () =>
      capture.appendObservedStroke(
        null,
        {
          stroke_id: "bad-order",
          points: [
            { x: 0, y: 0, t_ms: epoch + 10 },
            { x: 0, y: 0, t_ms: epoch },
          ],
        },
        capture.captureSize(rect),
      ),
    /capture_clock_moved_backwards/,
  );
});
