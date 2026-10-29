import { graphemeStrings } from "../../packages/contracts/src/unicode";

export const WRITING_TEXT_FIELDS = [
  "title",
  "recipient",
  "body",
  "signature",
] as const;
export type WritingTextField = (typeof WRITING_TEXT_FIELDS)[number];
export type WritingSelection = {
  field: WritingTextField;
  start: number;
  end: number;
};
export type WritingTextValues = Record<WritingTextField, string>;
export const WRITING_STYLE_LIMIT = 128;
export const WRITING_STYLE_BOUNDS = {
  fontSize: [12, 120],
  weight: [400, 700],
  spacing: [-1, 16],
  baselineShift: [-24, 24],
} as const;
export type WritingStyleKey = keyof typeof WRITING_STYLE_BOUNDS;
export type WritingStyle = Partial<Record<WritingStyleKey, number>>;
export type WritingTextStyle = WritingSelection & WritingStyle;
export type ResolvedWritingStyle = Required<WritingStyle>;
type Measure = (text: string, size: number, weight: number) => number;
const keys = Object.keys(WRITING_STYLE_BOUNDS) as WritingStyleKey[];
const sameStyle = (a: WritingStyle, b: WritingStyle) =>
  keys.every((key) => a[key] === b[key]);

/** Browser selections use UTF-16. Snap outwards to our pinned Unicode clusters. */
export function writingSelection(
  values: WritingTextValues,
  selection: WritingSelection,
): WritingSelection | null {
  const value = values[selection.field];
  if (
    typeof value !== "string" ||
    !Number.isInteger(selection.start) ||
    !Number.isInteger(selection.end)
  )
    return null;
  const left = Math.max(0, Math.min(value.length, selection.start));
  const right = Math.max(left, Math.min(value.length, selection.end));
  if (left === right) return null;
  let offset = 0,
    start = left,
    end = right;
  for (const cluster of graphemeStrings(value)) {
    const next = offset + cluster.length;
    if (offset <= left && left < next) start = offset;
    if (offset < right && right <= next) end = next;
    offset = next;
  }
  return { field: selection.field, start, end };
}

function compact(ranges: WritingTextStyle[]): WritingTextStyle[] {
  const result: WritingTextStyle[] = [];
  for (const range of ranges.sort(
    (a, b) =>
      WRITING_TEXT_FIELDS.indexOf(a.field) -
        WRITING_TEXT_FIELDS.indexOf(b.field) || a.start - b.start,
  )) {
    if (
      range.start >= range.end ||
      !keys.some((key) => range[key] !== undefined)
    )
      continue;
    const last = result.at(-1);
    if (
      last?.field === range.field &&
      last.end === range.start &&
      sameStyle(last, range)
    )
      last.end = range.end;
    else result.push({ ...range });
  }
  return result;
}

/** Recovery is fail-closed per range; malformed/overlapping saved styles are omitted. */
export function normalizeWritingStyles(
  value: unknown,
  values: WritingTextValues,
): WritingTextStyle[] {
  if (!Array.isArray(value) || value.length > WRITING_STYLE_LIMIT) return [];
  const result: WritingTextStyle[] = [];
  for (const candidate of value) {
    if (
      !candidate ||
      typeof candidate !== "object" ||
      !WRITING_TEXT_FIELDS.includes(candidate.field)
    )
      continue;
    const selection = writingSelection(values, candidate);
    if (
      !selection ||
      selection.start !== candidate.start ||
      selection.end !== candidate.end
    )
      continue;
    const style: WritingStyle = {};
    let valid = true;
    for (const key of keys)
      if (candidate[key] !== undefined) {
        const number = candidate[key];
        const [min, max] = WRITING_STYLE_BOUNDS[key];
        if (
          typeof number !== "number" ||
          !Number.isFinite(number) ||
          number < min ||
          number > max
        )
          valid = false;
        else style[key] = number;
      }
    if (
      !valid ||
      result.some(
        (range) =>
          range.field === selection.field &&
          range.start < selection.end &&
          range.end > selection.start,
      )
    )
      continue;
    result.push({ ...selection, ...style });
  }
  return compact(result);
}

/** Apply only the supplied properties; preserve other overrides inside the range. */
export function styleWritingSelection(
  ranges: readonly WritingTextStyle[],
  selection: WritingSelection,
  patch: WritingStyle | null,
): WritingTextStyle[] {
  const other = ranges
    .filter(
      (range) =>
        range.field !== selection.field ||
        range.end <= selection.start ||
        range.start >= selection.end,
    )
    .map((range) => ({ ...range }));
  const crossing = ranges.filter(
    (range) =>
      range.field === selection.field &&
      range.start < selection.end &&
      range.end > selection.start,
  );
  const cuts = [
    ...new Set([
      selection.start,
      selection.end,
      ...crossing.flatMap((range) => [
        Math.max(selection.start, range.start),
        Math.min(selection.end, range.end),
      ]),
    ]),
  ].sort((a, b) => a - b);
  for (const range of crossing) {
    if (range.start < selection.start)
      other.push({ ...range, end: selection.start });
    if (range.end > selection.end)
      other.push({ ...range, start: selection.end });
  }
  if (patch)
    for (let index = 0; index < cuts.length - 1; index++) {
      const start = cuts[index],
        end = cuts[index + 1];
      const previous = crossing.find(
        (range) => range.start <= start && range.end >= end,
      );
      const style: WritingStyle = {};
      for (const key of keys)
        if (previous?.[key] !== undefined) style[key] = previous[key];
      other.push({ field: selection.field, start, end, ...style, ...patch });
    }
  const result = compact(other);
  if (result.length > WRITING_STYLE_LIMIT)
    throw new Error(
      "This page has reached its selection limit. Reset an existing selection first.",
    );
  return result;
}

/** One text replacement; inserted text inherits only when inside a styled run. */
export function rebaseWritingStyles(
  ranges: readonly WritingTextStyle[],
  field: WritingTextField,
  before: string,
  after: string,
): WritingTextStyle[] {
  if (before === after) return ranges.map((range) => ({ ...range }));
  const oldClusters = graphemeStrings(before),
    newClusters = graphemeStrings(after);
  let prefix = 0,
    suffix = 0;
  while (
    prefix < Math.min(oldClusters.length, newClusters.length) &&
    oldClusters[prefix] === newClusters[prefix]
  )
    prefix++;
  while (
    suffix < Math.min(oldClusters.length, newClusters.length) - prefix &&
    oldClusters.at(-1 - suffix) === newClusters.at(-1 - suffix)
  )
    suffix++;
  const start = oldClusters.slice(0, prefix).join("").length;
  const end =
    before.length -
    oldClusters.slice(oldClusters.length - suffix).join("").length;
  const inserted =
    after.length -
    start -
    newClusters.slice(newClusters.length - suffix).join("").length;
  const delta = inserted - (end - start);
  const result: WritingTextStyle[] = [];
  for (const range of ranges) {
    if (range.field !== field || range.end <= start) result.push({ ...range });
    else if (range.start >= end)
      result.push({
        ...range,
        start: range.start + delta,
        end: range.end + delta,
      });
    else {
      if (range.start < start) result.push({ ...range, end: start });
      if (range.start < start && range.end > end && inserted)
        result.push({ ...range, start, end: start + inserted });
      if (range.end > end)
        result.push({
          ...range,
          start: start + inserted,
          end: range.end + delta,
        });
    }
  }
  return compact(result);
}

export function resolvedWritingStyle(
  ranges: readonly WritingTextStyle[],
  field: WritingTextField,
  offset: number,
  base: ResolvedWritingStyle,
): ResolvedWritingStyle {
  const range = ranges.find(
    (item) => item.field === field && item.start <= offset && offset < item.end,
  );
  const result = { ...base };
  for (const key of keys)
    if (range?.[key] !== undefined) result[key] = range[key];
  return result;
}

export type WritingRun = {
  text: string;
  start: number;
  end: number;
  style: ResolvedWritingStyle;
  x: number;
  width: number;
};
export type WritingLine = {
  text: string;
  start: number;
  end: number;
  runs: WritingRun[];
  width: number;
  above: number;
  below: number;
  offsetY: number;
};

export function writingRunWidth(
  text: string,
  style: ResolvedWritingStyle,
  measure: Measure,
): number {
  return (
    measure(text, style.fontSize, style.weight) +
    Math.max(0, graphemeStrings(text).length - 1) * style.spacing
  );
}

/** Preserve source offsets through wrapping, including repeated spaces and newlines. */
export function layoutWritingText(
  value: string,
  field: WritingTextField,
  ranges: readonly WritingTextStyle[],
  base: ResolvedWritingStyle,
  maximumWidth: number,
  step: number,
  measure: Measure,
): WritingLine[] {
  const lines: WritingLine[] = [];
  let paragraphStart = 0;
  for (const paragraph of value.split("\n")) {
    const clusters = graphemeStrings(paragraph);
    const offsets = [paragraphStart];
    for (const cluster of clusters)
      offsets.push(offsets.at(-1)! + cluster.length);
    const clusterStyles = clusters.map((_, index) =>
      resolvedWritingStyle(ranges, field, offsets[index], base),
    );
    const makeLine = (from: number, to: number): WritingLine => {
      const runs: WritingRun[] = [];
      for (let index = from; index < to; index++) {
        const style = clusterStyles[index];
        const last = runs.at(-1);
        if (last && sameStyle(last.style, style)) {
          last.text += clusters[index];
          last.end = offsets[index + 1];
        } else
          runs.push({
            text: clusters[index],
            start: offsets[index],
            end: offsets[index + 1],
            style,
            x: 0,
            width: 0,
          });
      }
      let width = 0;
      for (const [index, run] of runs.entries()) {
        run.x = width;
        run.width = writingRunWidth(run.text, run.style, measure);
        // A selected space gets a real advance even when it ends a run/line.
        if (
          index < runs.length - 1 ||
          (/\s$/u.test(run.text) &&
            ranges.some(
              (range) =>
                range.field === field &&
                range.start < run.end &&
                range.end >= run.end &&
                range.spacing !== undefined,
            ))
        )
          run.width += run.style.spacing;
        width += run.width;
      }
      return {
        text: clusters.slice(from, to).join(""),
        start: offsets[from],
        end: offsets[to],
        runs,
        width,
        above: Math.max(
          base.fontSize * 1.08,
          ...runs.map(
            (run) => run.style.fontSize * 1.08 + run.style.baselineShift,
          ),
        ),
        below: Math.max(
          base.fontSize * 0.4,
          ...runs.map(
            (run) => run.style.fontSize * 0.4 - run.style.baselineShift,
          ),
        ),
        offsetY: 0,
      };
    };
    if (!clusters.length) lines.push(makeLine(0, 0));
    for (let from = 0; from < clusters.length;) {
      let to = from;
      while (
        to < clusters.length &&
        makeLine(from, to + 1).width <= maximumWidth
      )
        to++;
      if (to === from) to++;
      if (to < clusters.length) {
        const space = clusters.slice(from, to).lastIndexOf(" ");
        if (space > 0) to = from + space;
      }
      let visibleEnd = to;
      // Preserve styled gaps; retain the established trim at ordinary wraps.
      while (
        visibleEnd > from &&
        /\s/u.test(clusters[visibleEnd - 1]) &&
        !ranges.some(
          (range) =>
            range.field === field &&
            range.start < offsets[visibleEnd] &&
            range.end > offsets[visibleEnd - 1],
        )
      )
        visibleEnd--;
      lines.push(makeLine(from, visibleEnd));
      from = to;
      while (
        clusters[from] === " " &&
        !ranges.some(
          (range) =>
            range.field === field &&
            range.start <= offsets[from] &&
            range.end > offsets[from],
        )
      )
        from++;
    }
    paragraphStart += paragraph.length + 1;
  }
  lines.forEach((line, index) => {
    line.offsetY = index
      ? lines[index - 1].offsetY +
        step +
        (lines[index - 1].below - base.fontSize * 0.4) +
        (line.above - base.fontSize * 1.08)
      : line.above - base.fontSize * 1.08;
  });
  return lines;
}
