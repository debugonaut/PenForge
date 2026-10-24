"use client";

import Link from "next/link";
import { useState } from "react";
import { PenCharacter } from "@/components/brand/Characters";
import { StepMascot, StepStage } from "./StepStage";
import { FileIcon } from "./WizardIcons";
import type { AnnouncedPanelProps } from "./creation-state";
import type { ExportFormat } from "@/lib/contracts";
import { fontService } from "@/lib/mock-services";

export function ExportPanel({
  project,
  go,
  announce,
}: Pick<AnnouncedPanelProps, "project" | "go" | "announce">) {
  const [formats, setFormats] = useState<ExportFormat[]>(["ttf"]);
  const [result, setResult] = useState<{
    missing: string[];
    warnings: string[];
  } | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function check() {
    if (busy || !formats.length) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const validation = await fontService.validate(project.id);
      setResult(validation);
      const response = await fontService.requestExport(project.id, formats);
      setMessage(response.message || "No font file was created.");
      announce("Demo export readiness checked. No file was created.");
    } catch {
      setError(
        "Readiness could not be checked. Your project is still available; try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="step-content">
      <div className="step-intro">
        <div className="step-intro-copy">
          <span className="eyebrow">06 / EXPORT</span>
          <h1>Almost in your hands.</h1>
          <p>
            Font generation is still a demo, but you can check the intended
            formats and readiness here. Your project stays saved locally.
          </p>
        </div>
        <StepMascot character="quill" />
      </div>
      <StepStage tone="yellow" number="06">
      <div className="export-workspace">
        <div className="export-panel">
          <span className="demo-tag">DEMO · NO FONT FILE GENERATED</span>
          <h2>Choose the formats you would like</h2>
          <div className="format-grid">
            {(["ttf", "otf", "woff2"] as const).map((format) => (
              <label
                key={format}
                className={formats.includes(format) ? "active" : ""}
              >
                <input
                  type="checkbox"
                  checked={formats.includes(format)}
                  disabled={busy}
                  onChange={() => {
                    setMessage("");
                    setFormats((current) =>
                      current.includes(format)
                        ? current.filter((item) => item !== format)
                        : [...current, format],
                    );
                  }}
                />
                <FileIcon className="format-icon" />
                <strong>{format.toUpperCase()}</strong>
                <small>
                  {format === "woff2"
                    ? "For the web"
                    : format === "otf"
                      ? "For creative apps"
                      : "For everyday use"}
                </small>
              </label>
            ))}
          </div>
          <button
            className="button button-dark"
            disabled={!formats.length || busy}
            onClick={() => void check()}
          >
            {busy ? "Checking…" : "Check readiness"} <span>→</span>
          </button>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          {message && (
            <div className="export-result" role="status">
              <strong>{message}</strong>
              {result && (
                <>
                  <p>
                    {result.missing.length} characters are missing or
                    unavailable in this demo.
                  </p>
                  {result.warnings.map((warning) => (
                    <p key={warning}>{warning}</p>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
        <aside className="export-aside">
          <PenCharacter mood="happy" />
          <h3>Keep the draft.</h3>
          <p>
            You can return to this workspace and continue shaping letters. The
            browser stores demo project data; it is not synced or backed up to
            an account.
          </p>
          <Link href="/projects" className="small-link">
            Open Creative Workbench →
          </Link>
        </aside>
      </div>
      <div className="step-actions" style={{ flexWrap: "wrap" }}>
        <button className="text-button" onClick={() => go("preview")}>
          ← Back to preview
        </button>
        <Link href="/projects" className="small-link">
          See my projects →
        </Link>
        <Link
          href={`/write?project=${encodeURIComponent(project.id)}`}
          className="button button-dark"
        >
          Open writing desk <span>→</span>
        </Link>
      </div>
      </StepStage>
    </div>
  );
}
