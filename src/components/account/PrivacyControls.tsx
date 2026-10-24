"use client";

import { useEffect, useRef, useState } from "react";
import type { IdentityService } from "@/lib/auth-service";
import {
  createPrivacyClient,
  type Consent,
  type Deletion,
  type DeletionInput,
} from "@/lib/privacy-client";
import { useConfirmation } from "@/components/shared/ConfirmationDialog";
import type { components } from "../../../packages/contracts/src/generated";
import styles from "./PrivacyControls.module.css";

type Project = components["schemas"]["ProjectResponse"];
type Sample = components["schemas"]["SampleResponse"];
const messages: Record<string, string> = {
  recent_auth_required:
    "Sign out and sign in again before changing consent or requesting deletion. Your writing stays private.",
  consent_ineligible:
    "Guests cannot contribute training data under the current policy.",
  consent_policy_conflict:
    "The policy changed. Refresh private settings before choosing again.",
  consent_required: "Active consent matching the current policy is required.",
  not_found: "This private resource is no longer available to this account.",
  account_deleting:
    "Account deletion is underway. Existing deletion receipts still show progress.",
  revision_conflict:
    "The project changed. Refresh private settings and review its current scope.",
  capability_unavailable:
    "This private capability is currently unavailable. No action was completed.",
  dependency_unavailable:
    "The private service is unavailable. Keep this tab and retry.",
  invalid_token: "Sign in again to access private settings.",
};
const stepNames: Record<string, string> = {
  jobs: "Stop processing",
  aliases: "Preserve other projects",
  contributions: "Remove dataset copies",
  storage: "Remove private files",
  database: "Remove database records",
  firebase: "Remove account identity",
  complete: "Complete",
};

export default function PrivacyControls({
  identity,
  uid,
  apiOrigin,
  onAccountDeleted,
}: {
  identity: IdentityService;
  uid: string;
  apiOrigin: string;
  onAccountDeleted?: (deleted: boolean) => void;
}) {
  const [opened, setOpened] = useState(false);
  const [consent, setConsent] = useState<Consent | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [sample, setSample] = useState("");
  const [request, setRequest] = useState<Deletion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const receipt = useRef<string | undefined>(undefined);
  const pending = useRef<{ body: string; key: string } | null>(null);
  const { confirm, confirmationDialog } = useConfirmation();
  const client = useRef(
    createPrivacyClient(apiOrigin, () => identity.getAccessToken()),
  );
  const recoveryKey = `penforge-privacy-receipt:v1:${uid}`;
  function failed(cause: unknown) {
    const code = cause instanceof Error ? cause.message : "request_failed";
    setError(
      messages[code] ??
        "The request could not be confirmed. Retry with the same scope in this tab.",
    );
  }
  async function act(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (cause) {
      failed(cause);
    } finally {
      setBusy(false);
    }
  }
  function remember(value: Deletion) {
    if (value.progress_token) receipt.current = value.progress_token;
    else if (request?.id !== value.id) receipt.current = undefined;
    setRequest(value);
    try {
      sessionStorage.setItem(
        recoveryKey,
        JSON.stringify({ id: value.id, receipt: receipt.current }),
      );
    } catch {
      /* Tab-only status remains available. */
    }
  }
  async function load() {
    setOpened(true);
    await act(async () => {
      try {
        const saved = JSON.parse(sessionStorage.getItem(recoveryKey) || "null");
        if (saved?.id) {
          receipt.current = saved.receipt;
          remember(await client.current.progress(saved.id, saved.receipt));
        }
      } catch {
        /* A stale receipt cannot authorize another account. */
      }
      setConsent(await client.current.consent());
      setProjects((await client.current.projects()).items);
    });
  }
  useEffect(() => {
    if (!request || request.state === "completed") return;
    const abort = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const next = await client.current.progress(
          request.id,
          receipt.current,
          abort.signal,
        );
        if (!abort.signal.aborted) setRequest(next);
      } catch (cause) {
        if (!abort.signal.aborted) failed(cause);
      }
    }, 2000);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [request]);
  useEffect(() => {
    if (request?.scope === "account" && request.state === "completed")
      onAccountDeleted?.(true);
  }, [request, onAccountDeleted]);
  async function consentChoice(granted: boolean) {
    if (
      !consent ||
      !(await confirm({
        title: granted
          ? "Allow model-training contributions?"
          : "Withdraw training consent?",
        message: granted
          ? "This enables a separate, explicit contribution action for selected stored samples under model-training-v1. Creating your own font stays independent. Nothing is contributed by this choice alone."
          : "Future dataset selection stops immediately. Active dataset copies will be removed through resumable cleanup. Withdrawal cannot guarantee removal of influence from already trained weights.",
        confirmLabel: granted ? "Allow contributions" : "Withdraw consent",
        destructive: !granted,
      }))
    )
      return;
    await act(async () => {
      const next = await client.current.setConsent(
        granted,
        consent.policy_version,
      );
      setConsent(next);
      if (next.withdrawal_deletion_id)
        remember(await client.current.progress(next.withdrawal_deletion_id));
    });
  }
  async function erase(scope: DeletionInput["scope"]) {
    const selected = projects.find((p) => p.id === project);
    if (scope === "project" && !selected) return;
    const label =
      scope === "project"
        ? `Delete “${selected?.name}”?`
        : scope === "account"
          ? "Delete your private account?"
          : "Delete training contributions only?";
    if (
      !(await confirm({
        title: label,
        message:
          scope === "account"
            ? "This removes all private projects, sources, glyph history, exports, dataset copies and your account identity, including claimed guest work. Processing stops first. Local demo drafts remain in this browser. Provider backup expiry is unknown."
            : scope === "project"
              ? "This permanently removes this private project's captures, glyph history, snapshots and exports. Other projects and local demo drafts remain. Provider backup expiry is unknown."
              : "This withdraws training consent and removes active dataset copies. Your private working projects remain. It cannot guarantee removal of influence from already trained weights.",
        confirmLabel: "Confirm deletion",
        destructive: true,
      }))
    )
      return;
    await act(async () => {
      const body: DeletionInput = {
        scope,
        confirmed: true,
        ...(selected && scope === "project"
          ? {
              project_id: selected.id,
              expected_project_version: selected.row_version,
            }
          : {}),
      };
      const encoded = JSON.stringify(body);
      if (pending.current?.body !== encoded)
        pending.current = { body: encoded, key: crypto.randomUUID() };
      remember(await client.current.delete(body, pending.current.key));
      pending.current = null;
      if (scope === "project") {
        setProjects((items) => items.filter((p) => p.id !== selected?.id));
        setProject("");
        setSamples([]);
        setSample("");
      }
    });
  }
  return (
    <section
      className={styles.panel}
      id="privacy"
      aria-labelledby="privacy-heading"
      aria-busy={busy}
    >
      <h2 id="privacy-heading">Your writing, your choice.</h2>
      <p>
        Private font creation never grants training consent. Local drafts stay
        on this device.
      </p>
      <button
        type="button"
        onClick={() => void load()}
        disabled={busy || request?.scope === "account"}
      >
        {opened ? "Refresh private settings" : "Open private settings"}
      </button>
      {opened && consent && request?.scope !== "account" && (
        <>
          <div className={styles.block}>
            <h3>Training consent</h3>
            <p>
              {consent.granted
                ? "Allowed for separately selected samples"
                : "Off — no training contributions allowed"}{" "}
              · {consent.policy_version}
            </p>
            <p>
              Withdrawal excludes future datasets. It cannot guarantee removal
              of influence from already trained weights.
            </p>
            <button
              type="button"
              disabled={busy || (!consent.eligible && !consent.granted)}
              onClick={() => void consentChoice(!consent.granted)}
            >
              {consent.granted ? "Withdraw consent" : "Allow contributions"}
            </button>
            {!consent.eligible && (
              <p>
                Guest contributions are unavailable under the current policy.
              </p>
            )}
          </div>
          <div className={styles.block}>
            <label>
              Private project
              <select
                value={project}
                onChange={(e) => {
                  setProject(e.target.value);
                  setSamples([]);
                  setSample("");
                }}
              >
                <option value="">Choose a private project</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              disabled={busy || !project}
              onClick={() => void erase("project")}
            >
              Delete selected private project
            </button>
            {consent.granted && (
              <details>
                <summary>Contribute a selected sample</summary>
                <p>
                  Only this selected stored source will be copied into the
                  separate dataset area. This does not start training.
                </p>
                <button
                  type="button"
                  disabled={busy || !project}
                  onClick={() =>
                    void act(async () => {
                      setSamples((await client.current.samples(project)).items);
                    })
                  }
                >
                  Load stored samples
                </button>
                <label>
                  Stored sample
                  <select
                    value={sample}
                    onChange={(e) => setSample(e.target.value)}
                  >
                    <option value="">Choose one sample</option>
                    {samples.map((s) => (
                      <option key={s.sample_id} value={s.sample_id}>
                        {s.source_type} · {s.sample_id.slice(0, 8)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={busy || !sample}
                  onClick={async () => {
                    if (
                      !(await confirm({
                        title: "Contribute this stored sample?",
                        message:
                          "A metadata-minimized copy of this sample enters the separate dataset area under your current model-training consent. No other captures or local drafts are sent. No training starts now.",
                        confirmLabel: "Contribute selected sample",
                      }))
                    )
                      return;
                    await act(async () => {
                      await client.current.contribute(
                        sample,
                        consent.consent_id!,
                        consent.policy_version,
                        crypto.randomUUID(),
                      );
                      setNotice(
                        "Selected sample copied to the separate dataset area. No training was run.",
                      );
                    });
                  }}
                >
                  Contribute selected sample
                </button>
              </details>
            )}
          </div>
          <div className={styles.block}>
            <h3>Delete private data</h3>
            <p>
              Deletion stops processing first, then removes files and database
              records. Account identity is removed last. Provider backup expiry
              remains unknown.
            </p>
            <div className={styles.actions}>
              <button
                type="button"
                disabled={busy}
                onClick={() => void erase("contributions_only")}
              >
                Delete contributions only
              </button>
              <button
                type="button"
                className={styles.destructive}
                disabled={busy}
                onClick={() => void erase("account")}
              >
                Delete private account
              </button>
            </div>
          </div>
        </>
      )}
      {request && (
        <div className={styles.progress} role="status" aria-live="polite">
          <h3>
            {request.state === "completed"
              ? "Deletion completed"
              : request.state === "failed"
                ? "Cleanup will retry"
                : "Deletion in progress"}
          </h3>
          <p>
            Scope: {request.scope.replaceAll("_", " ")}.{" "}
            {request.error_code
              ? "A private dependency is unavailable. Completed steps are preserved; retries continue automatically."
              : ""}
          </p>
          <ol>
            {Object.entries(stepNames)
              .filter(
                ([key]) =>
                  request.scope === "account" ||
                  (request.scope === "contributions_only"
                    ? ["contributions", "complete"].includes(key)
                    : key !== "firebase"),
              )
              .map(([key, label]) => (
                <li key={key}>
                  {label}:{" "}
                  {request.progress[key] === "done"
                    ? "Done"
                    : request.progress[key] === "waiting"
                      ? "Waiting for processing to stop"
                      : "Pending"}
                </li>
              ))}
          </ol>
          {request.state !== "completed" && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void act(async () =>
                  remember(
                    await client.current.progress(request.id, receipt.current),
                  ),
                )
              }
            >
              Check progress
            </button>
          )}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {confirmationDialog}
    </section>
  );
}
