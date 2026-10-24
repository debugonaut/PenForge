/** Each unknown-commit export request owns a key; tabs never rewrite an aggregate. */
import type { ExportReceipt, ExportRequest } from "./export-client";
export type ExportRecovery = {
  key: string;
  body: ExportRequest;
  receipt?: ExportReceipt;
};
export type ExportRecoveryStorage = Pick<
  Storage,
  "length" | "key" | "getItem" | "setItem" | "removeItem"
>;
export function exportRecoveryPrefix(owner: string, project: string) {
  return `penforge-export-request-v1:${encodeURIComponent(owner)}:${project}`;
}
export function saveExportRecovery(
  storage: ExportRecoveryStorage,
  prefix: string,
  value: ExportRecovery,
) {
  storage.setItem(`${prefix}:operation:${value.key}`, JSON.stringify(value));
}
export function readExportRecoveries(
  storage: ExportRecoveryStorage,
  prefix: string,
): ExportRecovery[] {
  const keys = Array.from({ length: storage.length }, (_, index) =>
    storage.key(index),
  ).filter(
    (key): key is string =>
      key === prefix || !!key?.startsWith(`${prefix}:operation:`),
  );
  const rows = new Map<string, ExportRecovery>();
  for (const key of keys.sort()) {
    try {
      const raw = storage.getItem(key);
      if (!raw) continue;
      const value = JSON.parse(raw) as ExportRecovery;
      if (
        typeof value.key !== "string" ||
        !/^[a-f0-9-]{36}$/i.test(value.key) ||
        !value.body?.formats?.length ||
        typeof value.body.family_name !== "string"
      )
        continue;
      if (key === prefix && storage.getItem(`${prefix}:resolved:${value.key}`))
        continue;
      rows.set(value.key, value);
    } catch {
      /* Malformed recovery stays intact for inspection. */
    }
  }
  return [...rows.values()];
}
export function completeExportRecovery(
  storage: ExportRecoveryStorage,
  prefix: string,
  value: ExportRecovery,
) {
  storage.setItem(`${prefix}:resolved:${value.key}`, "true");
  storage.removeItem(`${prefix}:operation:${value.key}`);
}
