/** Observed browser input; canonical documents are created only after choosing a capture time origin. */
import type { components } from "../../packages/contracts/src/generated";

export const CANVAS_BRUSH_WIDTH = 3.3;

export type RawStrokeDocument = components["schemas"]["StrokeDocument"];
export type CaptureSize = components["schemas"]["CaptureSize"];
export type ObservedStroke = components["schemas"]["Stroke"];
type StrokePoint = components["schemas"]["StrokePoint"];
export type CapturePointerType = NonNullable<ObservedStroke["pointer_type"]>;

export type CaptureRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};
export type ObservedPointerInput = {
  clientX: number;
  clientY: number;
  timeStamp: number;
  pointerType: string;
  pressure?: number;
  tiltX?: number;
  tiltY?: number;
  twist?: number;
};

function finite(value: number): number {
  if (!Number.isFinite(value)) throw new Error("invalid_observed_capture");
  return value;
}

export function captureSize(
  rect: Pick<CaptureRect, "width" | "height">,
): CaptureSize {
  const width = Math.round(finite(rect.width));
  const height = Math.round(finite(rect.height));
  if (width < 1 || height < 1 || width > 32768 || height > 32768)
    throw new Error("invalid_capture_dimensions");
  return { width, height };
}

/** PointerEvent timeStamp and performance.timeOrigin share the navigation time base. */
export function observedTimeMs(
  eventTimeStamp: number,
  timeOriginMs: number,
): number {
  finite(eventTimeStamp);
  finite(timeOriginMs);
  // Older event implementations may already expose epoch milliseconds.
  const observed =
    eventTimeStamp >= timeOriginMs
      ? eventTimeStamp
      : eventTimeStamp + timeOriginMs;
  if (eventTimeStamp < 0 || timeOriginMs < 0 || !Number.isFinite(observed))
    throw new Error("invalid_observed_capture");
  return observed;
}

export function observedPointerPoint(
  event: ObservedPointerInput,
  rect: CaptureRect,
  timeOriginMs: number,
): StrokePoint {
  captureSize(rect);
  const point: StrokePoint = {
    x: Math.max(
      0,
      Math.min(1, (finite(event.clientX) - finite(rect.left)) / rect.width),
    ),
    y: Math.max(
      0,
      Math.min(1, (finite(event.clientY) - finite(rect.top)) / rect.height),
    ),
    t_ms: observedTimeMs(event.timeStamp, timeOriginMs),
  };
  // Mouse/touch defaults (including mouse pressure 0.5) are not observed pen measurements.
  if (event.pointerType === "pen") {
    if (
      typeof event.pressure === "number" &&
      Number.isFinite(event.pressure) &&
      event.pressure >= 0 &&
      event.pressure <= 1
    )
      point.pressure = event.pressure;
    if (
      typeof event.tiltX === "number" &&
      Number.isFinite(event.tiltX) &&
      Math.abs(event.tiltX) <= 90
    )
      point.tilt_x = event.tiltX;
    if (
      typeof event.tiltY === "number" &&
      Number.isFinite(event.tiltY) &&
      Math.abs(event.tiltY) <= 90
    )
      point.tilt_y = event.tiltY;
    if (
      typeof event.twist === "number" &&
      Number.isFinite(event.twist) &&
      event.twist >= 0 &&
      event.twist < 360
    )
      point.twist = event.twist;
  }
  return point;
}

export function newObservedStroke(
  pointerType: string,
  point: StrokePoint,
): ObservedStroke {
  return {
    stroke_id: crypto.randomUUID(),
    pointer_type: ["pen", "mouse", "touch", "keyboard"].includes(pointerType)
      ? (pointerType as CapturePointerType)
      : null,
    points: [{ ...point }],
  };
}

export type CapturedStrokeDocument = {
  document: RawStrokeDocument;
  /** Local time-base evidence; keep with the document through reload/undo/redo. */
  captureStartedAtMs: number;
};

export function appendObservedStroke(
  previous: RawStrokeDocument | null | undefined,
  stroke: ObservedStroke,
  size: CaptureSize,
  captureStartedAtMs?: number,
): CapturedStrokeDocument {
  if (previous?.timing === "unavailable_legacy")
    throw new Error("legacy_capture_requires_new_source");
  if (previous && (previous.parent_source_hash || previous.operations?.length))
    throw new Error("derived_capture_requires_new_source");
  if (previous && captureStartedAtMs === undefined)
    throw new Error("capture_time_origin_missing");
  if (
    !stroke.stroke_id ||
    !stroke.points.length ||
    stroke.points.length > 20000
  )
    throw new Error("invalid_observed_capture");
  if (previous?.strokes.some((item) => item.stroke_id === stroke.stroke_id))
    throw new Error("duplicate_stroke_id");
  const firstTime = stroke.points[0].t_ms;
  if (firstTime == null)
    throw new Error("observed_capture_requires_timestamps");
  const origin = finite(captureStartedAtMs ?? firstTime);
  if (origin < 0) throw new Error("invalid_observed_capture");
  let priorTime = previous?.strokes.at(-1)?.points.at(-1)?.t_ms ?? 0;
  const points = stroke.points.map((point) => {
    if (point.t_ms == null)
      throw new Error("observed_capture_requires_timestamps");
    const t_ms = finite(point.t_ms) - origin;
    if (t_ms < 0 || t_ms < priorTime)
      throw new Error("capture_clock_moved_backwards");
    if (
      !Number.isFinite(point.x) ||
      !Number.isFinite(point.y) ||
      point.x < 0 ||
      point.x > 1 ||
      point.y < 0 ||
      point.y > 1
    )
      throw new Error("invalid_observed_capture");
    priorTime = t_ms;
    return { ...point, t_ms };
  });
  const strokes = [
    ...(previous?.strokes ?? []).map((item) => ({
      ...item,
      points: item.points.map((point) => ({ ...point })),
    })),
    { ...stroke, points },
  ];
  if (
    strokes.length > 2000 ||
    strokes.reduce((total, item) => total + item.points.length, 0) > 100000
  )
    throw new Error("stroke_point_budget_exceeded");
  return {
    captureStartedAtMs: origin,
    document: {
      schema_version: 1,
      coordinate_system: {
        origin: "top_left",
        x_positive: "right",
        y_positive: "down",
        normalized: true,
      },
      // This is the source artboard. Responsive rendering transforms normalized coordinates into it.
      capture_size_px: previous?.capture_size_px
        ? { ...previous.capture_size_px }
        : captureSize(size),
      timing: "observed",
      strokes,
      parent_source_hash: null,
      operations: [],
    },
  };
}
