import type { IdentityService } from "./auth-service";

/** Bind token acquisition to the owner captured when the operation was created. */
export function createOwnerTokenProvider(
  identity: Pick<IdentityService, "getSnapshot" | "getAccessToken">,
  ownerUid: string | null | undefined,
  mismatch: () => Error,
): () => Promise<string> {
  return async () => {
    if (!ownerUid || identity.getSnapshot().identity?.firebase_uid !== ownerUid)
      throw mismatch();
    const token = await identity.getAccessToken();
    if (identity.getSnapshot().identity?.firebase_uid !== ownerUid)
      throw mismatch();
    return token;
  };
}
