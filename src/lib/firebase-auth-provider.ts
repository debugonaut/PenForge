/**
 * Loaded in configured builds once the account session is needed. The session
 * itself uses local persistence so a signed-in desk survives closing and
 * reopening the browser; signing out always clears it.
 */
import { getApps, initializeApp } from "firebase/app";
import {
  browserLocalPersistence,
  browserPopupRedirectResolver,
  connectAuthEmulator,
  createUserWithEmailAndPassword,
  EmailAuthProvider,
  GithubAuthProvider,
  GoogleAuthProvider,
  initializeAuth,
  linkWithCredential,
  linkWithPopup,
  signInAnonymously,
  signInWithCredential,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  sendEmailVerification,
  reload,
  getAuth,
} from "firebase/auth";
import type { AuthConfiguration, AuthProvider } from "./auth-service";

export async function createFirebaseAuthProvider(
  config: Extract<AuthConfiguration, { state: "configured" }>,
): Promise<AuthProvider> {
  // A named, isolated Auth app never initializes Firestore, Storage or Analytics.
  const name = `penforge-identity-${config.firebase.projectId}`;
  const existing = getApps().find((app) => app.name === name);
  const app = existing || initializeApp(config.firebase, name);
  const auth = existing
    ? getAuth(app)
    : initializeAuth(app, {
        persistence: browserLocalPersistence,
        popupRedirectResolver: browserPopupRedirectResolver,
      });
  /**
   * A guest keeps its UID when the provider is linked in place. A provider that
   * already belongs to another account resolves to that account, whose guest
   * proof is then offered for an explicit claim in the account screen.
   */
  const popup = async (
    provider: Parameters<typeof signInWithPopup>[1],
    credentialFromError: typeof GoogleAuthProvider.credentialFromError,
  ) => {
    const current = auth.currentUser;
    if (!current?.isAnonymous) {
      await signInWithPopup(auth, provider);
      return;
    }
    try {
      await linkWithPopup(current, provider);
    } catch (error) {
      // Firebase provides a credential only after provider authentication succeeded.
      const code =
        error && typeof error === "object" && "code" in error ? error.code : "";
      if (code !== "auth/credential-already-in-use") throw error;
      const credential = credentialFromError(
        error as Parameters<typeof credentialFromError>[0],
      );
      if (!credential) throw error;
      await signInWithCredential(auth, credential);
    }
  };
  if (config.emulatorUrl && !existing) {
    // Defense in depth, independent of the configuration parser.
    if (process.env.NODE_ENV === "production")
      throw new Error("emulator_forbidden_in_production");
    connectAuthEmulator(auth, config.emulatorUrl);
  }
  return {
    currentUser: () => auth.currentUser,
    ready: () => auth.authStateReady(),
    guest: async () => {
      await signInAnonymously(auth);
    },
    register: async (email, password) => {
      const current = auth.currentUser;
      if (current?.isAnonymous)
        await linkWithCredential(
          current,
          EmailAuthProvider.credential(email, password),
        );
      else await createUserWithEmailAndPassword(auth, email, password);
    },
    email: async (email, password) => {
      await signInWithEmailAndPassword(auth, email, password);
    },
    google: async () => {
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      await popup(provider, GoogleAuthProvider.credentialFromError);
    },
    github: async () => {
      await popup(
        new GithubAuthProvider(),
        GithubAuthProvider.credentialFromError,
      );
    },
    sendVerification: async () => {
      if (!auth.currentUser || auth.currentUser.isAnonymous)
        throw new Error("account_required");
      await sendEmailVerification(auth.currentUser);
    },
    refreshVerification: async () => {
      const current = auth.currentUser;
      if (!current) throw new Error("unauthenticated");
      await reload(current);
      if (auth.currentUser?.uid !== current.uid)
        throw new Error("identity_mismatch");
      await current.getIdToken(true);
    },
    signOut: () => signOut(auth),
  };
}
