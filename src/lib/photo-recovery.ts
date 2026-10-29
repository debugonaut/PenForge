/** Bounded selection metadata only. Original handwriting bytes stay in private capture storage. */
import {
  photoAssignments,
  samePhotoAssignment,
  validatePhotoSize,
  type PhotoRegionAssignment,
  type PhotoRegionDraft,
  type PhotoSize,
} from "./photo-regions";

export type PhotoRecoveryScope = {
  ownerUid: string;
  projectId: string;
  sampleId: string;
  digest: string;
};
export type PhotoAttempt = {
  key: string;
  regions: PhotoRegionAssignment[];
  jobId?: string;
  completed?: boolean;
};
export type PhotoRecoveryDraft = {
  regions: PhotoRegionDraft[];
  capitalHeight: number;
  threshold: number;
  attempt: PhotoAttempt | null;
  readyCount: number;
  issues: Record<string, string>;
};
type StorageLike = Pick<Storage, "getItem" | "setItem">;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const keys = (value: Record<string, unknown>, allowed: string[]) =>
  Object.keys(value).every((key) => allowed.includes(key));

export function photoRecoveryKey(scope: PhotoRecoveryScope) {
  if (
    !scope.ownerUid ||
    scope.ownerUid.length > 128 ||
    !scope.projectId ||
    scope.projectId.length > 128 ||
    !uuid.test(scope.sampleId) ||
    !/^sha256:[0-9a-f]{64}$/.test(scope.digest)
  )
    throw new Error("invalid_photo_recovery_scope");
  return `penforge-photo-letters-v1:${[scope.ownerUid, scope.projectId, scope.sampleId, scope.digest].map(encodeURIComponent).join(":")}`;
}

function validateDraft(
  value: unknown,
  scope: PhotoRecoveryScope,
  size: PhotoSize,
): PhotoRecoveryDraft {
  if (
    !record(value) ||
    !keys(value, [
      "regions",
      "capitalHeight",
      "threshold",
      "attempt",
      "readyCount",
      "issues",
    ]) ||
    !Array.isArray(value.regions) ||
    value.regions.length > 96 ||
    typeof value.capitalHeight !== "number" ||
    typeof value.threshold !== "number"
  )
    throw new Error("invalid_photo_recovery");
  validatePhotoSize(size);
  if (
    !Number.isFinite(value.capitalHeight) ||
    value.capitalHeight < 7 ||
    value.capitalHeight > size.height ||
    !Number.isInteger(value.threshold) ||
    value.threshold < 0 ||
    value.threshold > 255
  )
    throw new Error("invalid_photo_recovery_settings");
  for (const draft of value.regions) {
    if (
      !record(draft) ||
      !keys(draft, ["id", "character", "rect", "baseline"]) ||
      typeof draft.id !== "string" ||
      typeof draft.character !== "string" ||
      typeof draft.baseline !== "number" ||
      !record(draft.rect) ||
      !keys(draft.rect, ["x", "y", "width", "height"])
    )
      throw new Error("invalid_photo_recovery_region");
  }
  const regions = value.regions as PhotoRegionDraft[];
  const expected = regions.length
    ? photoAssignments(
        scope.sampleId,
        scope.digest,
        size,
        regions,
        value.capitalHeight,
        value.threshold,
      )
    : [];
  if (
    typeof value.readyCount !== "number" ||
    !Number.isInteger(value.readyCount) ||
    value.readyCount < 0 ||
    value.readyCount > 96 ||
    !record(value.issues)
  )
    throw new Error("invalid_photo_recovery_outcome");
  for (const [id, reason] of Object.entries(value.issues)) {
    if (
      !regions.some((region) => region.id === id) ||
      !["empty_ink", "crop_touches_ink", "outline_requires_review"].includes(
        String(reason),
      )
    )
      throw new Error("invalid_photo_recovery_issue");
  }
  if (value.attempt !== null) {
    const attempt = value.attempt;
    if (
      !record(attempt) ||
      !keys(attempt, ["key", "regions", "jobId", "completed"]) ||
      typeof attempt.key !== "string" ||
      !uuid.test(attempt.key) ||
      !Array.isArray(attempt.regions) ||
      !expected.length ||
      attempt.regions.length !== expected.length ||
      attempt.regions.some(
        (row, index) => !samePhotoAssignment(row, expected[index]),
      ) ||
      (attempt.jobId !== undefined &&
        (typeof attempt.jobId !== "string" || !uuid.test(attempt.jobId))) ||
      (attempt.completed !== undefined &&
        (attempt.completed !== true || !attempt.jobId))
    )
      throw new Error("invalid_photo_recovery_attempt");
  }
  return value as PhotoRecoveryDraft;
}

export function readPhotoRecovery(
  storage: StorageLike,
  scope: PhotoRecoveryScope,
  size: PhotoSize,
):
  | { state: "missing" | "invalid"; draft: null }
  | { state: "ready"; draft: PhotoRecoveryDraft } {
  const raw = storage.getItem(photoRecoveryKey(scope));
  if (raw === null) return { state: "missing", draft: null };
  try {
    if (raw.length > 131072) throw new Error("photo_recovery_too_large");
    const value: unknown = JSON.parse(raw);
    if (
      !record(value) ||
      !keys(value, ["version", "scope", "size", "draft"]) ||
      value.version !== 1 ||
      !record(value.scope) ||
      Object.keys(value.scope).length !== 4 ||
      !Object.entries(scope).every(
        ([key, entry]) =>
          value.scope &&
          (value.scope as Record<string, unknown>)[key] === entry,
      ) ||
      !record(value.size) ||
      Object.keys(value.size).length !== 2 ||
      value.size.width !== size.width ||
      value.size.height !== size.height
    )
      throw new Error("photo_recovery_source_mismatch");
    return { state: "ready", draft: validateDraft(value.draft, scope, size) };
  } catch {
    return { state: "invalid", draft: null };
  }
}

export function savePhotoRecovery(
  storage: StorageLike,
  scope: PhotoRecoveryScope,
  size: PhotoSize,
  draft: PhotoRecoveryDraft,
) {
  validateDraft(draft, scope, size);
  const key = photoRecoveryKey(scope);
  const text = JSON.stringify({ version: 1, scope, size, draft });
  if (text.length > 131072) throw new Error("photo_recovery_too_large");
  const previous = storage.getItem(key);
  if (
    previous !== null &&
    readPhotoRecovery(storage, scope, size).state === "invalid"
  ) {
    // Preserve unreadable bytes before replacing the active record; a failed backup never overwrites it.
    storage.setItem(`${key}:unreadable:${crypto.randomUUID()}`, previous);
  }
  storage.setItem(key, text);
}
