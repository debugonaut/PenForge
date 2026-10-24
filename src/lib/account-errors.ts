import { CloudProjectError } from "./cloud-project-service";
import { WorkspaceError } from "./account-workspace";
import { DocumentError } from "./account-documents";

/** Normalize account failures without rendering server messages. */
export function accountErrorCode(error: unknown): string | null {
  if (error instanceof CloudProjectError) return error.code;
  if (error instanceof WorkspaceError)
    return error.code === "revision_conflict" ? "conflict" : error.code;
  if (error instanceof DocumentError)
    return error.code === "revision_conflict" ? "conflict" : error.code;
  if (error instanceof TypeError) return "network_unavailable";
  if (
    error &&
    typeof error === "object" &&
    "code" in error &&
    typeof error.code === "string"
  )
    return error.code;
  return null;
}

export function isAccountError(error: unknown): error is { code: string } {
  return accountErrorCode(error) !== null;
}
