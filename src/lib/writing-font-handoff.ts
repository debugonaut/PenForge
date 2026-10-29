/** Navigation selects a private font; fetching and byte verification stay in the export client. */
import type { WritingDocument } from "./writing";

export function clearGeneratedWritingReference(
  document: WritingDocument,
): WritingDocument {
  const next = { ...document };
  delete next.generatedExportId;
  return next;
}

export function requestedWritingExport(
  query: string,
  saved?: string,
): string | null {
  const valid = (value: string | null | undefined) =>
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value,
    )
      ? value.toLowerCase()
      : null;
  return valid(new URLSearchParams(query).get("export")) ?? valid(saved);
}

export function writingFontReferenceTransition(previous: string | undefined, next: string | undefined, loaded: string | null) {
  const before = requestedWritingExport("", previous);
  const target = requestedWritingExport("", next);
  const actual = requestedWritingExport("", loaded ?? undefined);
  const changed = before !== target;
  return {
    changed,
    nextExport: target,
    clearFace: (changed && !(target && actual === target)) || (!!actual && actual !== target),
  };
}

export class WritingFontHandoff {
  private attempted: string | null = null;
  private dismissed = false;

  reset() {
    this.attempted = null;
    this.dismissed = false;
  }
  dismiss() {
    this.dismissed = true;
  }

  adopt(uid: string | null, exportId: string) {
    this.reset();
    const id = requestedWritingExport("", exportId);
    if (uid && id) this.attempted = `${uid}:${id}`;
  }

  take(state: {
    ready: boolean;
    accountRestored: boolean;
    settled: boolean;
    restoring: boolean;
    configured: boolean;
    uid: string | null;
    busy: boolean;
    exportId: string | null;
  }): string | null {
    if (
      this.dismissed ||
      !state.ready ||
      !state.accountRestored ||
      !state.settled ||
      state.restoring ||
      !state.configured ||
      !state.uid ||
      state.busy ||
      !state.exportId
    )
      return null;
    const id = requestedWritingExport("", state.exportId);
    if (!id) return null;
    const key = `${state.uid}:${id}`;
    if (this.attempted === key) return null;
    this.attempted = key;
    return id;
  }
}
