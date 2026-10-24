"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { AccountSessionView } from "./use-account-session";
import { createOwnerTokenProvider } from "./owner-token";
import {
  createServiceAccessClient,
  ServiceAccessError,
  serviceAccessMessage,
} from "./service-access-client";
import type { components } from "../../packages/contracts/src/generated";

export type AccessSession = Pick<
  AccountSessionView,
  "configuration" | "identity" | "auth" | "uid"
>;
type View = {
  client: ReturnType<typeof createServiceAccessClient> | null;
  uid: string | null;
  policy: components["schemas"]["PolicyResponse"] | null;
  status: components["schemas"]["AccessResponse"] | null;
  error: string;
  loading: boolean;
};
export function useServiceAccess(session: AccessSession) {
  const { uid, identity, configuration } = session;
  const [view, setView] = useState<View>({
    client: null,
    uid: null,
    policy: null,
    status: null,
    error: "",
    loading: true,
  });
  const [revision, setRevision] = useState(0);
  const client = useMemo(() => {
    if (!uid || !identity || configuration?.state !== "configured") return null;
    return createServiceAccessClient(
      configuration.apiOrigin,
      createOwnerTokenProvider(
        identity,
        uid,
        () => new ServiceAccessError("unauthenticated"),
      ),
    );
  }, [uid, identity, configuration]);
  useEffect(() => {
    let active = true;
    if (!client || !uid) return;
    setView({
      client,
      uid,
      policy: null,
      status: null,
      error: "",
      loading: true,
    });
    void Promise.all([client.policy(), client.status()])
      .then(([policy, status]) => {
        if (active && identity?.getSnapshot().identity?.firebase_uid === uid)
          setView({ client, uid, policy, status, error: "", loading: false });
      })
      .catch((error) => {
        if (active)
          setView({
            client,
            uid,
            policy: null,
            status: null,
            error: serviceAccessMessage(error),
            loading: false,
          });
      });
    return () => {
      active = false;
    };
  }, [client, uid, identity, revision, session.auth?.identity?.email_verified]);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const current =
    view.uid === uid && view.client === client
      ? view
      : { uid, policy: null, status: null, error: "", loading: !!uid };
  const eligible = !!(
    current.status?.eligible &&
    current.policy?.ready &&
    current.status.policy_sha256 === current.policy.sha256 &&
    session.auth?.identity?.email_verified &&
    !session.auth.identity.is_anonymous
  );
  return { ...current, eligible, client, refresh };
}
