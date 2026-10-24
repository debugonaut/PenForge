"use client";

import Link from "next/link";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { BrandMark } from "@/components/brand/Characters";
import { useConfirmation } from "@/components/shared/ConfirmationDialog";
import {
  IdentityService,
  getAccountIdentityService,
  getPenforgeAuthConfiguration,
  type AuthConfiguration,
  type AuthState,
} from "@/lib/auth-service";
import {
  PASSWORD_MIN_LENGTH,
  passwordMeetsPolicy,
  passwordRules,
  passwordsMatch,
} from "@/lib/password-policy";
import {
  AlertIcon,
  CheckIcon,
  DotIcon,
  GitHubIcon,
  GoogleIcon,
  LockIcon,
  MailIcon,
  NewAccountIcon,
  RefreshIcon,
  ShieldIcon,
  SignInIcon,
  SignOutIcon,
} from "./AccountIcons";
import styles from "./AccountPanel.module.css";

/** Layout effects only matter after hydration; this keeps SSR quiet. */
const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;
import PrivacyControls from "./PrivacyControls";
import { ServiceAccessPanel } from "./ServiceAccessPanel";

const providerLabels: Record<string, string> = {
  anonymous: "Guest",
  password: "Email",
  "google.com": "Google",
  "github.com": "GitHub",
};
const providerLabel = (value: string) => providerLabels[value] ?? value;

export default function AccountPanel() {
  const service = useRef<IdentityService | null>(null);
  const [configuration, setConfiguration] = useState<AuthConfiguration | null>(
    null,
  );
  const [state, setState] = useState<AuthState | null>(null);
  const [mode, setMode] = useState<"sign-in" | "register">("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [confirmationTouched, setConfirmationTouched] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [accountDeleted, setAccountDeleted] = useState(false);
  const formPanel = useRef<HTMLDivElement>(null);
  const panelHeight = useRef(0);
  const heightAnimation = useRef<Animation | null>(null);
  const { confirm, confirmationDialog } = useConfirmation();

  useEffect(() => {
    const config = getPenforgeAuthConfiguration(window.location.hostname);
    const current = getAccountIdentityService(config);
    service.current = current;
    setConfiguration(config);
    setState(current.getSnapshot());
    const unsubscribe = current.subscribe(() =>
      setState(current.getSnapshot()),
    );
    if (config.state === "configured" && current.getSnapshot().phase === "idle")
      void current.connect();
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!state?.pendingClaim) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [state?.pendingClaim]);

  const busy = state?.phase === "busy";
  const identity = state?.identity;
  const available = configuration?.state === "configured";
  const emulator =
    configuration?.state === "configured" ? configuration.emulatorUrl : null;
  const rules = passwordRules(password);
  const passwordValid = passwordMeetsPolicy(password);
  const confirmationValid = passwordsMatch(password, confirmation);
  const registrationBlocked =
    mode === "register" && (!passwordValid || !confirmationValid);
  const showMismatch =
    mode === "register" &&
    confirmation.length > 0 &&
    !confirmationValid &&
    (confirmationTouched || confirmation.length >= password.length);

  useEffect(() => {
    return () => heightAnimation.current?.cancel();
  }, []);

  /** Morph the form area between modes instead of snapping the card height. */
  useIsomorphicLayoutEffect(() => {
    const element = formPanel.current;
    if (!element) return;
    const target = element.offsetHeight;
    const from = panelHeight.current;
    panelHeight.current = target;
    if (!from || from === target) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const gentle =
      document.documentElement.getAttribute("data-motion-variant") === "gentle";
    heightAnimation.current?.cancel();
    element.style.overflow = "hidden";
    const animation = element.animate(
      [{ height: `${from}px` }, { height: `${target}px` }],
      {
        duration: gentle ? 180 : 280,
        easing: "cubic-bezier(0.2, 0, 0, 1)",
      },
    );
    heightAnimation.current = animation;
    animation.finished
      .catch(() => {})
      .finally(() => {
        element.style.overflow = "";
        element.style.height = "";
      });
  }, [mode]);

  function switchMode(next: "sign-in" | "register") {
    if (next === mode) return;
    panelHeight.current = formPanel.current?.offsetHeight ?? 0;
    setMode(next);
    if (next === "sign-in") {
      setConfirmation("");
      setConfirmationTouched(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "register" && registrationBlocked) {
      setConfirmationTouched(true);
      return;
    }
    const value = password;
    setPassword("");
    setConfirmation("");
    setConfirmationTouched(false);
    if (mode === "register") await service.current?.register(email, value);
    else await service.current?.signInEmail(email, value);
  }
  async function leaveSession() {
    if (
      (identity?.is_anonymous || state?.pendingClaim) &&
      !(await confirm({
        title: "Leave this guest session?",
        message: state?.pendingClaim
          ? "The guest proof waiting in this tab will be discarded. Complete the claim first to preserve access to its server work. Your local drafts remain in this browser."
          : "Sign out ends this guest session. Link an account first if you need to keep access to its server work. Your local drafts remain in this browser.",
        confirmLabel: "Sign out",
      }))
    )
      return;
    await service.current?.signOut();
    setConfirmed(false);
    setAccountDeleted(false);
  }

  const linking = Boolean(identity?.is_anonymous);
  const submitLabel =
    mode === "register"
      ? linking
        ? "Link account"
        : "Create account"
      : "Sign in";

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Link className="wordmark" href="/" aria-label="Penforge home">
          <BrandMark />
        </Link>
        <Link className={styles.back} href="/projects">
          Back to my desk <span aria-hidden="true">↗</span>
        </Link>
      </header>
      <main id="main-content" className={styles.main}>
        <div className={styles.scene}>
          <span className={styles.blobMint} aria-hidden="true" />
          <span className={styles.blobCoral} aria-hidden="true" />
          <section
            className={styles.sheet}
            aria-labelledby="account-heading"
            aria-busy={busy}
          >
            <div className={styles.sheetTop}>
              <span className={styles.badge} aria-hidden="true">
                <BrandMark />
              </span>
              <span className={styles.eyebrow}>Your account</span>
            </div>
            <h1 id="account-heading">
              {accountDeleted ? (
                <>
                  Account <em>removed.</em>
                </>
              ) : identity ? (
                identity.is_anonymous ? (
                  <>
                    A guest <em>at the desk.</em>
                  </>
                ) : (
                  <>
                    You’re <em>signed in.</em>
                  </>
                )
              ) : (
                <>
                  Make yourself <em>at home.</em>
                </>
              )}
            </h1>

            {!state ? (
              <p className={styles.feedback} role="status">
                Checking account settings…
              </p>
            ) : !available ? (
              <div className={styles.unavailable} role="status">
                <p className={styles.unavailableTitle}>
                  <AlertIcon aria-hidden="true" />
                  <strong>Accounts aren’t connected here.</strong>
                </p>
                <p>
                  {configuration?.state === "disabled" &&
                  configuration.reason === "invalid-configuration"
                    ? "Sign-in settings need attention before a verified account can be used."
                    : "Firebase sign-in isn’t configured for this installation."}
                </p>
                <Link className="button button-dark" href="/create?mode=demo">
                  Try the local demo <span aria-hidden="true">→</span>
                </Link>
              </div>
            ) : (
              <>
                {emulator && (
                  <p className={styles.emulator}>
                    <AlertIcon aria-hidden="true" />
                    <span>
                      Dev emulator — hosted Firebase verification isn’t
                      exercised.
                    </span>
                  </p>
                )}

                {identity && (
                  <>
                    <div className={styles.identityCard}>
                      <span className={styles.identityDisc} aria-hidden="true">
                        <CheckIcon />
                      </span>
                      <div>
                        <strong>
                          {accountDeleted
                            ? "Account deleted"
                            : identity.is_anonymous
                              ? "Guest session"
                              : "Account connected"}
                        </strong>
                        <p className={styles.identityMeta}>
                          <span>
                            {providerLabel(identity.sign_in_provider)}
                          </span>
                          <span aria-hidden="true">·</span>
                          <span>{identity.tier}</span>
                          {identity.guest_creations.applicable && (
                            <>
                              <span aria-hidden="true">·</span>
                              <span>
                                {identity.guest_creations.remaining} of{" "}
                                {identity.guest_creations.limit} guest creations
                                left
                              </span>
                            </>
                          )}
                        </p>
                      </div>
                    </div>
                    {identity.quotas.configured &&
                      identity.quotas.over_limit && (
                        <p className={styles.warning}>
                          <AlertIcon aria-hidden="true" />
                          <span>
                            Over a configured limit — existing work is kept.
                          </span>
                        </p>
                      )}
                    <div className={styles.actions}>
                      <button
                        className={styles.secondary}
                        disabled={busy}
                        onClick={() => void service.current?.connect()}
                      >
                        <RefreshIcon aria-hidden="true" /> Refresh identity
                      </button>
                      <button
                        className={styles.quietAction}
                        disabled={busy}
                        onClick={() => void leaveSession()}
                      >
                        <SignOutIcon aria-hidden="true" /> Sign out
                      </button>
                    </div>
                  </>
                )}

                {state.pendingClaim && (
                  <section
                    className={styles.claim}
                    aria-labelledby="claim-heading"
                  >
                    <h2 id="claim-heading" className={styles.claimHeading}>
                      Keep your guest work.
                    </h2>
                    <p>
                      This guest’s server projects move to your account. Nothing
                      already stored is dropped.
                    </p>
                    <p className={styles.claimHint}>
                      Keep this tab open — the guest proof lives only in memory.
                    </p>
                    <label className={styles.check}>
                      <input
                        type="checkbox"
                        checked={confirmed}
                        onChange={(event) => setConfirmed(event.target.checked)}
                        disabled={busy}
                      />
                      <span>Move this guest’s server work to my account.</span>
                    </label>
                    <button
                      className={`button button-dark ${styles.claimAction}`}
                      disabled={!confirmed || busy}
                      onClick={() => void service.current?.claim(confirmed)}
                    >
                      Claim guest work <span aria-hidden="true">→</span>
                    </button>
                  </section>
                )}

                {(!identity || identity.is_anonymous) &&
                  !state.pendingClaim && (
                    <div
                      className={`${styles.credentials} ${
                        identity ? styles.credentialsLinked : ""
                      }`}
                    >
                      {identity?.is_anonymous && (
                        <p className={styles.linkNote}>
                          <CheckIcon aria-hidden="true" />
                          <span>
                            Link an account to keep this guest’s server work.
                          </span>
                        </p>
                      )}
                      <div
                        className={styles.modes}
                        role="group"
                        aria-label="Account action"
                      >
                        <span
                          className={styles.modeGlow}
                          data-mode={mode}
                          aria-hidden="true"
                        />
                        <button
                          type="button"
                          aria-pressed={mode === "sign-in"}
                          disabled={busy}
                          onClick={() => switchMode("sign-in")}
                        >
                          <SignInIcon aria-hidden="true" /> Sign in
                        </button>
                        <button
                          type="button"
                          aria-pressed={mode === "register"}
                          disabled={busy}
                          onClick={() => switchMode("register")}
                        >
                          <NewAccountIcon aria-hidden="true" />{" "}
                          {linking ? "Link account" : "Create account"}
                        </button>
                      </div>
                      <div className={styles.formPanel} ref={formPanel}>
                        <form
                          key={mode}
                          className={styles.form}
                          onSubmit={(event) => void submit(event)}
                        >
                          <label htmlFor="account-email">Email</label>
                          <div className={styles.field}>
                            <MailIcon
                              className={styles.fieldIcon}
                              aria-hidden="true"
                            />
                            <input
                              id="account-email"
                              name="email"
                              type="email"
                              autoComplete="email"
                              placeholder="you@example.com"
                              value={email}
                              onChange={(event) => setEmail(event.target.value)}
                              required
                              disabled={busy}
                            />
                          </div>
                          <label htmlFor="account-password">Password</label>
                          <div className={styles.field}>
                            <LockIcon
                              className={styles.fieldIcon}
                              aria-hidden="true"
                            />
                            <input
                              id="account-password"
                              name="password"
                              type="password"
                              autoComplete={
                                mode === "register"
                                  ? "new-password"
                                  : "current-password"
                              }
                              placeholder={
                                mode === "register"
                                  ? `${PASSWORD_MIN_LENGTH}+ characters`
                                  : "Your password"
                              }
                              minLength={
                                mode === "register"
                                  ? PASSWORD_MIN_LENGTH
                                  : undefined
                              }
                              maxLength={128}
                              aria-describedby={
                                mode === "register"
                                  ? "password-rules"
                                  : undefined
                              }
                              value={password}
                              onChange={(event) =>
                                setPassword(event.target.value)
                              }
                              required
                              disabled={busy}
                            />
                          </div>
                          {mode === "register" && (
                            <>
                              <div
                                className={styles.strength}
                                aria-hidden="true"
                              >
                                {rules.map((rule) => (
                                  <span key={rule.id} data-met={rule.met} />
                                ))}
                              </div>
                              <ul
                                id="password-rules"
                                className={styles.rules}
                                aria-label="Password requirements"
                              >
                                {rules.map((rule) => (
                                  <li key={rule.id} data-met={rule.met}>
                                    {rule.met ? (
                                      <CheckIcon aria-hidden="true" />
                                    ) : (
                                      <DotIcon aria-hidden="true" />
                                    )}
                                    <span className="sr-only">
                                      {rule.met ? "Met: " : "Not met: "}
                                    </span>
                                    {rule.label}
                                  </li>
                                ))}
                              </ul>
                              <label htmlFor="account-confirmation">
                                Confirm password
                              </label>
                              <div
                                className={`${styles.field} ${styles.fieldWithCheck}`}
                              >
                                <ShieldIcon
                                  className={styles.fieldIcon}
                                  aria-hidden="true"
                                />
                                <input
                                  id="account-confirmation"
                                  name="password-confirmation"
                                  type="password"
                                  autoComplete="new-password"
                                  placeholder="Repeat your password"
                                  maxLength={128}
                                  value={confirmation}
                                  onChange={(event) =>
                                    setConfirmation(event.target.value)
                                  }
                                  onBlur={() => setConfirmationTouched(true)}
                                  aria-invalid={showMismatch}
                                  aria-describedby={
                                    showMismatch
                                      ? "password-rules confirmation-error"
                                      : "password-rules"
                                  }
                                  required
                                  disabled={busy}
                                />
                                {confirmation && confirmationValid && (
                                  <span
                                    className={styles.fieldCheck}
                                    aria-hidden="true"
                                  >
                                    <CheckIcon />
                                  </span>
                                )}
                              </div>
                              {showMismatch && (
                                <p
                                  id="confirmation-error"
                                  className={styles.fieldError}
                                >
                                  <AlertIcon aria-hidden="true" />
                                  <span>Passwords don’t match yet.</span>
                                </p>
                              )}
                            </>
                          )}
                          <button
                            className="button button-dark"
                            type="submit"
                            disabled={busy || registrationBlocked}
                            aria-describedby={
                              mode === "register" ? "password-rules" : undefined
                            }
                          >
                            {submitLabel} <span aria-hidden="true">→</span>
                          </button>
                        </form>
                      </div>
                      <div className={styles.divider} aria-hidden="true">
                        <span>or</span>
                      </div>
                      <div className={styles.providers}>
                        <button
                          type="button"
                          className={styles.provider}
                          disabled={busy}
                          aria-label="Continue with Google"
                          onClick={() => void service.current?.signInGoogle()}
                        >
                          <GoogleIcon />
                          <span>Google</span>
                        </button>
                        <button
                          type="button"
                          className={styles.provider}
                          disabled={busy}
                          aria-label="Continue with GitHub"
                          onClick={() => void service.current?.signInGithub()}
                        >
                          <GitHubIcon />
                          <span>GitHub</span>
                        </button>
                      </div>
                      {!identity && (
                        <div className={styles.guestRow}>
                          <button
                            type="button"
                            className={styles.quietAction}
                            disabled={busy}
                            onClick={() => void service.current?.connect()}
                          >
                            Resume this tab’s session
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                {identity && !accountDeleted && (
                  <ServiceAccessPanel
                    session={{
                      configuration,
                      identity: service.current,
                      auth: state,
                      uid: identity.firebase_uid,
                    }}
                  />
                )}
                {busy && (
                  <p role="status" className={styles.feedback}>
                    Checking your account…
                  </p>
                )}
                {state.error && (
                  <p role="alert" className={styles.error}>
                    <AlertIcon aria-hidden="true" />
                    <span>{state.error}</span>
                  </p>
                )}
                {state.message && (
                  <p role="status" className={styles.feedback}>
                    {state.message}
                  </p>
                )}
                {!identity && (
                  <p className={styles.localNote}>
                    <LockIcon aria-hidden="true" />
                    <span>
                      Your desk stays local — nothing here is uploaded.
                    </span>
                  </p>
                )}
              </>
            )}
            {identity &&
              service.current &&
              configuration?.state === "configured" && (
                <PrivacyControls
                  key={identity.firebase_uid}
                  identity={service.current}
                  uid={identity.firebase_uid}
                  apiOrigin={configuration.apiOrigin}
                  onAccountDeleted={setAccountDeleted}
                />
              )}
          </section>
        </div>
      </main>
      {confirmationDialog}
    </div>
  );
}
