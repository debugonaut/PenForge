"use client";

import Link from "next/link";
import { useState } from "react";
import { useServiceAccess, type AccessSession } from "@/lib/use-service-access";
import { serviceAccessMessage } from "@/lib/service-access-client";
import styles from "./ServiceAccessPanel.module.css";

export function ServiceAccessPanel({ session }: { session: AccessSession }) {
  const access = useServiceAccess(session);
  return (
    <ServiceAccessView key={session.uid} session={session} access={access} />
  );
}

export function ServiceAccessView({
  session,
  access,
}: {
  session: AccessSession;
  access: ReturnType<typeof useServiceAccess>;
}) {
  const [terms, setTerms] = useState(false);
  const [rights, setRights] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const identity = session.auth?.identity;
  const verified = !!identity?.email_verified && !identity.is_anonymous;
  const key = `${session.uid}:${access.policy?.sha256 ?? ""}`;
  const [checkedKey, setCheckedKey] = useState(key);
  // Never carry checks across accounts or revised terms.
  if (key !== checkedKey) {
    setCheckedKey(key);
    setTerms(false);
    setRights(false);
    setMessage("");
  }
  const run = async (operation: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      await operation();
    } catch (error) {
      setMessage(serviceAccessMessage(error));
    } finally {
      setBusy(false);
    }
  };
  if (!identity) return null;
  if (access.eligible)
    return (
      <div className={styles.panel}>
        <p>Your account is ready.</p>
        <Link className="button button-dark" href="/create?mode=account">
          Start a font
        </Link>
      </div>
    );
  return (
    <section className={styles.panel} aria-labelledby="service-access-heading">
      <h2 id="service-access-heading">Before your first font.</h2>
      {identity.is_anonymous ? (
        <p>
          Create or link an account to continue. Existing guest work can still
          be recovered.
        </p>
      ) : !verified ? (
        <>
          <p>Verify your email to keep your handwriting account secure.</p>
          <div className={styles.actions}>
            <button
              className="button button-dark"
              disabled={busy || session.auth?.phase === "busy"}
              onClick={() =>
                void run(async () => {
                  await session.identity?.sendVerification();
                })
              }
            >
              Send verification email
            </button>
            <button
              className="text-button"
              disabled={busy || session.auth?.phase === "busy"}
              onClick={() =>
                void run(async () => {
                  await session.identity?.refreshVerification();
                  access.refresh();
                })
              }
            >
              I’ve verified — check again
            </button>
          </div>
        </>
      ) : access.loading ? (
        <p role="status">Checking your account…</p>
      ) : access.policy && !access.policy.ready ? (
        <p>The hosted service is not ready for public use yet.</p>
      ) : access.policy ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              if (!terms || !rights || !access.client || !access.policy) return;
              await access.client.accept(access.policy.sha256);
              access.refresh();
            });
          }}
        >
          <p>Use your own writing, or writing you have permission to use.</p>
          <label>
            <input
              type="checkbox"
              checked={terms}
              onChange={(event) => setTerms(event.target.checked)}
              required
              disabled={busy}
            />
            <span>
              I accept the{" "}
              <Link href="/terms" target="_blank">
                Terms
              </Link>{" "}
              and have read the{" "}
              <Link href="/privacy" target="_blank">
                Privacy notice
              </Link>
              .
            </span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={rights}
              onChange={(event) => setRights(event.target.checked)}
              required
              disabled={busy}
            />
            <span>
              I own this handwriting or have permission to digitise and use it.
            </span>
          </label>
          <p className={styles.note}>
            Training is separate and optional. These checks do not give training
            permission.
          </p>
          <button
            className="button button-dark"
            type="submit"
            disabled={busy || !terms || !rights}
          >
            Accept and continue
          </button>
          <details>
            <summary>Read this exact version · {access.policy.version}</summary>
            {(
              access.policy.document.sections as {
                heading: string;
                body: string;
              }[]
            ).map((section) => (
              <div key={section.heading}>
                <h3>{section.heading}</h3>
                <p>{section.body}</p>
              </div>
            ))}
            <p>
              Operator:{" "}
              {(access.policy.document.operator as { name: string | null })
                .name || "not yet published — local draft only"}
            </p>
          </details>
        </form>
      ) : null}
      {session.auth?.error && <p role="alert">{session.auth.error}</p>}
      {session.auth?.message && <p role="status">{session.auth.message}</p>}
      {(message || access.error) && (
        <p role="alert">
          {message || access.error}{" "}
          <button className="text-button" onClick={access.refresh}>
            Retry
          </button>
        </p>
      )}
    </section>
  );
}
