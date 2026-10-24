import type { components } from "../../packages/contracts/src/generated";
import { createAccountRequest } from "./account-request";

export class ServiceAccessError extends Error {
  constructor(
    readonly code: string,
    readonly status = 0,
  ) {
    super("Service access could not be confirmed.");
  }
}
export const serviceAccessMessage = (error: unknown): string => {
  const code = error instanceof ServiceAccessError ? error.code : "";
  return (
    (
      {
        account_required: "Create an account to continue.",
        email_verification_required: "Verify your email, then check again.",
        terms_required: "Review and accept the current terms to continue.",
        policy_changed:
          "The terms changed. Review the updated version before accepting.",
        service_not_ready:
          "The hosted service is not ready for public use yet.",
        unauthenticated: "Sign in again to continue.",
        network_unavailable: "Couldn’t reach Penforge. Try again.",
      } as Record<string, string>
    )[code] ?? "Couldn’t confirm access. Try again."
  );
};

export function createServiceAccessClient(
  baseUrl: string,
  token: () => Promise<string>,
  fetcher: typeof fetch = fetch,
) {
  const request = createAccountRequest(
    baseUrl,
    token,
    {
      error: ServiceAccessError,
      codes: new Set([
        "account_required",
        "email_verification_required",
        "terms_required",
        "policy_changed",
        "service_not_ready",
        "unauthenticated",
        "dependency_unavailable",
      ]),
    },
    fetcher,
  );
  return {
    async policy(): Promise<components["schemas"]["PolicyResponse"]> {
      return (await request("/api/v1/service-policy")).json();
    },
    async status(): Promise<components["schemas"]["AccessResponse"]> {
      return (await request("/api/v1/me/service-access")).json();
    },
    async accept(
      hash: string,
    ): Promise<components["schemas"]["AccessResponse"]> {
      return (
        await request("/api/v1/me/service-agreements", {
          method: "POST",
          body: {
            policy_sha256: hash,
            terms_accepted: true,
            handwriting_rights_confirmed: true,
          },
        })
      ).json();
    },
  };
}
