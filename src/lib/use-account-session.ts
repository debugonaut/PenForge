"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAccountIdentityService,
  getPenforgeAuthConfiguration,
  type AuthConfiguration,
  type AuthState,
  type IdentityService,
} from "./auth-service";

export type AccountSessionView = {
  configuration: AuthConfiguration | null;
  auth: AuthState | null;
  identity: IdentityService | null;
  uid: string | null;
  configured: boolean;
  settled: boolean;
  /** A configured build is restoring this browser's existing session. */
  restoring: boolean;
};

/** One lazy client session; identity changes clear the previous projection. */
export function useAccountSession(): AccountSessionView {
  const [view, setView] = useState<AccountSessionView>({
    configuration: null,
    auth: null,
    identity: null,
    uid: null,
    configured: false,
    settled: false,
    restoring: true,
  });
  const attempted = useRef(false);

  useEffect(() => {
    const configuration = getPenforgeAuthConfiguration(window.location.hostname);
    const identity = getAccountIdentityService(configuration);
    let active = true;
    const publish = () => {
      if (!active) return;
      const auth = identity.getSnapshot();
      const configured = configuration.state === "configured";
      const uid = auth.identity?.firebase_uid ?? null;
      setView({
        configuration,
        auth,
        identity,
        uid,
        configured,
        settled: true,
        // A configured build quietly restores this browser's session so a
        // refresh reopens the signed-in desk; a failed attempt settles into
        // signed-out and shows the sign-in choice instead of a stuck load.
        restoring:
          configured &&
          !uid &&
          (auth.phase === "idle" || auth.phase === "busy"),
      });
    };
    publish();
    const unsubscribe = identity.subscribe(publish);
    // Resume once per browser session, only from a pristine idle state; a
    // signed-out visitor is never auto-upgraded into a guest.
    if (
      configuration.state === "configured" &&
      !attempted.current &&
      identity.getSnapshot().phase === "idle"
    ) {
      attempted.current = true;
      void identity.connect();
    }
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  return view;
}

export function useConnectAccount(session: AccountSessionView) {
  return useCallback(async () => {
    if (!session.identity || session.configuration?.state !== "configured")
      return false;
    if (!session.identity.getSnapshot().identity) {
      try {
        await session.identity.connect();
      } catch {
        return false;
      }
    }
    return Boolean(session.identity.getSnapshot().identity);
  }, [session.configuration, session.identity]);
}
