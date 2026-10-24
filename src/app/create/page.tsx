"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  BrandMark,
  PenCharacter,
  QuillCharacter,
} from "@/components/brand/Characters";
import { CapturePanel } from "@/components/creation/CapturePanel";
import { ReviewPanel } from "@/components/creation/ReviewPanel";
import { EditPanel } from "@/components/creation/EditPanel";
import { StylePanel } from "@/components/creation/StylePanel";
import { PreviewPanel } from "@/components/creation/PreviewPanel";
import { ExportPanel } from "@/components/creation/ExportPanel";
import { AccountStudio } from "@/components/creation/AccountStudio";
import {
  AlertIcon,
  CheckGridIcon,
  CheckIcon,
  CropIcon,
  DownloadIcon,
  EyeIcon,
  PenIcon,
  SlidersIcon,
} from "@/components/creation/WizardIcons";
import { WizardAction } from "@/components/creation/WizardAction";
import { availableStep } from "@/components/creation/creation-state";
import { LoadingScene } from "@/components/shared/LoadingScene";
import type { GlyphHistoryStore } from "@/components/creation/creation-state";
import type {
  CreationStep,
  FontProject,
  ProjectChanges,
} from "@/lib/contracts";
import { steps } from "@/lib/contracts";
import { guestPolicy, projectService } from "@/lib/mock-services";
import { ServiceAccessView } from "@/components/account/ServiceAccessPanel";
import { useServiceAccess } from "@/lib/use-service-access";
import { useAccountSession } from "@/lib/use-account-session";
import {
  createAccountProjectService,
  type AccountProject,
  type SaveSnapshot,
  type WorkspaceSaver,
} from "@/lib/account-project-service";
import { isAccountError } from "@/lib/account-errors";
import styles from "./create.module.css";

const stepIcons = {
  capture: PenIcon,
  review: CheckGridIcon,
  edit: CropIcon,
  style: SlidersIcon,
  preview: EyeIcon,
  export: DownloadIcon,
};

type Mode = "account" | "demo";

function CreateExperience() {
  const router = useRouter();
  const params = useSearchParams();
  const id = params.get("project");
  const rawStep = params.get("step");
  const rawMode = params.get("mode");
  const session = useAccountSession();
  const access = useServiceAccess(session);
  let mode: Mode | null = null;
  if (session.settled) {
    if (rawMode === "demo" || rawMode === "account") mode = rawMode;
    else mode = "account";
  }
  const [loadedProject, setProject] = useState<FontProject | null>(null);
  // A URL mode change must not render the previous mode's project while loading.
  const project = loadedProject?.origin.kind === mode ? loadedProject : null;
  const projectRef = useRef<FontProject | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [intro, setIntro] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [save, setSave] = useState<SaveSnapshot>({
    state: "idle",
    message: "",
    pending: 0,
  });
  const [pendingRecovery, setPendingRecovery] = useState(0);
  const creation = useRef<Promise<FontProject> | null>(null);
  const accountService = useRef<ReturnType<
    typeof createAccountProjectService
  > | null>(null);
  const saver = useRef<WorkspaceSaver | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const histories = useRef<GlyphHistoryStore["current"]>({});
  const previousStep = useRef<CreationStep | null>(null);
  const requested = steps.some((item) => item.id === rawStep)
    ? (rawStep as CreationStep)
    : project?.step || "capture";
  const step = project ? availableStep(project, requested) : "capture";
  const isAccount = mode === "account";
  const signedIn = Boolean(session.uid);

  useEffect(() => {
    try {
      if (window.localStorage.getItem("penforge-seen-intro") !== "yes")
        setIntro(true);
    } catch {
      setIntro(true);
    }
  }, []);

  const persistProject = useCallback((next: FontProject) => {
    projectRef.current = next;
    setProject(next);
  }, []);

  function update(change: ProjectChanges) {
    const current = projectRef.current;
    if (!current) return;
    const next: FontProject = {
      ...current,
      ...change,
      updatedAt: new Date().toISOString(),
    };
    persistProject(next);
    if (next.origin.kind === "account") {
      saver.current?.schedule(next);
      return;
    }
    void projectService
      .update(current.id, change)
      .then(() =>
        setSave({
          state: "saved",
          message: "Saved in this browser",
          pending: 0,
        }),
      )
      .catch(() =>
        setSave({
          state: "error",
          message: "Couldn’t save — retry",
          pending: 0,
        }),
      );
  }

  // Demo projects load from the local store when the explicit demo is chosen.
  useEffect(() => {
    if (mode !== "demo") return;
    let active = true;
    setError("");
    setProject(null);
    projectRef.current = null;
    if (!id) {
      creation.current ||= projectService.create("My first font");
      creation.current
        .then((item) => {
          if (active)
            router.replace(`/create?mode=demo&project=${item.id}&step=capture`);
        })
        .catch(() => {
          if (active)
            setError(
              "Your browser could not open a new demo draft. Open the workbench to check browser storage.",
            );
        });
    } else {
      projectService
        .get(id)
        .then((item) => {
          if (active) persistProject(item);
        })
        .catch(() => {
          if (active)
            setError(
              "This demo draft could not be opened. It may belong to another browser.",
            );
        });
    }
    return () => {
      active = false;
    };
  }, [id, mode, persistProject, router]);

  // Account projects load metadata plus durable working state.
  useEffect(() => {
    if (mode !== "account") return;
    if (!signedIn || !session.identity || !access.eligible) return;
    let active = true;
    setError("");
    setProject(null);
    projectRef.current = null;
    saver.current?.dispose();
    saver.current = null;
    let service: ReturnType<typeof createAccountProjectService>;
    try {
      service = createAccountProjectService(session.identity);
    } catch {
      setError("Sign in again to open your account project.");
      return;
    }
    accountService.current = service;
    const open = async () => {
      try {
        const loaded: AccountProject = id
          ? await service.open(id)
          : await service.create("Untitled font");
        if (!active || accountService.current !== service) return;
        if (!id) {
          router.replace(
            `/create?mode=account&project=${loaded.project.id}&step=${loaded.view.step}`,
          );
        }
        persistProject(loaded.view);
        const next = service.saver(loaded.project.id, loaded.version, {
          onState: (snapshot) => {
            if (active) setSave(snapshot);
          },
          onSaved: (version, updatedAt) => {
            const current = projectRef.current;
            if (!current || current.origin.kind !== "account") return;
            projectRef.current = {
              ...current,
              updatedAt,
              origin: { ...current.origin, workspaceVersion: version },
            };
            setProject(projectRef.current);
          },
          onRemote: () => {
            setPendingRecovery(service.pending(loaded.project.id).length);
          },
        });
        saver.current = next;
        const kept = service.pending(loaded.project.id).length;
        setPendingRecovery(kept);
        // Kept changes replay their exact stored bodies on open, so a refresh
        // mid-save heals itself without asking for another click.
        if (kept > 0) {
          setSave({ state: "saving", message: "Saving…", pending: kept });
          void next.retryPending();
        } else
          setSave({
            state: "saved",
            message: "Saved to your account",
            pending: 0,
          });
      } catch (cause) {
        if (!active) return;
        if (isAccountError(cause) && cause.code === "not_found") {
          setError(
            "This project is not available to this account. It may have been deleted or belong to another person.",
          );
        } else if (
          isAccountError(cause) &&
          cause.code === "guest_limit_reached"
        ) {
          setError(
            `This guest has used its ${guestPolicy.displayAllowance} lifetime projects. Sign in to keep working.`,
          );
        } else if (
          isAccountError(cause) &&
          cause.code === "network_unavailable"
        ) {
          setError(
            "Penforge couldn’t reach your account. Check the connection and try again. Nothing was replaced.",
          );
        } else {
          setError(
            "This account project couldn’t be opened. Your saved work has not been changed.",
          );
        }
      }
    };
    void open();
    return () => {
      active = false;
      saver.current?.dispose();
      saver.current = null;
      if (accountService.current === service) accountService.current = null;
    };
  }, [
    id,
    mode,
    persistProject,
    router,
    session.identity,
    signedIn,
    access.eligible,
  ]);

  useEffect(() => {
    setRailOpen(false);
  }, [step]);
  useEffect(() => {
    if (!project) return;
    const prefix = isAccount ? "mode=account" : "mode=demo";
    if (rawStep !== step || rawMode !== (isAccount ? "account" : "demo"))
      router.replace(`/create?${prefix}&project=${project.id}&step=${step}`);
    if (project.step !== step) {
      const next = { ...project, step };
      persistProject(next);
      if (next.origin.kind === "account") saver.current?.schedule(next);
      else
        void projectService.update(project.id, { step }).catch(() =>
          setSave({
            state: "error",
            message: "Couldn’t save — retry",
            pending: 0,
          }),
        );
    }
    if (previousStep.current && previousStep.current !== step) {
      mainRef.current?.focus({ preventScroll: true });
      setNotice(
        `${steps.find((item) => item.id === step)?.label} step opened.`,
      );
    }
    previousStep.current = step;
  }, [isAccount, project, persistProject, rawMode, rawStep, router, step]);
  useEffect(() => {
    if (!intro) return;
    const dialog = dialogRef.current;
    const main = mainRef.current;
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      document.body.style.overflow = overflow;
      if (dialog?.open) dialog.close();
      if (previousFocus?.isConnected && previousFocus !== document.body)
        previousFocus.focus({ preventScroll: true });
      else main?.focus({ preventScroll: true });
    };
  }, [intro]);

  function retrySave() {
    if (save.state === "conflict") return;
    if (projectRef.current?.origin.kind === "account")
      void saver.current?.retryPending();
    else if (projectRef.current) {
      const { id: projectId } = projectRef.current;
      void projectService
        .get(projectId)
        .then((item) => projectService.save(projectRef.current ?? item))
        .then((saved) => {
          persistProject(saved);
          setSave({
            state: "saved",
            message: "Saved in this browser",
            pending: 0,
          });
        })
        .catch(() =>
          setSave({
            state: "error",
            message: "Couldn’t save — retry",
            pending: 0,
          }),
        );
    }
  }

  async function resolveConflict(choice: "mine" | "saved") {
    if (!saver.current) return;
    await saver.current.resolve(choice);
    if (choice === "saved" && accountService.current && projectRef.current) {
      try {
        const refreshed = await accountService.current.open(
          projectRef.current.id,
        );
        persistProject(refreshed.view);
        setNotice("Opened the saved version.");
      } catch {
        setNotice("The saved version could not be reloaded. Refresh the page.");
      }
    }
    setPendingRecovery(0);
  }

  function go(next: CreationStep) {
    const current = projectRef.current;
    if (!current) return;
    const destination = availableStep(current, next);
    if (destination !== next) {
      setNotice(
        destination === "capture"
          ? "Add a handwriting sample before continuing."
          : "Approve at least one letter before continuing.",
      );
      return;
    }
    update({ step: next });
    setNotice("");
    setRailOpen(false);
    const prefix = isAccount ? "mode=account" : "mode=demo";
    router.push(`/create?${prefix}&project=${current.id}&step=${next}`);
    window.scrollTo({
      top: 0,
      behavior:
        document.documentElement.dataset.motion === "off" ||
        window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
    });
  }
  function closeIntro() {
    setIntro(false);
    try {
      window.localStorage.setItem("penforge-seen-intro", "yes");
    } catch {
      /* The intro remains dismissible when browser storage is unavailable. */
    }
  }
  async function connect() {
    if (!session.identity) return;
    setError("");
    try {
      await session.identity.connect();
    } catch {
      setError("Sign-in could not start. Open the account page to try again.");
    }
  }
  const index = steps.findIndex((item) => item.id === step);
  const CurrentStepIcon = stepIcons[step];
  const railHints: Record<CreationStep, string> = {
    capture: "Add a letter or page",
    review: "Approve the letters",
    edit: "Adjust the shapes",
    style: "See your hand, measured",
    preview: "Read it back",
    export: "Download the font",
  };
  const currentAccountProject =
    session.configured &&
    project?.origin.kind === "account" &&
    project.origin.ownerUid === session.uid;
  const accountState = currentAccountProject ? save.state : "idle";
  let accountSaveMessage =
    save.message ||
    (accountState === "saved" ? "Saved to your account" : "Not saved yet");
  if (!session.settled || session.restoring)
    accountSaveMessage = "Opening your account…";
  else if (!session.configured) accountSaveMessage = "Accounts unavailable";
  else if (!signedIn) accountSaveMessage = "Sign in to save";
  else if (!currentAccountProject)
    accountSaveMessage = error
      ? "Project unavailable"
      : "Opening your project…";

  return (
    <div className="studio-app">
      <header className="studio-header">
        <Link href="/" className="wordmark" aria-label="Penforge home">
          <BrandMark />
        </Link>
        <span className="studio-divider" />
        <Link href="/projects" className="back-workbench">
          ← Workbench
        </Link>
        <div
          className={`studio-header-right ${isAccount ? styles.accountHeader : ""}`}
        >
          <span className={isAccount ? styles.accountTag : "demo-tag"}>
            {isAccount
              ? session.auth?.identity?.is_anonymous
                ? "GUEST DESK"
                : "YOUR ACCOUNT"
              : "LOCAL DEMO"}
          </span>
          {isAccount && (
            <span
              className={`${styles.saveChip} ${styles[`save-${accountState}`] ?? ""}`}
              role="status"
              aria-live="polite"
              title={accountSaveMessage}
            >
              {accountState === "saved" && <CheckIcon />}
              {accountState === "error" && <AlertIcon />}
              {accountSaveMessage}
            </span>
          )}
          <WizardAction
            label="Replay the studio introduction"
            hint="Replay intro"
            icon={<PenIcon />}
            onClick={() => setIntro(true)}
          />
        </div>
      </header>
      {intro && (
        <dialog
          ref={dialogRef}
          className="intro-overlay"
          aria-labelledby="intro-title"
          aria-describedby="intro-description"
          onCancel={(event) => {
            event.preventDefault();
            closeIntro();
          }}
          style={{
            margin: 0,
            width: "100%",
            height: "100%",
            maxWidth: "none",
            maxHeight: "none",
            border: 0,
            overflowY: "auto",
          }}
        >
          <div className="intro-card">
            <button autoFocus className="intro-skip" onClick={closeIntro}>
              Skip intro ×
            </button>
            <div className="intro-characters" aria-hidden="true">
              <PenCharacter mood="happy" />
              <QuillCharacter mood="surprised" />
            </div>
            <span className="scene-number">WELCOME TO THE STUDIO</span>
            <h2 id="intro-title">
              Let&apos;s make something
              <br />
              <em>only you could make.</em>
            </h2>
            <p id="intro-description">
              {isAccount
                ? "Your handwriting, your letters, your font. We keep every step in your account so you can come back any time."
                : "Your pen, your quirks, your type. This is the local demo — a guided sketch of the real studio."}
            </p>
            <button className="button button-dark" onClick={closeIntro}>
              Let’s begin <span aria-hidden="true">→</span>
            </button>
          </div>
        </dialog>
      )}
      <div className="studio-shell">
        <aside
          className={`studio-rail${railOpen ? " is-open" : ""}`}
          aria-label="Creation steps"
        >
          <button
            className="rail-toggle"
            aria-expanded={railOpen}
            aria-controls="studio-rail-details"
            onClick={() => setRailOpen((open) => !open)}
          >
            <span className="rail-toggle-step">
              STEP {String(index + 1).padStart(2, "0")} / 06
            </span>
            <span className="rail-toggle-label">
              <CurrentStepIcon className="rail-current-icon" />
              {steps[index].label}
              <span aria-hidden="true">⌄</span>
            </span>
          </button>
          <div className="rail-details" id="studio-rail-details">
            <div className="rail-project">
              <span>WORKING ON</span>
              <strong>{project?.name || "Opening your desk…"}</strong>
            </div>
            <nav className="rail-steps" aria-label="Creation steps">
              {steps.map((item, itemIndex) => {
                const StepIcon = stepIcons[item.id];
                return (
                  <button
                    key={item.id}
                    onClick={() => go(item.id)}
                    disabled={
                      !project || availableStep(project, item.id) !== item.id
                    }
                    aria-current={step === item.id ? "step" : undefined}
                    title={`${item.label} · ${railHints[item.id]}`}
                    className={`${step === item.id ? "active" : ""} ${itemIndex < index ? "visited" : ""}`}
                  >
                    <span className="rail-marker" aria-hidden="true">
                      <StepIcon />
                      {itemIndex < index && (
                        <span className="rail-visited-mark">
                          <CheckIcon />
                        </span>
                      )}
                    </span>
                    <span className="rail-copy">
                      <strong>
                        <span className="rail-step-number" aria-hidden="true">
                          {String(itemIndex + 1).padStart(2, "0")}
                        </span>
                        {item.label}
                      </strong>
                      <small>{railHints[item.id]}</small>
                    </span>
                  </button>
                );
              })}
            </nav>
            <div className="rail-footer">
              <span>
                Step {index + 1} of {steps.length}
              </span>
              <span>{steps.length - index - 1} to go</span>
            </div>
          </div>
        </aside>
        <main
          id="main-content"
          ref={mainRef}
          className={`studio-main${step === "capture" && !isAccount ? " capture-main-page" : ""}`}
          tabIndex={-1}
          aria-label={`${steps[index].label} workspace`}
        >
          {isAccount && (
            <div className={styles.accountBar}>
              <span className={styles.accountContext}>
                {signedIn
                  ? "Private · saved to this account"
                  : session.restoring
                    ? "Opening your desk…"
                    : "Sign in to open this project"}
              </span>
              {save.state === "error" && (
                <span className={styles.saveAction}>
                  <button className="text-button" onClick={retrySave}>
                    Try saving again
                  </button>
                </span>
              )}
              {save.state === "conflict" && (
                <span className={styles.conflict}>
                  <strong>This project changed in another tab.</strong>
                  <button
                    className="text-button"
                    onClick={() => void resolveConflict("mine")}
                  >
                    Keep my changes
                  </button>
                  <button
                    className="text-button"
                    onClick={() => void resolveConflict("saved")}
                  >
                    Open the saved version
                  </button>
                </span>
              )}
              {pendingRecovery > 0 && save.state !== "saved" && (
                <span className={styles.saveAction}>
                  <button className="text-button" onClick={retrySave}>
                    Retry {pendingRecovery} kept change
                    {pendingRecovery === 1 ? "" : "s"}
                  </button>
                </span>
              )}
            </div>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}{" "}
              {isAccount && session.identity && !signedIn ? (
                <button className="text-button" onClick={() => void connect()}>
                  Sign in
                </button>
              ) : (
                <Link href="/projects">Open Workbench</Link>
              )}
            </p>
          )}
          {!error && mode === null ? (
            <LoadingScene inline message="Opening your creative desk…" />
          ) : isAccount &&
            !signedIn &&
            session.settled &&
            !session.restoring ? (
            <section className={styles.signIn} aria-labelledby="studio-signin">
              <QuillCharacter mood="surprised" />
              <div>
                <span className="scene-number">PRIVATE STUDIO</span>
                <h1 id="studio-signin">Open your desk.</h1>
                <p>
                  Your letters and fonts belong to your account. Sign in to open
                  your private studio.
                </p>
                <div className={styles.signInActions}>
                  <button
                    className="button button-dark"
                    onClick={() => router.push("/account")}
                    disabled={!session.configured}
                  >
                    Continue
                  </button>
                  <Link className="text-button" href="/projects">
                    Open Workbench
                  </Link>
                </div>
                {!session.configured && (
                  <p className={styles.muted}>
                    Accounts aren’t configured in this build.{" "}
                    <Link href="/create?mode=demo">Try the local demo</Link>.
                  </p>
                )}
              </div>
            </section>
          ) : isAccount && signedIn && !access.eligible ? (
            <ServiceAccessView
              key={session.uid}
              session={session}
              access={access}
            />
          ) : !project ? (
            <LoadingScene inline message="Opening your creative desk…" />
          ) : isAccount ? (
            <div key={project.id}>
              <AccountStudio
                project={project}
                step={step}
                go={go}
                update={update}
                announce={setNotice}
              />
            </div>
          ) : (
            <div
              key={project.id}
              className={step === "capture" ? "capture-host" : undefined}
            >
              {step === "capture" && (
                <CapturePanel
                  project={project}
                  update={update}
                  go={go}
                  announce={setNotice}
                />
              )}
              {step === "review" && (
                <ReviewPanel
                  project={project}
                  update={update}
                  go={go}
                  announce={setNotice}
                />
              )}
              {step === "edit" && (
                <EditPanel
                  project={project}
                  update={update}
                  go={go}
                  histories={histories}
                />
              )}
              {step === "style" && (
                <StylePanel
                  project={project}
                  update={update}
                  go={go}
                  announce={setNotice}
                />
              )}
              {step === "preview" && (
                <PreviewPanel project={project} update={update} go={go} />
              )}
              {step === "export" && (
                <ExportPanel project={project} go={go} announce={setNotice} />
              )}
            </div>
          )}
        </main>
      </div>
      <div className="sr-only" role="status" aria-live="polite">
        {notice}
      </div>
      <footer className="studio-footer">
        <span>
          {isAccount ? "Penforge account studio" : "Penforge creative demo"}
        </span>
        <span>
          {isAccount
            ? session.auth?.identity?.is_anonymous
              ? "Guest work stays on this browser until you sign in."
              : "Your projects resume wherever you sign in."
            : `Guest allowance illustration: ${guestPolicy.displayAllowance} creations · not enforced`}
        </span>
        <span>
          {isAccount
            ? accountSaveMessage
            : save.state === "error"
              ? "Local changes awaiting save"
              : "Saved in this browser only"}
        </span>
      </footer>
    </div>
  );
}

export default function CreatePage() {
  return (
    <Suspense fallback={<LoadingScene message="Opening your creative desk…" />}>
      <CreateExperience />
    </Suspense>
  );
}
