import type { CanonicalOutline } from "./api-boundary";

type Point = { x: number; y: number };

function points(outline: CanonicalOutline): Point[] {
  return outline.contours.flatMap((contour) => [
    contour.start,
    ...contour.segments.flatMap((segment) => [
      segment.to,
      ...(segment.type === "quadratic"
        ? [segment.c]
        : segment.type === "cubic"
          ? [segment.c1, segment.c2]
          : []),
    ]),
  ]);
}

/** Fail closed before producing SVG; text/font fallbacks would conceal missing geometry. */
export function assertRenderableOutline(outline: CanonicalOutline): void {
  if (
    outline.schema_version !== 1 ||
    outline.coordinate_system.origin !== "baseline" ||
    outline.coordinate_system.x_positive !== "right" ||
    outline.coordinate_system.y_positive !== "up" ||
    outline.fill_rule !== "evenodd" ||
    !Number.isInteger(outline.units_per_em) ||
    outline.units_per_em < 16 ||
    outline.units_per_em > 16384 ||
    outline.contours.length > 128
  )
    throw new Error("unsupported_canonical_outline");
  const all = points(outline);
  const bound = outline.units_per_em * 8;
  if (
    all.length > 8192 ||
    all.some(
      (point) =>
        !Number.isFinite(point.x) ||
        !Number.isFinite(point.y) ||
        Math.abs(point.x) > bound ||
        Math.abs(point.y) > bound,
    ) ||
    !Number.isFinite(outline.metrics.advance_width) ||
    outline.metrics.advance_width < 0 ||
    outline.metrics.advance_width > bound ||
    !Number.isFinite(outline.metrics.left_side_bearing) ||
    Math.abs(outline.metrics.left_side_bearing) > bound
  )
    throw new Error("invalid_canonical_geometry");
  if (
    (!outline.contours.length &&
      !(outline.codepoints.length === 1 && outline.codepoints[0] === 32)) ||
    outline.contours.some(
      (contour) =>
        !contour.closed ||
        !contour.segments.length ||
        contour.segments.some(
          (segment) => !["line", "quadratic", "cubic"].includes(segment.type),
        ) ||
        contour.segments.at(-1)?.to.x !== contour.start.x ||
        contour.segments.at(-1)?.to.y !== contour.start.y,
    )
  )
    throw new Error("invalid_canonical_contour");
}

export function outlinePath(outline: CanonicalOutline): string {
  assertRenderableOutline(outline);
  const point = ({ x, y }: Point) => `${x} ${y}`;
  return outline.contours
    .map(
      (contour) =>
        `M ${point(contour.start)} ${contour.segments
          .map((segment) =>
            segment.type === "line"
              ? `L ${point(segment.to)}`
              : segment.type === "quadratic"
                ? `Q ${point(segment.c)} ${point(segment.to)}`
                : `C ${point(segment.c1)} ${point(segment.c2)} ${point(segment.to)}`,
          )
          .join(" ")} Z`,
    )
    .join(" ");
}

/** A shared em frame preserves relative size; expand only for actual overshoots. */
export function outlineViewBox(outline: CanonicalOutline): string {
  assertRenderableOutline(outline);
  const all = points(outline);
  const em = outline.units_per_em;
  const minX = Math.min(-em * 0.1, ...all.map((point) => point.x));
  const maxX = Math.max(
    em,
    outline.metrics.advance_width,
    ...all.map((point) => point.x),
  );
  const minY = Math.min(-em * 0.3, ...all.map((point) => point.y));
  const maxY = Math.max(em * 0.9, ...all.map((point) => point.y));
  const pad = em * 0.05;
  return `${minX - pad} ${-maxY - pad} ${maxX - minX + pad * 2} ${maxY - minY + pad * 2}`;
}

/** These are outline edits in font units, never CSS presentation transforms. */
export function transformOutline(
  outline: CanonicalOutline,
  edit: { scale: number; dx: number; dy: number },
): CanonicalOutline {
  assertRenderableOutline(outline);
  if (outline.indic)
    throw new Error("review_mark_positioning_before_transform");
  if (
    !Object.values(edit).every(Number.isFinite) ||
    edit.scale <= 0 ||
    edit.scale > 4
  )
    throw new Error("unsupported_canonical_transform");
  const point = ({ x, y }: Point): Point => ({
    x: x * edit.scale + edit.dx,
    y: y * edit.scale + edit.dy,
  });
  const result: CanonicalOutline = {
    ...outline,
    coordinate_system: { ...outline.coordinate_system },
    codepoints: [...outline.codepoints],
    metrics: {
      advance_width: outline.metrics.advance_width * edit.scale,
      left_side_bearing:
        outline.metrics.left_side_bearing * edit.scale + edit.dx,
    },
    contours: outline.contours.map((contour) => ({
      ...contour,
      start: point(contour.start),
      segments: contour.segments.map((segment) =>
        segment.type === "line"
          ? { ...segment, to: point(segment.to) }
          : segment.type === "quadratic"
            ? { ...segment, c: point(segment.c), to: point(segment.to) }
            : {
                ...segment,
                c1: point(segment.c1),
                c2: point(segment.c2),
                to: point(segment.to),
              },
      ),
    })),
  };
  assertRenderableOutline(result);
  return result;
}
