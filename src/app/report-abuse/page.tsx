"use client";

import { useState, type FormEvent } from "react";
import { LegalPage } from "@/components/legal/LegalPage";
import { getPenforgeAuthConfiguration } from "@/lib/auth-service";
import styles from "@/components/account/ServiceAccessPanel.module.css";

export default function ReportAbusePage() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [receipt, setReceipt] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setMessage("");
    const config = getPenforgeAuthConfiguration(window.location.hostname);
    if (config.state !== "configured") {
      setMessage(
        "Reporting is not connected in this local build. No report was sent. Public contact details must be added before launch.",
      );
      return;
    }
    const data = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const response = await fetch(
        new URL("/api/v1/abuse-reports", config.apiOrigin),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({
            category: data.get("category"),
            details: data.get("details"),
            contact: data.get("contact") || null,
          }),
          credentials: "omit",
          redirect: "error",
          cache: "no-store",
          referrerPolicy: "no-referrer",
        },
      );
      if (!response.ok) {
        setMessage(
          response.status === 429
            ? "Too many reports. Please try again later."
            : "The report could not be saved. Please try again.",
        );
        return;
      }
      const result = await response.json();
      if (
        typeof result.report_id !== "string" ||
        !/^[0-9a-f-]{36}$/.test(result.report_id)
      )
        throw new Error("invalid_receipt");
      setReceipt(result.report_id);
    } catch {
      setMessage("Couldn’t reach Penforge. No saved report was confirmed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <LegalPage
      eyebrow="Report abuse"
      title="Something isn’t right?"
      lede="Tell us about suspected impersonation, fraud or misuse of someone’s handwriting. A report does not automatically suspend an account."
      updated="Pre-release reporting · Operator identity and public contact must be added before public launch."
      sections={[
        {
          heading: "Send a private report",
          body: (
            <span>
              Describe what happened and where you encountered it. Don’t send
              signatures, identity documents or private handwriting. Reports are
              stored for review; response times are not yet established.
            </span>
          ),
        },
        {
          heading: "What happened",
          body: receipt ? (
            <span role="status">
              Report saved. Your reference is {receipt}. Keep this reference if
              you contact the operator.
            </span>
          ) : (
            <form className={styles.panel} onSubmit={submit}>
              <label>
                Concern
                <select name="category" required disabled={busy}>
                  <option value="impersonation">Impersonation</option>
                  <option value="fraud">Fraud</option>
                  <option value="rights">Handwriting rights</option>
                  <option value="other">Other misuse</option>
                </select>
              </label>
              <label>
                Details
                <textarea
                  name="details"
                  required
                  minLength={20}
                  maxLength={4000}
                  rows={6}
                  disabled={busy}
                />
              </label>
              <label>
                Contact email (optional)
                <input
                  name="contact"
                  type="email"
                  maxLength={254}
                  disabled={busy}
                />
              </label>
              <button
                type="submit"
                className="button button-dark"
                disabled={busy}
              >
                {busy ? "Saving report…" : "Send report"}
              </button>
              {message && <span role="alert">{message}</span>}
            </form>
          ),
        },
      ]}
    />
  );
}
