/** Original captures and independent upload attempts live in transactional IndexedDB.
 * Preview JPEGs in local demo drafts are never used as original image bytes.
 */
import type { components } from "../../packages/contracts/src/generated";

export type RawStrokeDocument = components["schemas"]["StrokeDocument"];
export type OriginalCapture = {
  id: string;
  localProjectId: string;
  bytes: Blob;
  name: string;
  mime: string;
  lastModified: number;
  createdAt: string;
};
export type CaptureOperation = {
  id: string;
  ownerUid: string;
  localProjectId: string;
  projectId: string;
  originalId?: string;
  strokeDocument?: RawStrokeDocument;
  /** The selected public manifest is frozen with this operation, including retries. */
  sourceType?: components["schemas"]["UploadInit"]["source_type"];
  template?: components["schemas"]["TemplateReference"];
  promptContext?: components["schemas"]["PromptContext"];
  initKey: string;
  sampleKey: string;
  assetId?: string;
  jobId?: string;
  sampleId?: string;
  phase:
    | "prepared"
    | "uploading"
    | "verifying"
    | "registering"
    | "confirmed"
    | "retry"
    | "retake";
  createdAt: string;
};

const DATABASE = "penforge-private-captures-v1";
type StoreName = "originals" | "operations";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      db.createObjectStore("originals", { keyPath: "id" });
      db.createObjectStore("operations", { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error("capture_recovery_unavailable"));
    request.onblocked = () => reject(new Error("capture_recovery_unavailable"));
  });
}

async function transaction<Result>(
  storeName: StoreName,
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore, complete: (value: Result) => void) => void,
): Promise<Result> {
  const db = await open();
  return new Promise((resolve, reject) => {
    let result: Result;
    const tx = db.transaction(storeName, mode);
    tx.oncomplete = () => {
      db.close();
      resolve(result);
    };
    tx.onerror = tx.onabort = () => {
      db.close();
      reject(new Error("capture_recovery_unavailable"));
    };
    try {
      work(tx.objectStore(storeName), (value) => {
        result = value;
      });
    } catch {
      tx.abort();
    }
  });
}

export const captureRecovery = {
  async preserve(localProjectId: string, file: File): Promise<OriginalCapture> {
    const record: OriginalCapture = {
      id: crypto.randomUUID(),
      localProjectId,
      bytes: file.slice(0, file.size, file.type),
      name: file.name,
      mime: file.type,
      lastModified: file.lastModified,
      createdAt: new Date().toISOString(),
    };
    await transaction<void>("originals", "readwrite", (store, done) => {
      store.add(record);
      done();
    });
    return record;
  },
  getOriginal(id: string): Promise<OriginalCapture | undefined> {
    return transaction("originals", "readonly", (store, done) => {
      const request = store.get(id);
      request.onsuccess = () => done(request.result);
    });
  },
  async prepare(
    input: Omit<
      CaptureOperation,
      "id" | "initKey" | "sampleKey" | "phase" | "createdAt"
    >,
  ): Promise<CaptureOperation> {
    const operation: CaptureOperation = {
      ...input,
      id: crypto.randomUUID(),
      initKey: crypto.randomUUID(),
      sampleKey: crypto.randomUUID(),
      phase: "prepared",
      createdAt: new Date().toISOString(),
    };
    await transaction<void>("operations", "readwrite", (store, done) => {
      store.add(operation);
      done();
    });
    return operation;
  },
  /** The read and patch share a transaction, including across browser tabs. */
  patch(
    id: string,
    patch: Partial<
      Pick<CaptureOperation, "assetId" | "jobId" | "sampleId" | "phase">
    >,
  ): Promise<CaptureOperation> {
    return transaction("operations", "readwrite", (store, done) => {
      const request = store.get(id);
      request.onsuccess = () => {
        if (!request.result) {
          store.transaction.abort();
          return;
        }
        // A slower tab cannot regress confirmation or erase another tab's receipt.
        const next =
          request.result.phase === "confirmed"
            ? (request.result as CaptureOperation)
            : ({ ...request.result, ...patch } as CaptureOperation);
        store.put(next);
        done(next);
      };
    });
  },
  list(localProjectId: string): Promise<CaptureOperation[]> {
    return transaction("operations", "readonly", (store, done) => {
      const request = store.getAll();
      request.onsuccess = () =>
        done(
          (request.result as CaptureOperation[])
            .filter((operation) => operation.localProjectId === localProjectId)
            .sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
        );
    });
  },
};
