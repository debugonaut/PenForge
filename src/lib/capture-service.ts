import {
  CaptureError,
  type CaptureClient,
  type CaptureJob,
} from "./capture-client";
import { captureRecovery, type CaptureOperation } from "./capture-recovery";

export type CaptureProgress = {
  phase: CaptureOperation["phase"];
  stage?: string;
  completed: number;
  total: number | null;
  basis: "bytes" | "verified_checks";
  etaSeconds: null;
};

async function pause(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, 1000);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

/** Retries reuse both resource keys and selected original bytes, including after reload. */
export async function submitCapture(
  client: CaptureClient,
  pending: CaptureOperation,
  ownerUid: string,
  progress: (value: CaptureProgress) => void,
  signal?: AbortSignal,
  recovery = captureRecovery,
): Promise<CaptureOperation> {
  if (pending.ownerUid !== ownerUid)
    throw new CaptureError("identity_mismatch");
  let operation = pending;
  const set = async (patch: Parameters<typeof recovery.patch>[1]) => {
    operation = await recovery.patch(operation.id, patch);
    return operation;
  };
  try {
    // Read the current operation in the same transaction used for patches. A
    // stale tab must not replay a key after another tab has confirmed the source.
    await set({});
    if (operation.ownerUid !== ownerUid)
      throw new CaptureError("identity_mismatch");
    if (operation.phase === "confirmed") {
      progress({
        phase: "confirmed",
        completed: 1,
        total: 1,
        basis: "verified_checks",
        etaSeconds: null,
      });
      return operation;
    }
    if (operation.strokeDocument) {
      await set({ phase: "registering" });
      progress({
        phase: "registering",
        completed: 0,
        total: null,
        basis: "verified_checks",
        etaSeconds: null,
      });
      const sample = await client.sample(
        operation.projectId,
        {
          source_kind: "strokes",
          stroke_document: operation.strokeDocument,
          ...(operation.promptContext
            ? { prompt_context: operation.promptContext }
            : {}),
        },
        operation.sampleKey,
        signal,
      );
      await set({
        phase: "confirmed",
        assetId: sample.asset_id,
        sampleId: sample.sample_id,
      });
    } else {
      const original = operation.originalId
        ? await recovery.getOriginal(operation.originalId)
        : undefined;
      if (!original) throw new CaptureError("original_missing");
      if (original.localProjectId !== operation.localProjectId)
        throw new CaptureError("original_missing");
      if (!operation.assetId) {
        const digest = Array.from(
          new Uint8Array(
            await crypto.subtle.digest(
              "SHA-256",
              await original.bytes.arrayBuffer(),
            ),
          ),
        )
          .map((byte) => byte.toString(16).padStart(2, "0"))
          .join("");
        const created = await client.init(
          operation.projectId,
          {
            filename: original.name,
            declared_size: original.bytes.size,
            declared_mime: original.mime as
              "image/jpeg" | "image/png" | "image/webp",
            source_type: operation.sourceType ?? "freeform_photo",
            sha256: `sha256:${digest}`,
            ...(operation.template ? { template: operation.template } : {}),
          },
          operation.initKey,
          signal,
        );
        await set({ assetId: created.asset_id });
      }
      let asset = await client.asset(operation.assetId!, signal);
      if (asset.state === "pending_upload") {
        const renewed = await client.renew(asset.asset_id, signal);
        await set({ phase: "uploading" });
        progress({
          phase: "uploading",
          completed: 0,
          total: original.bytes.size,
          basis: "bytes",
          etaSeconds: null,
        });
        await client.upload(
          renewed.upload,
          original.bytes,
          (sent, total) =>
            progress({
              phase: "uploading",
              completed: sent,
              total,
              basis: "bytes",
              etaSeconds: null,
            }),
          signal,
        );
        const completed = await client.complete(asset.asset_id, signal);
        await set({ jobId: completed.job_id, phase: "verifying" });
        asset = completed.asset;
      }
      if (asset.state === "uploaded" || asset.state === "verifying") {
        if (!operation.jobId && asset.job_id)
          await set({ jobId: asset.job_id });
        if (!operation.jobId) throw new CaptureError("state_conflict");
        let job: CaptureJob;
        do {
          job = await client.job(operation.jobId, signal);
          progress({
            phase: "verifying",
            stage: job.stage ?? undefined,
            completed: job.progress.completed_units,
            total: job.progress.total_units,
            basis: "verified_checks",
            etaSeconds: null,
          });
          if (["succeeded", "failed", "canceled"].includes(job.state)) break;
          await pause(signal);
        } while (true);
        asset = await client.asset(asset.asset_id, signal);
        if (job.state !== "succeeded" && asset.state !== "ready") {
          // A failed or canceled verification is a terminal retake signal, not a
          // transport retry: preserve the local original and show the retake action.
          if (
            asset.state === "rejected" ||
            job.state === "failed" ||
            job.state === "canceled"
          ) {
            await set({ phase: "retake" });
            throw new CaptureError("capture_rejected");
          }
          throw new CaptureError(job.error_code || "verification_failed");
        }
      }
      const duplicate =
        asset.state === "rejected" &&
        asset.duplicate_of &&
        asset.reject_reason === "duplicate";
      if (asset.state !== "ready" && !duplicate) {
        await set({ phase: "retake" });
        throw new CaptureError("capture_rejected");
      }
      await set({ phase: "registering" });
      const sample = await client.sample(
        operation.projectId,
        {
          source_kind: "asset",
          asset_id: asset.asset_id,
          source_type: operation.sourceType ?? "freeform_photo",
          ...(operation.template ? { template: operation.template } : {}),
          ...(operation.promptContext
            ? { prompt_context: operation.promptContext }
            : {}),
        },
        operation.sampleKey,
        signal,
      );
      await set({ phase: "confirmed", sampleId: sample.sample_id });
    }
    progress({
      phase: "confirmed",
      completed: 1,
      total: 1,
      basis: "verified_checks",
      etaSeconds: null,
    });
    return operation;
  } catch (error) {
    if (operation.phase !== "retake") await set({ phase: "retry" });
    if (operation.phase === "confirmed") {
      progress({
        phase: "confirmed",
        completed: 1,
        total: 1,
        basis: "verified_checks",
        etaSeconds: null,
      });
      return operation;
    }
    throw error;
  }
}

export function captureErrorText(error: unknown): string {
  const code = error instanceof CaptureError ? error.code : "";
  const known: Record<string, string> = {
    terms_required:
      "Open Your account to accept the current terms. Your local original is retained.",
    account_required: "Create an account before uploading handwriting.",
    email_verification_required:
      "Verify your email in Your account, then retry.",
    service_not_ready: "The hosted service is not ready for public use yet.",
    quota_unconfigured:
      "Private capture storage has no configured allowance yet. Your local backup is retained.",
    quota_exceeded:
      "The configured storage allowance is full. Your local backup is retained.",
    upload_expired:
      "This upload expired. Retake or choose the original again to begin a new upload.",
    original_missing:
      "The original image bytes are unavailable in this browser. Select the original file again; its preview cannot replace it.",
    identity_mismatch:
      "This recovery belongs to another identity. Sign in with its original account; your current account can start an independent upload.",
    capture_rejected:
      "This image did not pass verification. Retake it or choose a valid JPEG, PNG or WebP. Your local original is retained.",
    not_found:
      "This capture or project is unavailable to the connected identity. Check the account and destination project.",
    template_mismatch:
      "The template version could not be verified. Use a supported template or capture a freeform image.",
    decoder_unavailable:
      "Safe image verification is temporarily unavailable. Keep the local backup and retry later.",
    account_merged:
      "This guest account was claimed. Connect the destination account; the original recovery evidence remains here.",
  };
  return (
    known[code] ||
    "The capture could not be confirmed. Your original and retry details remain in this browser. Retry when connected."
  );
}
