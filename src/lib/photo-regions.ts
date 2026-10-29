/** Bounds and source coordinates for explicitly assigned letters from an existing photo. */
import type { ImageRegionAssignment } from "./extraction-client";
export type PhotoSize = { width: number; height: number };
export type PhotoRect = { x: number; y: number; width: number; height: number };
export type PhotoRegionDraft = {
  id: string;
  character: string;
  rect: PhotoRect;
  baseline: number;
};
export type PhotoRegionAssignment = ImageRegionAssignment;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const integer = (value: number) => Number.isSafeInteger(value);

export function validatePhotoSize(size: PhotoSize) {
  if (
    !integer(size.width) ||
    !integer(size.height) ||
    size.width < 1 ||
    size.height < 1 ||
    size.width > 6000 ||
    size.height > 6000 ||
    size.width * size.height > 16_000_000
  )
    throw new Error(
      "Use an image up to 6000 pixels per side and 16 million pixels in total.",
    );
}

/** Asset metadata precedes EXIF orientation; a quarter-turn swaps the exact sides. */
export function validatePhotoFrame(original: PhotoSize, oriented: PhotoSize) {
  validatePhotoSize(original);
  validatePhotoSize(oriented);
  if (!(
    (original.width === oriented.width &&
      original.height === oriented.height) ||
    (original.width === oriented.height && original.height === oriented.width)
  ))
    throw new Error(
      "The preview does not match the original image dimensions. Reopen the saved page.",
    );
}

export function validatePhotoRect(rect: PhotoRect, size: PhotoSize) {
  validatePhotoSize(size);
  if (
    ![rect.x, rect.y, rect.width, rect.height].every(integer) ||
    rect.x < 0 ||
    rect.y < 0 ||
    rect.width < 1 ||
    rect.height < 1 ||
    rect.x + rect.width > size.width ||
    rect.y + rect.height > size.height ||
    rect.width * rect.height > 4_000_000
  )
    throw new Error(
      "Keep a non-empty letter box inside the image, under four million pixels.",
    );
}

export function photoPoint(
  x: number,
  y: number,
  displayed: { left: number; top: number; width: number; height: number },
  size: PhotoSize,
) {
  validatePhotoSize(size);
  if (
    !Number.isFinite(x) ||
    !Number.isFinite(y) ||
    !Number.isFinite(displayed.left) ||
    !Number.isFinite(displayed.top) ||
    !Number.isFinite(displayed.width) ||
    !Number.isFinite(displayed.height) ||
    displayed.width <= 0 ||
    displayed.height <= 0
  )
    throw new Error("The image is not ready for selection yet.");
  return {
    x: Math.max(
      0,
      Math.min(
        size.width,
        ((x - displayed.left) * size.width) / displayed.width,
      ),
    ),
    y: Math.max(
      0,
      Math.min(
        size.height,
        ((y - displayed.top) * size.height) / displayed.height,
      ),
    ),
  };
}

export function photoRectangle(
  start: { x: number; y: number },
  end: { x: number; y: number },
  size: PhotoSize,
): PhotoRect {
  const left = Math.floor(Math.min(start.x, end.x));
  const top = Math.floor(Math.min(start.y, end.y));
  const right = Math.ceil(Math.max(start.x, end.x));
  const bottom = Math.ceil(Math.max(start.y, end.y));
  const rect = { x: left, y: top, width: right - left, height: bottom - top };
  validatePhotoRect(rect, size);
  return rect;
}

export function photoAssignments(
  sampleId: string,
  sourceSha256: string,
  size: PhotoSize,
  drafts: PhotoRegionDraft[],
  capitalHeight: number,
  threshold: number,
): PhotoRegionAssignment[] {
  validatePhotoSize(size);
  if (!uuid.test(sampleId) || !/^sha256:[0-9a-f]{64}$/.test(sourceSha256))
    throw new Error("Reopen the saved page to verify its source.");
  if (!drafts.length || drafts.length > 96)
    throw new Error("Choose between 1 and 96 letters for one batch.");
  const units = 700 / capitalHeight;
  if (
    !Number.isFinite(capitalHeight) ||
    capitalHeight <= 0 ||
    capitalHeight > size.height ||
    !Number.isFinite(units) ||
    units > 100
  )
    throw new Error(
      "Set the height of a capital letter to at least 7 pixels, within the image.",
    );
  if (!integer(threshold) || threshold < 0 || threshold > 255)
    throw new Error("Choose an ink threshold from 0 to 255.");
  const ids = new Set<string>();
  let pixels = 0;
  return drafts.map((draft) => {
    validatePhotoRect(draft.rect, size);
    const points = Array.from(draft.character, (value) =>
      value.codePointAt(0)!,
    );
    if (points.length !== 1 || points[0] < 33 || points[0] > 126)
      throw new Error(
        "Give each box one English letter, number or punctuation mark. Space is added in Review.",
      );
    if (!uuid.test(draft.id) || ids.has(draft.id))
      throw new Error("Each selected letter needs its own box.");
    ids.add(draft.id);
    if (
      !Number.isFinite(draft.baseline) ||
      draft.baseline < 0 ||
      draft.baseline > size.height
    )
      throw new Error("Keep each baseline inside the image.");
    pixels += draft.rect.width * draft.rect.height;
    if (pixels > 8_000_000)
      throw new Error(
        "This batch is too large. Use tighter letter boxes or process fewer letters together.",
      );
    return {
      region_id: draft.id,
      sample_id: sampleId,
      source_sha256: sourceSha256,
      source_width: size.width,
      source_height: size.height,
      rect: { ...draft.rect },
      codepoints: points,
      baseline_y_px: draft.baseline,
      units_per_px: units,
      ink_threshold: threshold,
    };
  });
}

export function samePhotoAssignment(
  value: unknown,
  expected: PhotoRegionAssignment,
) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== Object.keys(expected).length) return false;
  for (const key of [
    "region_id",
    "sample_id",
    "source_sha256",
    "source_width",
    "source_height",
    "baseline_y_px",
    "units_per_px",
    "ink_threshold",
  ] as const)
    if (row[key] !== expected[key]) return false;
  if (
    !Array.isArray(row.codepoints) ||
    row.codepoints.length !== expected.codepoints.length ||
    row.codepoints.some((value, index) => value !== expected.codepoints[index])
  )
    return false;
  if (
    !row.rect ||
    typeof row.rect !== "object" ||
    Array.isArray(row.rect) ||
    Object.keys(row.rect).length !== 4
  )
    return false;
  return (["x", "y", "width", "height"] as const).every(
    (key) => (row.rect as Record<string, unknown>)[key] === expected.rect[key],
  );
}

/** A successful job is complete processing, not proof that every crop produced a glyph. */
export function photoBatchOutcome(
  quality: unknown,
  expected: PhotoRegionAssignment[],
) {
  const invalid = () =>
    new Error(
      "The results for this letter batch could not be verified. Your selections are kept; resume checking before changing them.",
    );
  if (!expected.length || !Array.isArray(quality) || quality.length !== 1)
    throw invalid();
  const sample = quality[0];
  if (
    !sample ||
    sample.sample_id !== expected[0].sample_id ||
    !Array.isArray(sample.regions) ||
    sample.regions.length !== expected.length
  )
    throw invalid();
  const ready: string[] = [];
  const issues: Record<string, string> = {};
  for (let index = 0; index < expected.length; index++) {
    const record = sample.regions[index];
    const region = expected[index];
    if (
      !record ||
      record.region_id !== region.region_id ||
      !samePhotoAssignment(record.assignment, region) ||
      !Array.isArray(record.reasons)
    )
      throw invalid();
    if (
      record.status === "analyzed" &&
      record.reasons.length === 0 &&
      typeof record.outline_hash === "string" &&
      /^sha256:[0-9a-f]{64}$/.test(record.outline_hash)
    )
      ready.push(region.region_id);
    else if (
      record.status === "needs_better_input" &&
      record.reasons.length > 0 &&
      record.reasons.every((reason: unknown) => typeof reason === "string")
    )
      issues[region.region_id] = record.reasons.includes("empty_ink")
        ? "empty_ink"
        : record.reasons.includes("crop_touches_ink")
          ? "crop_touches_ink"
          : "outline_requires_review";
    else throw invalid();
  }
  return { ready, issues };
}

/** Match the worker's white-alpha composite and integer Rec.601 threshold exactly. */
export function photoInkMask(
  rgba: Uint8ClampedArray,
  width: number,
  height: number,
  threshold: number,
) {
  if (
    !integer(width) ||
    !integer(height) ||
    width < 1 ||
    height < 1 ||
    width * height > 4_000_000 ||
    rgba.length !== width * height * 4 ||
    !integer(threshold) ||
    threshold < 0 ||
    threshold > 255
  )
    throw new Error(
      "This letter preview is outside the supported image bounds.",
    );
  const pixels = new Uint8ClampedArray(rgba.length);
  let ink = 0;
  let borderInk = false;
  for (let position = 0; position < width * height; position++) {
    const offset = position * 4;
    const alpha = rgba[offset + 3];
    const red = Math.floor(
      (rgba[offset] * alpha + 255 * (255 - alpha) + 127) / 255,
    );
    const green = Math.floor(
      (rgba[offset + 1] * alpha + 255 * (255 - alpha) + 127) / 255,
    );
    const blue = Math.floor(
      (rgba[offset + 2] * alpha + 255 * (255 - alpha) + 127) / 255,
    );
    const gray = Math.floor(
      (299 * red + 587 * green + 114 * blue + 500) / 1000,
    );
    const isInk = gray < threshold;
    const value = isInk ? 0 : 255;
    pixels[offset] = pixels[offset + 1] = pixels[offset + 2] = value;
    pixels[offset + 3] = 255;
    if (isInk) {
      ink++;
      const x = position % width;
      const y = Math.floor(position / width);
      borderInk ||= x === 0 || x === width - 1 || y === 0 || y === height - 1;
    }
  }
  return { pixels, ink, borderInk };
}
