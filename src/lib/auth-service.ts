/** Identity is opt-in and independent of the local project/document services. */
import type { components } from "../../packages/contracts/src/generated";

export type Identity = components["schemas"]["MeResponse"];
export type ClaimResult = components["schemas"]["ClaimResponse"];

export type AuthConfiguration =
  | { state: "disabled"; reason: "not-selected" | "invalid-configuration" }
  | {
      state: "configured";
      apiOrigin: string;
      firebase: {
        apiKey: string;
        projectId: string;
        authDomain: string;
        appId: string;
      };
      emulatorUrl: string | null;
    };

function loopback(hostname: string): boolean {
  return ["localhost", "127.0.0.1", "[::1]"].includes(hostname);
}
function origin(value: string | undefined): URL {
  const parsed = new URL(value || "");
  if (
    !["https:", "http:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname !== "/"
  )
    throw new Error("invalid_origin");
  return parsed;
}

/** No Firebase SDK loads, sessions resume or network requests happen while disabled. */
export function resolveAuthConfiguration(
  env: Record<string, string | undefined>,
  browserHostname?: string,
): AuthConfiguration {
  if (
    !env.NEXT_PUBLIC_PENFORGE_AUTH_BACKEND ||
    env.NEXT_PUBLIC_PENFORGE_AUTH_BACKEND === "disabled"
  )
    return { state: "disabled", reason: "not-selected" };
  try {
    if (env.NEXT_PUBLIC_PENFORGE_AUTH_BACKEND !== "firebase")
      throw new Error("unsupported_backend");
    const appEnv = env.NEXT_PUBLIC_PENFORGE_ENV;
    if (!["local", "preview", "production"].includes(appEnv || ""))
      throw new Error("missing_environment");
    const api = origin(env.NEXT_PUBLIC_PENFORGE_API_ORIGIN);
    if (
      api.protocol !== "https:" &&
      !(appEnv === "local" && loopback(api.hostname))
    )
      throw new Error("insecure_origin");
    const firebase = {
      apiKey: env.NEXT_PUBLIC_FIREBASE_API_KEY || "",
      projectId: env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || "",
      authDomain: env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || "",
      appId: env.NEXT_PUBLIC_FIREBASE_APP_ID || "",
    };
    if (
      !Object.values(firebase).every(Boolean) ||
      !/^[a-z][a-z0-9-]{4,62}$/.test(firebase.projectId)
    )
      throw new Error("missing_firebase_configuration");
    if (!/^[a-zA-Z0-9.-]+(?::[0-9]+)?$/.test(firebase.authDomain))
      throw new Error("invalid_auth_domain");
    let emulatorUrl: string | null = null;
    if (env.NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_URL) {
      const emulator = origin(env.NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_URL);
      if (
        env.NODE_ENV === "production" ||
        appEnv !== "local" ||
        !loopback(emulator.hostname) ||
        emulator.protocol !== "http:" ||
        !loopback(api.hostname) ||
        !browserHostname ||
        !loopback(browserHostname) ||
        !firebase.projectId.startsWith("demo-")
      )
        throw new Error("unsafe_emulator_configuration");
      emulatorUrl = emulator.origin;
    } else if (firebase.projectId.startsWith("demo-"))
      throw new Error("demo_project_requires_emulator");
    return {
      state: "configured",
      apiOrigin: api.origin,
      firebase,
      emulatorUrl,
    };
  } catch {
    return { state: "disabled", reason: "invalid-configuration" };
  }
}

/** Share the same strict public auth configuration between Account and Cloud Projects. */
export function getPenforgeAuthConfiguration(
  browserHostname?: string,
): AuthConfiguration {
  return resolveAuthConfiguration(
    {
      NODE_ENV: process.env.NODE_ENV,
      NEXT_PUBLIC_PENFORGE_AUTH_BACKEND:
        process.env.NEXT_PUBLIC_PENFORGE_AUTH_BACKEND,
      NEXT_PUBLIC_PENFORGE_ENV: process.env.NEXT_PUBLIC_PENFORGE_ENV,
      NEXT_PUBLIC_PENFORGE_API_ORIGIN:
        process.env.NEXT_PUBLIC_PENFORGE_API_ORIGIN,
      NEXT_PUBLIC_FIREBASE_API_KEY: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
      NEXT_PUBLIC_FIREBASE_PROJECT_ID:
        process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
      NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN:
        process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
      NEXT_PUBLIC_FIREBASE_APP_ID: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
      NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_URL:
        process.env.NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_URL,
    },
    browserHostname,
  );
}

export interface AuthUser {
  uid: string;
  isAnonymous: boolean;
  getIdToken(forceRefresh?: boolean): Promise<string>;
}
export interface AuthProvider {
  currentUser(): AuthUser | null;
  ready(): Promise<void>;
  guest(): Promise<void>;
  register(email: string, password: string): Promise<void>;
  email(email: string, password: string): Promise<void>;
  google(): Promise<void>;
  github(): Promise<void>;
  sendVerification(): Promise<void>;
  refreshVerification(): Promise<void>;
  signOut(): Promise<void>;
}
export interface IdentityApi {
  me(): Promise<Identity>;
  claim(guestIdToken: string): Promise<ClaimResult>;
}
export type AuthState = {
  phase: "unavailable" | "idle" | "busy" | "signed-out" | "signed-in";
  identity: Identity | null;
  pendingClaim: boolean;
  message: string;
  error: string;
};

type ProviderLoader = (
  configuration: Extract<AuthConfiguration, { state: "configured" }>,
) => Promise<AuthProvider>;
type ApiLoader = (
  apiOrigin: string,
  token: () => Promise<string>,
) => Promise<IdentityApi>;

/** Only allowlisted error codes become UI text; SDK/backend messages may contain secrets. */
export function safeAuthError(error: unknown): string {
  const code =
    error && typeof error === "object" && "code" in error
      ? String(error.code)
      : "";
  const messages: Record<string, string> = {
    "auth/invalid-email": "Enter a valid email address.",
    "auth/weak-password":
      "Choose a stronger password with at least six characters.",
    "auth/invalid-credential":
      "Sign-in could not be completed. Check your credentials and try again.",
    "auth/wrong-password":
      "Sign-in could not be completed. Check your credentials and try again.",
    "auth/user-not-found":
      "Sign-in could not be completed. Check your credentials and try again.",
    "auth/email-already-in-use":
      "This email already has an account. Choose Sign in to that account, then review the guest claim.",
    "auth/credential-already-in-use":
      "This credential already has an account. Choose Sign in to that account, then review the guest claim.",
    "auth/account-exists-with-different-credential":
      "Use your existing account’s sign-in method, then review the guest claim.",
    "auth/popup-blocked":
      "Your browser blocked the sign-in popup. Allow popups and try again.",
    "auth/popup-closed-by-user":
      "The sign-in popup was closed. You can try again.",
    "auth/cancelled-popup-request":
      "The sign-in popup was closed. You can try again.",
    "auth/operation-not-allowed":
      "This sign-in method has not been enabled for the configured Firebase project.",
    "auth/unauthorized-domain":
      "This browser domain has not been authorized for Firebase sign-in.",
    "auth/network-request-failed":
      "The authentication service could not be reached. Try again when connected.",
    "auth/too-many-requests":
      "There have been too many attempts. Wait before trying again.",
    "auth/user-disabled":
      "This Firebase account is disabled. Contact the project administrator.",
    account_suspended: "This account is suspended.",
    account_deleting: "This account is being deleted and cannot be used.",
    account_merged:
      "This guest identity was claimed. Sign in to the destination account.",
    already_claimed:
      "This guest identity has already been claimed by another account.",
    stale_guest_token:
      "The guest sign-in proof is too old to claim securely. Your work remains stored; keep this tab open and contact the administrator.",
    guest_auth_too_old:
      "The guest sign-in proof is too old to claim securely. Your work remains stored; keep this tab open and contact the administrator.",
    invalid_token: "The sign-in session is no longer valid. Sign in again.",
    unauthenticated: "Sign in before checking your account.",
    rate_limited: "Too many account requests. Wait before trying again.",
    dependency_unavailable:
      "The account API is unavailable. Your local desk is still ready to use.",
    state_conflict:
      "The account state changed. Refresh identity before trying again.",
    claim_confirmation_required:
      "Confirm the guest ownership transfer before continuing.",
    no_pending_claim:
      "There is no guest ownership transfer waiting in this tab.",
    identity_mismatch:
      "The account changed during this request. Refresh identity before continuing.",
  };
  return (
    messages[code] ||
    "The account action could not be completed. Try again; your local drafts are unchanged."
  );
}

/** Memory-only guest proof; never stored, emitted in state, logged, or sent before claim. */
export class IdentityService {
  private provider: AuthProvider | null = null;
  private api: IdentityApi | null = null;
  private guestProof: { uid: string; token: string } | null = null;
  private listeners = new Set<() => void>();
  private state: AuthState;

  constructor(
    readonly configuration: AuthConfiguration,
    private readonly loadProvider: ProviderLoader = async (config) =>
      (await import("./firebase-auth-provider")).createFirebaseAuthProvider(
        config,
      ),
    private readonly loadApi: ApiLoader = async (apiOrigin, token) =>
      (
        await import("../../packages/contracts/src/client")
      ).createIdentityClient(apiOrigin, token),
  ) {
    this.state = {
      phase: configuration.state === "disabled" ? "unavailable" : "idle",
      identity: null,
      pendingClaim: false,
      message: "",
      error: "",
    };
  }
  getSnapshot = (): AuthState => this.state;
  async getAccessToken(): Promise<string> {
    await this.initialize();
    const user = this.provider?.currentUser();
    if (
      !user ||
      this.state.phase !== "signed-in" ||
      this.state.identity?.firebase_uid !== user.uid
    )
      throw { code: "unauthenticated" };
    return user.getIdToken();
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private update(next: Partial<AuthState>): void {
    this.state = { ...this.state, ...next };
    for (const listener of this.listeners) listener();
  }
  private async initialize(): Promise<void> {
    if (this.configuration.state !== "configured")
      throw { code: "dependency_unavailable" };
    if (this.provider && this.api) return;
    this.provider =
      this.provider || (await this.loadProvider(this.configuration));
    await this.provider.ready();
    this.api = await this.loadApi(this.configuration.apiOrigin, async () => {
      const user = this.provider?.currentUser();
      if (!user) throw { code: "unauthenticated" };
      return user.getIdToken();
    });
  }
  private requireAccountUser() {
    const user = this.provider?.currentUser();
    if (
      !user ||
      user.isAnonymous ||
      this.state.identity?.firebase_uid !== user.uid
    )
      throw { code: "unauthenticated" };
  }

  sendVerification = () =>
    this.action(async () => {
      this.requireAccountUser();
      await this.provider!.sendVerification();
      await this.refreshIdentity();
      this.update({ message: "Verification email sent. Check your inbox." });
    });

  refreshVerification = () =>
    this.action(async () => {
      this.requireAccountUser();
      await this.provider!.refreshVerification();
      await this.refreshIdentity();
    });

  private async preserveGuest(): Promise<void> {
    const current = this.provider?.currentUser();
    if (current?.isAnonymous)
      this.guestProof = {
        uid: current.uid,
        token: await current.getIdToken(true),
      };
  }
  private async refreshIdentity(): Promise<void> {
    const user = this.provider?.currentUser();
    if (!user) {
      this.update({ phase: "signed-out", identity: null, pendingClaim: false });
      return;
    }
    const requestedUid = user.uid;
    const identity = await this.api!.me();
    if (
      this.provider?.currentUser()?.uid !== requestedUid ||
      identity.firebase_uid !== requestedUid
    )
      throw { code: "identity_mismatch" };
    // A same-UID Firebase link is an in-place upgrade; no transfer request is needed.
    if (this.guestProof?.uid === requestedUid && !identity.is_anonymous)
      this.guestProof = null;
    this.update({
      phase: "signed-in",
      identity,
      pendingClaim: Boolean(
        this.guestProof &&
        this.guestProof.uid !== requestedUid &&
        !identity.is_anonymous,
      ),
    });
  }
  private async action(operation: () => Promise<void>): Promise<void> {
    if (this.state.phase === "busy") return;
    this.update({ phase: "busy", error: "", message: "" });
    try {
      await this.initialize();
      await operation();
    } catch (error) {
      const identity =
        this.state.identity?.firebase_uid === this.provider?.currentUser()?.uid
          ? this.state.identity
          : null;
      this.update({
        identity,
        pendingClaim: Boolean(
          identity &&
          this.guestProof &&
          this.guestProof.uid !== identity.firebase_uid &&
          !identity.is_anonymous,
        ),
        phase:
          this.configuration.state === "disabled"
            ? "unavailable"
            : identity
              ? "signed-in"
              : "signed-out",
        error: safeAuthError(error),
      });
    }
  }
  connect = (): Promise<void> =>
    this.action(async () => {
      await this.refreshIdentity();
    });
  startGuest = (): Promise<void> =>
    this.action(async () => {
      if (!this.provider!.currentUser()) await this.provider!.guest();
      await this.refreshIdentity();
    });
  register = (email: string, password: string): Promise<void> =>
    this.action(async () => {
      await this.preserveGuest();
      await this.provider!.register(email.trim(), password);
      await this.provider!.currentUser()?.getIdToken(true);
      await this.refreshIdentity();
      this.update({
        message:
          "Account created. Your server identity and its history are preserved.",
      });
    });
  signInEmail = (email: string, password: string): Promise<void> =>
    this.action(async () => {
      await this.preserveGuest();
      await this.provider!.email(email.trim(), password);
      await this.refreshIdentity();
    });
  signInGoogle = (): Promise<void> =>
    this.action(async () => {
      await this.preserveGuest();
      await this.provider!.google();
      await this.provider!.currentUser()?.getIdToken(true);
      await this.refreshIdentity();
    });
  signInGithub = (): Promise<void> =>
    this.action(async () => {
      await this.preserveGuest();
      await this.provider!.github();
      await this.provider!.currentUser()?.getIdToken(true);
      await this.refreshIdentity();
    });
  claim = (confirmed: boolean): Promise<void> =>
    this.action(async () => {
      if (!confirmed) throw { code: "claim_confirmation_required" };
      if (!this.guestProof || !this.state.pendingClaim)
        throw { code: "no_pending_claim" };
      const destination = this.provider!.currentUser();
      if (
        !destination ||
        destination.isAnonymous ||
        destination.uid !== this.state.identity?.firebase_uid
      )
        throw { code: "identity_mismatch" };
      const result = await this.api!.claim(this.guestProof.token);
      this.guestProof = null;
      this.update({ pendingClaim: false });
      await this.refreshIdentity();
      this.update({
        message: result.over_limit
          ? "Guest server work is now owned by this account. It exceeds a configured quota; existing work is preserved."
          : "Guest server work is now owned by this account. Creation history is preserved.",
      });
    });
  signOut = (): Promise<void> =>
    this.action(async () => {
      await this.provider!.signOut();
      this.guestProof = null;
      this.update({
        phase: "signed-out",
        identity: null,
        pendingClaim: false,
        message: "Signed out. Your local drafts stay in this browser.",
      });
    });
}

let accountSession: IdentityService | null = null;
/** Keep pending proof during same-tab navigation, never across reloads or tab closure. */
export function getAccountIdentityService(
  configuration: AuthConfiguration,
): IdentityService {
  if (
    !accountSession ||
    JSON.stringify(accountSession.configuration) !==
      JSON.stringify(configuration)
  )
    accountSession = new IdentityService(configuration);
  return accountSession;
}
