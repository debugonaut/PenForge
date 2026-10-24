"use client";

import { createOwnerTokenProvider } from "@/lib/owner-token";

import Link from "next/link";
import { createJobClient, type JobClient } from "@/lib/job-client";
import { JobMonitor } from "./JobMonitor";
import { useEffect, useRef, useState } from "react";
import type { Sample } from "@/lib/contracts";
import {
  getAccountIdentityService,
  getPenforgeAuthConfiguration,
  type IdentityService,
} from "@/lib/auth-service";
import { createProjectsClient } from "../../../packages/contracts/src/client";
import { preserveLegacyStrokes } from "@/lib/api-boundary";
import { createCaptureClient, CaptureError } from "@/lib/capture-client";
import { captureRecovery, type CaptureOperation } from "@/lib/capture-recovery";
import {
  submitCapture,
  captureErrorText,
  type CaptureProgress,
} from "@/lib/capture-service";
import styles from "./CaptureCloudPanel.module.css";
import {
  createExtractionClient,
  type ExtractionClient,
  type TemplateChoice,
} from "@/lib/extraction-client";
import { CloudExportPanel, createExportClient } from "./CloudExportPanel";
import type { ExportClient } from "@/lib/export-client";
import { CloudReviewPanel } from "./CloudReviewPanel";

export function CaptureCloudPanel({
  localProjectId,
  sample,
}: {
  localProjectId: string;
  sample: Sample | null;
}) {
  const identity = useRef<IdentityService | null>(null);
  const controller = useRef<AbortController | null>(null);
  const connectedUid = useRef<string | null>(null);
  const requestGeneration = useRef(0);
  const lifecycleGeneration = useRef(0);
  const [open, setOpen] = useState(false);
  const [uid, setUid] = useState<string | null>(null);
  const [projects, setProjects] = useState<
    Array<{ id: string; name: string; script: string }>
  >([]);
  const [projectId, setProjectId] = useState("");
  const [operations, setOperations] = useState<CaptureOperation[]>([]);
  const [progress, setProgress] = useState<CaptureProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [legacyApproved, setLegacyApproved] = useState(false);
  const [reviewClient, setReviewClient] = useState<ExtractionClient | null>(
    null,
  );
  const [exportClient, setExportClient] = useState<ExportClient | null>(null);
  const [jobClient, setJobClient] = useState<JobClient | null>(null);
  const [templates, setTemplates] = useState<TemplateChoice[]>([]);
  const [templateIndex, setTemplateIndex] = useState("");
  const [followUpContext, setFollowUpContext] =
    useState<CaptureOperation["promptContext"]>();

  useEffect(() => {
    const session = getAccountIdentityService(
      getPenforgeAuthConfiguration(window.location.hostname),
    );
    identity.current = session;
    lifecycleGeneration.current += 1;
    const generation = ++requestGeneration.current;
    let observedUid = session.getSnapshot().identity?.firebase_uid;
    void captureRecovery
      .list(localProjectId)
      .then((records) => {
        if (requestGeneration.current === generation) setOperations(records);
      })
      .catch(() => {});
    const unsubscribe = session.subscribe(() => {
      const current = session.getSnapshot().identity?.firebase_uid;
      if (observedUid !== current) {
        observedUid = current;
        requestGeneration.current += 1;
        controller.current?.abort();
        controller.current = null;
        connectedUid.current = null;
        setUid(null);
        setProjects([]);
        setBusy(false);
        setProjectId("");
        setFollowUpContext(undefined);
        setProgress(null);
        setReviewClient(null);
        setExportClient(null);
        setJobClient(null);
        setTemplates([]);
        setTemplateIndex("");
        setError(
          "Account changed. Connect again to choose this account’s destination. Earlier recovery remains here.",
        );
      }
    });
    return () => {
      requestGeneration.current += 1;
      lifecycleGeneration.current += 1;
      identity.current = null;
      controller.current?.abort();
      unsubscribe();
    };
  }, [localProjectId]);

  async function connect() {
    setOpen(true);
    setError("");
    setBusy(true);
    const session = identity.current;
    let generation = requestGeneration.current;
    const lifecycle = lifecycleGeneration.current;
    const isCurrent = () =>
      requestGeneration.current === generation &&
      lifecycleGeneration.current === lifecycle;
    try {
      if (!session || session.configuration.state !== "configured")
        throw new CaptureError("dependency_unavailable");
      await session.connect();
      if (
        lifecycleGeneration.current !== lifecycle ||
        identity.current !== session
      )
        return;
      // Initial session hydration may establish the first identity during connect.
      generation = requestGeneration.current;
      setError("");
      setBusy(true);
      const ownerUid = session.getSnapshot().identity?.firebase_uid;
      if (!ownerUid) throw new CaptureError("unauthenticated");
      const token = createOwnerTokenProvider(
        session,
        ownerUid,
        () => new CaptureError("identity_mismatch"),
      );
      const rows = await createProjectsClient(
        session.configuration.apiOrigin,
        token,
      ).list({ limit: 100 });
      if (
        !isCurrent() ||
        session.getSnapshot().identity?.firebase_uid !== ownerUid
      )
        return;
      connectedUid.current = ownerUid;
      setUid(ownerUid);
      setProjects(
        rows.items.map(({ id, name, script_code }) => ({
          id,
          name,
          script: script_code,
        })),
      );
      setProjectId(rows.items[0]?.id || "");
      setJobClient(createJobClient(session.configuration.apiOrigin, token));
      setExportClient(
        createExportClient(session.configuration.apiOrigin, token),
      );
      const extraction = createExtractionClient(
        session.configuration.apiOrigin,
        token,
      );
      setReviewClient(extraction);
      // Public template inventory is requested only after this explicit connection.
      void Promise.all([
        extraction.templates(),
        extraction.templates(undefined, "Deva"),
      ])
        .then((choices) => {
          if (isCurrent()) setTemplates(choices.flat());
        })
        .catch(() => {});
      const records = await captureRecovery.list(localProjectId);
      if (isCurrent()) setOperations(records);
    } catch (cause) {
      if (isCurrent())
        setError(
          cause instanceof CaptureError && cause.code === "unauthenticated"
            ? "Sign in or start a guest account, then connect capture storage here."
            : captureErrorText(cause),
        );
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  async function run(pending?: CaptureOperation) {
    const session = identity.current;
    if (!session || session.configuration.state !== "configured" || !uid)
      return;
    const ownerUid = uid;
    const generation = requestGeneration.current;
    const isCurrent = () =>
      requestGeneration.current === generation &&
      connectedUid.current === ownerUid;
    setError("");
    setBusy(true);
    const cancellation = new AbortController();
    controller.current = cancellation;
    try {
      let operation = pending;
      if (!operation) {
        if (!projectId || !sample || sample.kind === "demo")
          throw new CaptureError("original_missing");
        const strokeDocument =
          sample.kind === "draw"
            ? sample.strokeDocument ||
              (legacyApproved
                ? preserveLegacyStrokes(sample.strokes || [])
                : undefined)
            : undefined;
        if (sample.kind === "draw" && !strokeDocument)
          throw new CaptureError("legacy_policy_required");
        if (sample.kind === "upload" && !sample.originalCaptureId)
          throw new CaptureError("original_missing");
        operation = await captureRecovery.prepare({
          ownerUid,
          localProjectId,
          projectId,
          ...(followUpContext ? { promptContext: followUpContext } : {}),
          ...(sample.kind === "upload" &&
          templateIndex !== "" &&
          templates[Number(templateIndex)]
            ? {
                sourceType: "template_photo" as const,
                template: {
                  template_id:
                    templates[Number(templateIndex)].manifest.template_id,
                  template_version:
                    templates[Number(templateIndex)].manifest.template_version,
                  manifest_sha256:
                    templates[Number(templateIndex)].manifest_sha256,
                  page_index:
                    templates[Number(templateIndex)].manifest.page.index,
                },
              }
            : {}),
          ...(strokeDocument
            ? { strokeDocument }
            : { originalId: sample.originalCaptureId }),
        });
      }
      const token = createOwnerTokenProvider(
        session,
        ownerUid,
        () => new CaptureError("identity_mismatch"),
      );
      const client = createCaptureClient(
        session.configuration.apiOrigin,
        token,
      );
      await submitCapture(
        client,
        operation,
        ownerUid,
        (value) => {
          if (isCurrent()) {
            setProgress(value);
            void captureRecovery
              .list(localProjectId)
              .then((records) => {
                if (isCurrent()) setOperations(records);
              })
              .catch(() => {});
          }
        },
        cancellation.signal,
      );
    } catch (cause) {
      if (
        isCurrent() &&
        !(cause instanceof DOMException && cause.name === "AbortError")
      )
        setError(captureErrorText(cause));
    } finally {
      const records = await captureRecovery
        .list(localProjectId)
        .catch(() => []);
      if (isCurrent()) setOperations(records);
      if (isCurrent() && controller.current === cancellation) {
        controller.current = null;
        setBusy(false);
      }
    }
  }

  const legacy = sample?.kind === "draw" && !sample.strokeDocument;
  return (
    <section className={styles.panel} aria-label="Private capture storage">
      <div className={styles.heading}>
        <strong>Keep a private source</strong>
        <span>OPTIONAL ACCOUNT STORAGE</span>
      </div>
      <p>
        Save this original to a cloud project only when you choose. Private
        extraction and review are separate from the local demo. Approved
        snapshots can be compiled into private font exports.
      </p>
      {sample?.kind === "upload" && !sample.originalCaptureId && (
        <p role="status">
          This local preview has no original file backup for this draft. Choose
          the original image again before uploading a private source. The
          preview remains available for the local demo.
        </p>
      )}
      {!open ? (
        <button className="text-button" onClick={() => void connect()}>
          Connect capture storage →
        </button>
      ) : (
        <>
          {!uid ? (
            <>
              <Link href="/account">Open account settings →</Link>
              <button onClick={() => void connect()} disabled={busy}>
                Check capture connection
              </button>
            </>
          ) : (
            <>
              <label>
                Destination cloud project
                <select
                  value={projectId}
                  onChange={(event) => {
                    setProjectId(event.target.value);
                    setFollowUpContext(undefined);
                  }}
                  disabled={busy}
                >
                  {projects.length === 0 && (
                    <option value="">Create a cloud project first</option>
                  )}
                  {projects.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              {followUpContext && (
                <div role="status">
                  <p>
                    Next capture:{" "}
                    {followUpContext.expected_codepoints
                      .map((cp) =>
                        cp === 32 ? "Space" : String.fromCodePoint(cp),
                      )
                      .join(" ")}
                    . These are requested marks; you still review their
                    identity.
                  </p>
                  <button
                    type="button"
                    onClick={() => setFollowUpContext(undefined)}
                  >
                    Clear follow-up
                  </button>
                </div>
              )}
              <Link href="/projects">Manage cloud projects →</Link>
              {sample?.kind === "upload" && (
                <>
                  <label>
                    Image layout for this upload
                    <select
                      value={templateIndex}
                      disabled={busy}
                      onChange={(event) => setTemplateIndex(event.target.value)}
                    >
                      <option value="">
                        Natural page · private storage only
                      </option>
                      {templates
                        .filter(
                          (t) =>
                            t.manifest.script_code ===
                            (projects.find((p) => p.id === projectId)?.script ??
                              "Latn"),
                        )
                        .map((choice, index) => (
                          <option
                            key={`${choice.manifest.template_id}:${choice.manifest.template_version}:${choice.manifest.page.index}`}
                            value={index}
                          >
                            {choice.manifest.template_id} ·{" "}
                            {choice.manifest.template_version} · page{" "}
                            {choice.manifest.page.index + 1}
                          </option>
                        ))}
                    </select>
                  </label>
                  {templateIndex !== "" &&
                    templates[Number(templateIndex)] &&
                    reviewClient && (
                      <div className={styles.status}>
                        <p>
                          Use this exact template at 100% scale. Keep the whole
                          page and markers visible; write dark, isolated marks
                          in their cells. Expected labels are assignment
                          context, never proof of the shape.
                        </p>
                        <a
                          href={reviewClient.publicTemplateUrl(
                            templates[Number(templateIndex)].pdf_url,
                          )}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open printable PDF →
                        </a>
                        <a
                          href={reviewClient.publicTemplateUrl(
                            templates[Number(templateIndex)].svg_url,
                          )}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open on-screen template →
                        </a>
                        <small>
                          Version and manifest digest are pinned with this
                          operation and retained across retry/reload.
                        </small>
                      </div>
                    )}
                </>
              )}
              {legacy && (
                <label className={styles.check}>
                  <input
                    type="checkbox"
                    checked={legacyApproved}
                    onChange={(event) =>
                      setLegacyApproved(event.target.checked)
                    }
                    disabled={busy}
                  />
                  Preserve this legacy x/y drawing with timing, pressure and
                  dimensions unavailable.
                </label>
              )}
              <button
                className="button button-dark"
                disabled={
                  busy ||
                  !projectId ||
                  !sample ||
                  sample.kind === "demo" ||
                  (legacy && !legacyApproved) ||
                  (sample?.kind === "upload" && !sample.originalCaptureId)
                }
                onClick={() => void run()}
              >
                {busy
                  ? "Saving private source…"
                  : sample?.kind === "draw"
                    ? "Save raw drawing"
                    : "Upload selected original"}
              </button>
            </>
          )}
          {progress && (
            <div role="status" aria-live="polite" className={styles.status}>
              <strong>
                {progress.phase === "confirmed"
                  ? "Source confirmed · local backup retained"
                  : progress.phase === "uploading"
                    ? "Uploading original bytes"
                    : progress.phase === "registering"
                      ? "Registering immutable source"
                      : "Verifying private image"}
              </strong>
              {progress.stage && (
                <span>{progress.stage.replaceAll("_", " ")}</span>
              )}
              {progress.phase !== "confirmed" && (
                <>
                  <progress
                    value={
                      progress.total === null ? undefined : progress.completed
                    }
                    max={progress.total ?? 1}
                    aria-label="Capture progress"
                  />
                  <span>
                    {progress.total === null
                      ? "Waiting for a worker"
                      : `${progress.completed} / ${progress.total} ${progress.basis === "bytes" ? "bytes sent" : "checks"}`}{" "}
                    · Time estimate unavailable
                  </span>
                </>
              )}
              {progress.phase === "confirmed" && (
                <span>
                  Source stored. Open private extraction below to assess it;
                  nothing is approved automatically.
                </span>
              )}
            </div>
          )}
          {busy && (
            <button onClick={() => controller.current?.abort()}>
              Stop checking; keep local recovery
            </button>
          )}
          {jobClient &&
            operations
              .filter(
                (op) =>
                  op.ownerUid === uid && op.jobId && op.phase !== "confirmed",
              )
              .map((op) => (
                <JobMonitor
                  key={op.jobId}
                  client={jobClient}
                  jobId={op.jobId!}
                />
              ))}
          {operations
            .filter((operation) => operation.phase !== "confirmed")
            .map((operation) => (
              <div key={operation.id} className={styles.recovery}>
                <strong>
                  {operation.phase === "retake"
                    ? "Retake needed"
                    : "Unconfirmed capture retained"}
                </strong>
                <span>
                  Started {new Date(operation.createdAt).toLocaleString()} ·{" "}
                  {operation.assetId
                    ? "Server asset recorded"
                    : "Original request key retained"}
                </span>
                {operation.ownerUid === uid ? (
                  <button
                    disabled={busy || operation.phase === "retake"}
                    onClick={() => void run(operation)}
                  >
                    Retry this capture
                  </button>
                ) : (
                  <p>
                    This attempt belongs to another account. Its original bytes
                    and request keys remain here; connect its original account
                    to retry. Server assets transferred by a claim remain owned
                    by the destination, but this browser operation is not
                    replayed under a different UID.
                  </p>
                )}
              </div>
            ))}
          {uid && projectId && exportClient && (
            <CloudExportPanel
              key={`export:${uid}:${projectId}`}
              client={exportClient}
              jobClient={jobClient ?? undefined}
              projectId={projectId}
              ownerUid={uid}
            />
          )}
          {uid && projectId && reviewClient && (
            <CloudReviewPanel
              key={`${uid}:${projectId}`}
              client={reviewClient}
              jobClient={jobClient ?? undefined}
              projectId={projectId}
              ownerUid={uid}
              scriptCode={
                projects.find((p) => p.id === projectId)?.script ?? "Latn"
              }
              operations={operations}
              onFollowUp={(prompt) =>
                setFollowUpContext({
                  prompt_policy_version: "coverage-followup-v2",
                  expected_codepoints:
                    prompt.expected_codepoints ?? prompt.codepoints,
                  selection_reasons: prompt.selection_reasons ?? {},
                  evidence_hash: prompt.evidence_hash!,
                  prompt_is_ground_truth: false,
                })
              }
            />
          )}
        </>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
