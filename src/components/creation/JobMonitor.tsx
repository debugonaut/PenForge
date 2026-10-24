"use client";
import { useEffect, useState } from "react";
import { terminalJob, type JobClient, type JobStatus } from "@/lib/job-client";
import styles from "./CloudReviewPanel.module.css";

export function JobMonitor({
  client,
  jobId,
}: {
  client: JobClient;
  jobId: string;
}) {
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [transport, setTransport] = useState("reconnecting");
  const [error, setError] = useState("");
  const [canceling, setCanceling] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void client
      .watch(
        jobId,
        (value) => {
          if (!controller.signal.aborted) setStatus(value);
        },
        controller.signal,
        setTransport,
      )
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            "Job updates paused. Reconnect with the same account to check its durable state.",
          );
      });
    return () => controller.abort();
  }, [client, jobId]);
  const label =
    status?.state === "cancel_requested"
      ? "Canceling at a safe checkpoint"
      : status?.retrying
        ? "Retrying after a worker interruption"
        : status?.state.replaceAll("_", " ");
  return (
    <section className={styles.notice} aria-label="Server job status">
      <div role="status" aria-live="polite">
        <strong>{label ?? "Checking saved job"}</strong>
        {status && (
          <>
            <span>
              {status.stage?.replaceAll("_", " ") ??
                "Waiting for qualified capacity"}{" "}
              ·{" "}
              {transport === "live"
                ? "Live updates"
                : "Checking saved progress"}
            </span>
            <progress
              aria-label="Measured server work"
              max={status.progress.total_units ?? 1}
              value={
                status.progress.indeterminate
                  ? undefined
                  : status.progress.completed_units
              }
            />
            <span>
              {status.progress.completed_units}
              {status.progress.total_units === null
                ? " completed units; total unknown"
                : ` / ${status.progress.total_units} ${status.progress.basis.replaceAll("_", " ")}`}{" "}
              · Time estimate unavailable: insufficient data.
            </span>
            {status.progress.denominator_change_reason && (
              <small>Work counts changed for the current stage.</small>
            )}
            {status.state === "queued" && (
              <small>Queue wait is separate from active processing.</small>
            )}
            {status.user_review_wait && (
              <small>
                Processing finished. Your review and approval are still
                required.
              </small>
            )}
            {status.error_code && (
              <small>Outcome: {status.error_code.replaceAll("_", " ")}</small>
            )}
          </>
        )}
      </div>
      {status && !terminalJob(status.state) && (
        <button
          type="button"
          disabled={canceling || status.state === "cancel_requested"}
          onClick={async () => {
            setCanceling(true);
            const controller = new AbortController();
            try {
              setStatus(await client.cancel(jobId, controller.signal));
            } catch {
              setError(
                "Cancellation could not be confirmed. Check the saved job before retrying.",
              );
            } finally {
              setCanceling(false);
            }
          }}
        >
          {canceling || status.state === "cancel_requested"
            ? "Canceling…"
            : "Cancel server job"}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
