"use client";

import { createOwnerTokenProvider } from "./owner-token";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAccountIdentityService,
  getPenforgeAuthConfiguration,
  type IdentityService,
} from "./auth-service";
import { createCaptureClient, type CaptureClient } from "./capture-client";
import {
  createExtractionClient,
  type ExtractionClient,
  type TemplateChoice,
} from "./extraction-client";
import { createExportClient, type ExportClient } from "./export-client";
import { createJobClient, type JobClient } from "./job-client";
import { captureRecovery, type CaptureOperation } from "./capture-recovery";

export type AccountBackend = {
  identity: IdentityService | null;
  uid: string | null;
  capture: CaptureClient | null;
  extraction: ExtractionClient | null;
  exportClient: ExportClient | null;
  jobs: JobClient | null;
  templates: TemplateChoice[];
  operations: CaptureOperation[];
  ready: boolean;
  error: string;
  refreshOperations: () => Promise<void>;
  refreshTemplates: (scriptCode: "Latn" | "Deva") => Promise<void>;
};

const EMPTY = {
  identity: null,
  uid: null,
  capture: null,
  extraction: null,
  exportClient: null,
  jobs: null,
  templates: [],
  operations: [],
  ready: false,
  error: "",
} satisfies Omit<AccountBackend, "refreshOperations" | "refreshTemplates">;

/**
 * One connection per mounted studio. Every client token acquisition is fenced
 * against an identity change, and late results are discarded.
 */
export function useAccountBackend(
  projectId: string,
  scriptCode: "Latn" | "Deva" = "Latn",
): AccountBackend {
  const [state, setState] = useState<
    Omit<AccountBackend, "refreshOperations" | "refreshTemplates">
  >(EMPTY);
  const generation = useRef(0);

  const refreshOperations = useCallback(async () => {
    const version = generation.current;
    try {
      const operations = await captureRecovery.list(projectId);
      if (generation.current === version)
        setState((current) => ({ ...current, operations }));
    } catch {
      /* IndexedDB may be unavailable; server captures still work. */
    }
  }, [projectId]);

  useEffect(() => {
    const configuration = getPenforgeAuthConfiguration(window.location.hostname);
    const identity = getAccountIdentityService(configuration);
    let observedUid: string | null =
      identity.getSnapshot().identity?.firebase_uid ?? null;

    const open = async () => {
      const attempt = ++generation.current;
      const current = () => generation.current === attempt;
      if (configuration.state !== "configured") {
        setState((value) => ({
          ...value,
          ready: true,
          error: "Accounts are not configured in this build.",
        }));
        return;
      }
      try {
        if (!identity.getSnapshot().identity) await identity.connect();
      } catch {
        if (current())
          setState((value) => ({
            ...value,
            ready: true,
            error: "Sign in to open this project’s private tools.",
          }));
        return;
      }
      const uid = identity.getSnapshot().identity?.firebase_uid;
      if (!uid) {
        if (current()) setState((value) => ({ ...value, ready: true }));
        return;
      }
      const token = createOwnerTokenProvider(
        identity,
        uid,
        () => new Error("identity_mismatch"),
      );
      const capture = createCaptureClient(configuration.apiOrigin, token);
      const extraction = createExtractionClient(configuration.apiOrigin, token);
      if (!current()) return;
      observedUid = uid;
      setState((value) => ({
        ...value,
        identity,
        uid,
        capture,
        extraction,
        exportClient: createExportClient(configuration.apiOrigin, token),
        jobs: createJobClient(configuration.apiOrigin, token),
        ready: true,
        error: "",
      }));
      await refreshOperations();
      try {
        const choices = await extraction.templates(undefined, scriptCode);
        if (current()) setState((value) => ({ ...value, templates: choices }));
      } catch {
        /* Template inventory may be unavailable; drawing stays available. */
      }
    };
    void open();

    const unsubscribe = identity.subscribe(() => {
      const uid = identity.getSnapshot().identity?.firebase_uid ?? null;
      if (uid === observedUid) return;
      observedUid = uid;
      generation.current += 1;
      setState({
        ...EMPTY,
        ready: true,
        error: uid ? "" : "Account changed. Sign in again to open this project.",
      });
      if (uid) void open();
    });
    return () => {
      generation.current += 1;
      unsubscribe();
    };
  }, [projectId, refreshOperations, scriptCode]);

  const refreshTemplates = useCallback(
    async (nextScript: "Latn" | "Deva") => {
      const extraction = state.extraction;
      if (!extraction) return;
      try {
        const choices = await extraction.templates(undefined, nextScript);
        setState((current) =>
          current.extraction === extraction
            ? { ...current, templates: choices }
            : current,
        );
      } catch {
        /* Keep the existing inventory. */
      }
    },
    [state.extraction],
  );

  return { ...state, refreshOperations, refreshTemplates };
}
