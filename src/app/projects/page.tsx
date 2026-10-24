"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  BrandMark,
  PenCharacter,
  QuillCharacter,
} from "@/components/brand/Characters";
import {
  steps,
  type FontProject,
  type ProjectStorageInfo,
} from "@/lib/contracts";
import { guestPolicy, projectService } from "@/lib/mock-services";
import { useConfirmation } from "@/components/shared/ConfirmationDialog";
import { LoadingScene } from "@/components/shared/LoadingScene";
import { WizardAction } from "@/components/creation/WizardAction";
import {
  AlertIcon,
  ArrowUpRightIcon,
  DownloadIcon,
  PencilIcon,
  TrashIcon,
  UploadIcon,
} from "@/components/creation/WizardIcons";
import { useAccountSession } from "@/lib/use-account-session";
import {
  AccountProjectService,
  createAccountProjectService,
} from "@/lib/account-project-service";
import type { CloudProjectView } from "@/lib/api-boundary";
import { emptyWorkspace } from "@/lib/account-workspace";
import { isAccountError } from "@/lib/account-errors";
import styles from "./workbench.module.css";

type Summary = {
  updatedAt: string;
  step: string;
  previewText: string;
  readyExportId: string | null;
  glyphCount: number;
};

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export default function ProjectsPage() {
  const router = useRouter();
  const { confirm, confirmationDialog } = useConfirmation();
  const session = useAccountSession();
  const account = useRef<AccountProjectService | null>(null);
  const [cloudProjects, setCloudProjects] = useState<CloudProjectView[]>([]);
  const [summaries, setSummaries] = useState<Record<string, Summary>>({});
  const [accountLoaded, setAccountLoaded] = useState(false);
  const [accountError, setAccountError] = useState("");
  const [accountBusy, setAccountBusy] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [renameError, setRenameError] = useState("");
  const renameInput = useRef<HTMLInputElement>(null);

  const [projects, setProjects] = useState<FontProject[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [storageInfo, setStorageInfo] = useState<ProjectStorageInfo | null>(
    null,
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const readVersion = useRef(0);
  const renameId = renameDraft?.id;

  useEffect(() => {
    if (renameId) {
      renameInput.current?.focus();
      renameInput.current?.select();
    }
  }, [renameId]);

  const refresh = useCallback(async () => {
    const version = ++readVersion.current;
    try {
      const info = await projectService.storageInfo();
      const items = info.state === "ready" ? await projectService.list() : null;
      if (version !== readVersion.current) return false;
      setStorageInfo(info);
      if (items) setProjects(items);
      setLoaded(true);
      return info.state === "ready";
    } catch (cause) {
      if (version !== readVersion.current) return false;
      setStorageInfo(null);
      setError(errorMessage(cause, "Could not read projects from this browser."));
      setLoaded(true);
      return false;
    }
  }, []);

  useEffect(() => {
    void refresh();
    function syncDrafts(event: StorageEvent) {
      if (event.key === null || event.key.startsWith("penforge-projects"))
        void refresh();
    }
    window.addEventListener("storage", syncDrafts);
    return () => {
      readVersion.current += 1;
      window.removeEventListener("storage", syncDrafts);
    };
  }, [refresh]);

  const uid = session.uid;

  const loadAccount = useCallback(
    async (service: AccountProjectService) => {
      setAccountError("");
      try {
        const items = await service.list();
        if (account.current !== service) return;
        setCloudProjects(items);
        setAccountLoaded(true);
        const entries = await Promise.all(
          items.slice(0, 24).map(async (project) => {
            try {
              const loadedWorkspace = await service.workspace(project.id);
              const workspace = loadedWorkspace?.workspace ?? emptyWorkspace("Latn");
              const ready = workspace.exports.find(
                (item) => item.status === "ready",
              );
              return [
                project.id,
                {
                  updatedAt: loadedWorkspace?.updatedAt ?? project.updatedAt,
                  step: workspace.step,
                  previewText: workspace.previewText,
                  readyExportId:
                    workspace.selectedExportId ??
                    ready?.exportId ??
                    null,
                  glyphCount: workspace.glyphs.length,
                } satisfies Summary,
              ] as const;
            } catch {
              return null;
            }
          }),
        );
        if (account.current !== service) return;
        setSummaries(Object.fromEntries(entries.filter(Boolean) as Array<readonly [string, Summary]>));
      } catch (cause) {
        if (account.current !== service) return;
        setAccountLoaded(true);
        setAccountError(
          isAccountError(cause)
            ? "Your account projects couldn’t be opened. Check the connection and try again."
            : "Your account projects couldn’t be opened. Your local demo drafts are unchanged.",
        );
      }
    },
    [],
  );

  useEffect(() => {
    if (!session.settled) return;
    if (!session.configured || !uid || !session.identity) {
      account.current = null;
      setCloudProjects([]);
      setSummaries({});
      setAccountLoaded(true);
      return;
    }
    let service: AccountProjectService;
    try {
      service = createAccountProjectService(session.identity);
    } catch {
      setAccountLoaded(true);
      setAccountError("Sign in again to open your account projects.");
      return;
    }
    account.current = service;
    setAccountLoaded(false);
    void loadAccount(service);
    return () => {
      if (account.current === service) account.current = null;
    };
  }, [loadAccount, session.configured, session.identity, session.settled, uid]);

  async function startGuestOrSignIn() {
    if (!session.identity) {
      router.push("/account");
      return;
    }
    setAccountBusy("connect");
    setAccountError("");
    try {
      await session.identity.connect();
    } catch {
      setAccountError("Sign-in could not start. Try again from the account page.");
    } finally {
      setAccountBusy(null);
    }
  }

  async function createAccountProject() {
    if (!account.current || accountBusy) return;
    setAccountBusy("create");
    setAccountError("");
    try {
      const created = await account.current.create("Untitled font");
      router.push(`/create?mode=account&project=${created.project.id}&step=capture`);
    } catch (cause) {
      setAccountError(
        isAccountError(cause) && cause.code === "guest_limit_reached"
          ? `This guest identity has used its ${guestPolicy.displayAllowance} lifetime projects. Sign in to keep working, or open the local demo below.`
          : "A new project could not be created. Check your connection and try again.",
      );
    } finally {
      setAccountBusy(null);
    }
  }

  async function rename(project: CloudProjectView) {
    if (!account.current || !renameDraft || renameDraft.id !== project.id) return;
    const name = renameDraft.name.trim();
    if (!name || name.length > 120) {
      setRenameError("Use a project name between 1 and 120 characters.");
      return;
    }
    if (name === project.name) {
      setRenameDraft(null);
      return;
    }
    setAccountBusy(project.id);
    setAccountError("");
    try {
      const saved = await account.current.rename(project, name);
      setCloudProjects((current) =>
        current.map((item) => (item.id === saved.id ? saved : item)),
      );
      setRenameDraft(null);
      setNotice(`Renamed to “${saved.name}”.`);
    } catch (cause) {
      if (isAccountError(cause) && cause.code === "conflict") {
        setRenameError("This project changed elsewhere. Reopen it and try again.");
      } else {
        setRenameError("The new name could not be saved. Your project is unchanged.");
      }
    } finally {
      setAccountBusy(null);
    }
  }

  async function removeAccountProject(project: CloudProjectView) {
    if (
      !account.current ||
      !(await confirm({
        title: "Delete this project?",
        message: `Delete “${project.name}” from your account? Its private captures, letters and exports are removed by the deletion service. This cannot be undone.`,
        confirmLabel: "Delete project",
        destructive: true,
      }))
    )
      return;
    setAccountBusy(project.id);
    setAccountError("");
    try {
      await account.current.remove(project);
      setCloudProjects((current) => current.filter((item) => item.id !== project.id));
      setNotice(`Deletion started for “${project.name}”. Private access is closed immediately.`);
    } catch {
      setAccountError("Deletion could not be requested. Nothing was deleted.");
    } finally {
      setAccountBusy(null);
    }
  }

  async function importDemo(project: FontProject) {
    if (!account.current) return;
    if (
      !(await confirm({
        title: "Import this demo draft?",
        message: `Creates a new account project from “${project.name}”: your letters, drawing strokes, style, preview words and notes. Uploaded images are not uploaded again — add the original in Capture. Your demo draft stays in this browser.`,
        confirmLabel: "Import to my account",
      }))
    )
      return;
    setBusy(project.id);
    setAccountError("");
    try {
      const imported = await account.current.importDemoProject(project);
      setNotice(`Imported “${project.name}”. The local demo copy is untouched.`);
      router.push(
        `/create?mode=account&project=${imported.project.id}&step=${imported.view.step}`,
      );
    } catch {
      setAccountError(
        "The demo draft could not be imported. Its local copy is unchanged.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function changeProject(
    id: string,
    action: () => Promise<unknown>,
    message: string,
  ) {
    if (busy) return false;
    setBusy(id);
    setError("");
    setNotice("");
    try {
      await action();
      const refreshed = await refresh();
      if (refreshed) setNotice(message);
      return refreshed;
    } catch (cause) {
      setError(
        errorMessage(cause, "This change could not be saved. Please try again."),
      );
      await refresh();
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function duplicate(project: FontProject) {
    await changeProject(
      project.id,
      () => projectService.duplicate(project.id),
      `Created a copy of “${project.name}”.`,
    );
  }

  async function removeLocal(project: FontProject) {
    if (
      !(await confirm({
        title: "Delete this demo draft?",
        message: `Delete “${project.name}” from this browser? This cannot be undone, and it is not connected to your account.`,
        confirmLabel: "Delete draft",
        destructive: true,
      }))
    )
      return;
    await changeProject(
      project.id,
      () => projectService.remove(project.id),
      `Deleted “${project.name}”.`,
    );
  }

  async function recover() {
    await changeProject(
      "storage",
      () => projectService.recoverLocalData(),
      "Recovered the readable drafts. The original saved data is backed up in this browser.",
    );
  }

  async function downloadSavedData() {
    if (busy) return;
    setBusy("download");
    setError("");
    try {
      const raw = await projectService.recoveryBackup();
      if (raw === null) throw new Error("There is no saved recovery data to download.");
      const url = URL.createObjectURL(
        new Blob([raw], { type: "text/plain;charset=utf-8" }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = "penforge-saved-drafts.txt";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("Saved draft data downloaded.");
    } catch (cause) {
      setError(errorMessage(cause, "Could not download the saved data."));
    } finally {
      setBusy(null);
    }
  }

  const storageReady = storageInfo?.state === "ready";
  const needsRecovery =
    storageInfo?.state === "needs-recovery" ||
    storageInfo?.state === "unsupported";
  const accountReady = session.configured && uid;
  const accountName = session.auth?.identity?.is_anonymous
    ? "Guest desk"
    : "Your account";

  return (
    <div className="workbench-app">
      {confirmationDialog}
      <aside className="workbench-sidebar">
        <Link href="/" className="wordmark" aria-label="Penforge home">
          <BrandMark />
        </Link>
        <div className="sidebar-label">YOUR STUDIO</div>
        <nav aria-label="Workbench navigation">
          <Link href="/projects" aria-current="page">
            ▦ &nbsp; Workbench
          </Link>
          <Link href="/create?mode=account">✎ &nbsp; Create a font</Link>
          <Link href="/write">▤ &nbsp; Writing desk</Link>
          <Link href="/account">◎ &nbsp; Account</Link>
          <Link href="/">↗ &nbsp; Explore the story</Link>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-characters" aria-hidden="true">
            <PenCharacter mood="happy" />
            <QuillCharacter mood="happy" />
          </div>
          <p>A desk for your best ideas.</p>
          <small>
            {accountReady
              ? "Projects save to your account."
              : "Your drafts live in this browser."}
          </small>
        </div>
      </aside>
      <div className="workbench-content">
        <header className="workbench-top">
          <span>CREATIVE WORKBENCH</span>
          <div className={styles.topActions}>
            <span className={`${styles.accountChip} ${accountReady ? styles.accountChipOn : ""}`}>
              {accountReady ? accountName : "Not signed in"}
            </span>
            <Link href="/account" className="small-link">
              {accountReady ? "Account settings" : "Sign in"} ↗
            </Link>
          </div>
        </header>
        <main id="main-content">
          <section className="workbench-hero">
            <div>
              <span className="scene-number">
                {accountReady ? "WELCOME BACK" : "WELCOME TO YOUR DESK"}
              </span>
              <h1>
                A new letter
                <br />
                <em>starts here.</em>
              </h1>
              <p>
                Your projects keep their handwriting, letters, style and font in
                your account — ready on any device you sign in to.
              </p>
              {accountReady ? (
                <button
                  className="button button-dark"
                  onClick={() => void createAccountProject()}
                  disabled={accountBusy !== null}
                >
                  {accountBusy === "create" ? "Opening a fresh page…" : "Create a font"}
                  <span aria-hidden="true">→</span>
                </button>
              ) : session.configured ? (
                <div className={styles.heroActions}>
                  <button
                    className="button button-dark"
                    onClick={() => void startGuestOrSignIn()}
                    disabled={accountBusy !== null}
                  >
                    {accountBusy === "connect" ? "Opening…" : "Start a project"}
                    <span aria-hidden="true">→</span>
                  </button>
                  <Link href="/account" className="text-button">
                    Sign in to keep it across devices
                  </Link>
                </div>
              ) : (
                <Link className="button button-dark" href="/create?mode=demo">
                  Try the demo <span aria-hidden="true">→</span>
                </Link>
              )}
            </div>
            <div className="workbench-hero-art" aria-hidden="true">
              <div className="desk-sheet">
                <span>
                  from my hand,
                  <br />
                  to the page.
                </span>
                <small>ABCDEFGHIJKLMNOPQRSTUVWXYZ</small>
              </div>
              <PenCharacter mood="happy" />
              <QuillCharacter />
            </div>
          </section>

          {!session.configured && session.settled && (
            <p className={styles.inlineNote} role="status">
              <AlertIcon />
              Accounts aren’t configured in this build. The local demo below
              still works; account sign-in stays unavailable rather than
              pretending to save.
            </p>
          )}

          {session.configured &&
            !accountReady &&
            session.settled &&
            session.restoring && (
              <LoadingScene
                inline
                message="Opening your desk…"
              />
            )}

          {session.configured &&
            !accountReady &&
            session.settled &&
            !session.restoring && (
            <section className={styles.signInCard} aria-labelledby="signin-heading">
              <QuillCharacter mood="surprised" />
              <div>
                <span className="scene-number">YOUR PROJECTS</span>
                <h2 id="signin-heading">Start now, keep it forever.</h2>
                <p>
                  A guest desk starts a project on this browser. Sign in to find
                  the same work on another device.
                </p>
                <div className={styles.heroActions}>
                  <button
                    className="button button-dark"
                    onClick={() => void startGuestOrSignIn()}
                    disabled={accountBusy !== null}
                  >
                    {accountBusy === "connect"
                      ? "Opening…"
                      : "Continue as a guest"}
                  </button>
                  <Link href="/account" className="text-button">
                    Sign in instead
                  </Link>
                </div>
              </div>
            </section>
          )}

          {accountReady && (
            <>
              <div className="project-heading">
                <div>
                  <span className="scene-number">YOUR ACCOUNT</span>
                  <h2>Your projects</h2>
                </div>
                {accountLoaded && cloudProjects.length > 0 && (
                  <span>
                    {cloudProjects.length} saved project
                    {cloudProjects.length === 1 ? "" : "s"}
                  </span>
                )}
              </div>
              {accountError && (
                <p className="form-error" role="alert">
                  {accountError}{" "}
                  {account.current && (
                    <button
                      className="text-button"
                      onClick={() => {
                        if (account.current) void loadAccount(account.current);
                      }}
                    >
                      Try again
                    </button>
                  )}
                </p>
              )}
              {notice && <p role="status">{notice}</p>}
              {!accountLoaded ? (
                <LoadingScene inline message="Opening your account desk…" />
              ) : cloudProjects.length === 0 ? (
                <div className="workbench-empty">
                  <span className="empty-letter" aria-hidden="true">
                    Aa
                  </span>
                  <div>
                    <h3>Your account desk is ready.</h3>
                    <p>
                      Make your first project. Everything you add is saved to
                      your account as you work.
                    </p>
                    <button
                      className="button button-dark"
                      onClick={() => void createAccountProject()}
                      disabled={accountBusy !== null}
                    >
                      Create your first font
                    </button>
                  </div>
                </div>
              ) : (
                <div className="project-grid">
                  {cloudProjects.map((project, index) => {
                    const summary = summaries[project.id];
                    return (
                      <article
                        className="project-card"
                        key={project.id}
                        aria-busy={accountBusy === project.id}
                      >
                        <div
                          className={`project-specimen specimen-${index % 4}`}
                          aria-hidden="true"
                        >
                          <span className="specimen-index">
                            ACCOUNT / {String(index + 1).padStart(2, "0")}
                          </span>
                          <strong>
                            {summary?.previewText?.slice(0, 23) ||
                              project.name.slice(0, 23)}
                          </strong>
                          <span>
                            {summary?.glyphCount
                              ? `${summary.glyphCount} letters`
                              : "abcdefghijklm"}
                          </span>
                        </div>
                        <div className="project-meta">
                          <h3>{project.name}</h3>
                          <p>
                            Saved to your account {" · "}Last worked on{" "}
                            <time dateTime={summary?.updatedAt ?? project.updatedAt}>
                              {new Date(summary?.updatedAt ?? project.updatedAt).toLocaleDateString(
                                undefined,
                                { month: "short", day: "numeric", year: "numeric" },
                              )}
                            </time>
                            {" · "}
                            {steps.find((step) => step.id === summary?.step)?.label ??
                              "Capture"}
                          </p>
                          {renameDraft?.id === project.id ? (
                            <form
                              onSubmit={(event) => {
                                event.preventDefault();
                                void rename(project);
                              }}
                              aria-label={`Rename ${project.name}`}
                            >
                              <label
                                className="sample-label"
                                htmlFor={`account-name-${project.id}`}
                              >
                                Project name
                              </label>
                              <input
                                ref={renameInput}
                                className="sample-input"
                                id={`account-name-${project.id}`}
                                value={renameDraft.name}
                                maxLength={120}
                                disabled={accountBusy !== null}
                                onChange={(event) => {
                                  setRenameDraft({ id: project.id, name: event.target.value });
                                  setRenameError("");
                                }}
                              />
                              {renameError && (
                                <p className="form-error" role="alert">
                                  {renameError}
                                </p>
                              )}
                              <div className="project-actions">
                                <button type="submit" disabled={accountBusy !== null}>
                                  {accountBusy === project.id ? "Saving…" : "Save name"}
                                </button>
                                <button type="button" onClick={() => setRenameDraft(null)}>
                                  Cancel
                                </button>
                              </div>
                            </form>
                          ) : null}
                          <div className="project-actions">
                            <Link
                              href={`/create?mode=account&project=${project.id}&step=${summary?.step ?? "capture"}`}
                              aria-label={`Open ${project.name}`}
                            >
                              Open <span aria-hidden="true">→</span>
                            </Link>
                            {summary?.readyExportId && (
                              <Link
                                href={`/write?export=${summary.readyExportId}`}
                                aria-label={`Write with ${project.name}`}
                              >
                                Write with it
                              </Link>
                            )}
                            <WizardAction
                              label={`Rename ${project.name}`}
                              hint="Rename project"
                              icon={<PencilIcon />}
                              onClick={(event) => {
                                event.preventDefault();
                                setRenameDraft({ id: project.id, name: project.name });
                                setRenameError("");
                              }}
                              disabled={accountBusy !== null}
                            />
                            <WizardAction
                              label={`Delete ${project.name}`}
                              hint="Delete project"
                              icon={<TrashIcon />}
                              onClick={() => void removeAccountProject(project)}
                              disabled={accountBusy !== null}
                            />
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </>
          )}

          <details className={styles.demoDetails}>
            <summary>
              <span>
                <strong>Local demo</strong>
                <small>
                  Try Penforge without an account · saved in this browser only
                </small>
              </span>
              <span aria-hidden="true">⌄</span>
            </summary>
            <div className={styles.demoBody}>
              <div className="project-heading">
                <div>
                  <span className="scene-number">OPTIONAL DEMO</span>
                  <h2>Demo projects</h2>
                </div>
                {loaded && storageReady && (
                  <span>
                    {projects.length} draft{projects.length === 1 ? "" : "s"} in
                    this browser
                  </span>
                )}
              </div>
              <p className={styles.demoNote}>
                Demo letters and previews are illustrative. They are never
                uploaded by signing in. Importing a demo starts a separate
                account project.
              </p>
              {error && (
                <p className="form-error" role="alert">
                  {error}
                </p>
              )}
              {!loaded ? (
                <LoadingScene inline message="Opening demo drafts…" />
              ) : !storageReady ? (
                <div className={styles.inlineNote}>
                  <AlertIcon />
                  <div>
                    <p>
                      {storageInfo?.message ||
                        "Could not read demo drafts from this browser. Saved data has not been changed."}
                    </p>
                    <div className="step-actions" style={{ flexWrap: "wrap" }}>
                      {storageInfo?.state === "needs-recovery" && (
                        <button className="text-button" onClick={() => void recover()}>
                          Recover readable drafts
                        </button>
                      )}
                      {needsRecovery && (
                        <button className="text-button" onClick={() => void downloadSavedData()}>
                          <DownloadIcon /> Download saved data
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ) : projects.length === 0 ? (
                <p className={styles.demoNote}>
                  No demo drafts yet.{" "}
                  <Link className="text-button" href="/create?mode=demo">
                    Try the demo →
                  </Link>
                </p>
              ) : (
                <div className={styles.demoList}>
                  {projects.map((project) => (
                    <article key={project.id} className={styles.demoRow}>
                      <div>
                        <h3>{project.name}</h3>
                        <p>
                          {steps.find((step) => step.id === project.step)?.label} ·{" "}
                          {new Date(project.updatedAt).toLocaleDateString()}
                        </p>
                      </div>
                      <div className={styles.demoRowActions}>
                        <Link
                          href={`/create?mode=demo&project=${project.id}&step=${project.step}`}
                          className="text-button"
                        >
                          Resume
                        </Link>
                        {accountReady && (
                          <WizardAction
                            label={`Import ${project.name} to my account`}
                            hint="Import to account"
                            icon={<UploadIcon />}
                            onClick={() => void importDemo(project)}
                            disabled={busy !== null}
                          />
                        )}
                        <WizardAction
                          label={`Duplicate ${project.name}`}
                          hint="Duplicate draft"
                          icon={<ArrowUpRightIcon />}
                          onClick={() => void duplicate(project)}
                          disabled={busy !== null}
                        />
                        <WizardAction
                          label={`Delete ${project.name}`}
                          hint="Delete demo draft"
                          icon={<TrashIcon />}
                          onClick={() => void removeLocal(project)}
                          disabled={busy !== null}
                        />
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {storageReady && storageInfo.recoveryAvailable && (
                <p className="workbench-disclosure">
                  A copy of earlier demo data is available.{" "}
                  <button className="text-button" onClick={() => void downloadSavedData()}>
                    Download recovery data
                  </button>
                </p>
              )}
            </div>
          </details>

          <p className="workbench-disclosure">
            Guest projects use the existing {guestPolicy.displayAllowance}
            -project allowance and stay on this browser until you sign in.
            Signing in never uploads local demo handwriting; import asks first.
          </p>
        </main>
      </div>
    </div>
  );
}
