"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

type Confirmation = {
  title: string;
  message: string;
  confirmLabel?: string;
  destructive?: boolean;
};

/** Explicit in-page confirmation also works in embedded browsers that suppress window.confirm. */
export function useConfirmation() {
  const [request, setRequest] = useState<Confirmation | null>(null);
  const resolver = useRef<((answer: boolean) => void) | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  const finish = useCallback((answer: boolean) => {
    resolver.current?.(answer);
    resolver.current = null;
    setRequest(null);
  }, []);
  const confirm = useCallback(
    (next: Confirmation) =>
      new Promise<boolean>((resolve) => {
        resolver.current?.(false);
        resolver.current = resolve;
        setRequest(next);
      }),
    [],
  );

  useEffect(() => {
    if (!request) return;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const current = dialog.current;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (current && !current.open) current.showModal();
    return () => {
      document.body.style.overflow = overflow;
      if (current?.open) current.close();
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [request]);
  useEffect(
    () => () => {
      resolver.current?.(false);
    },
    [],
  );

  const confirmationDialog = request ? (
    <dialog
      ref={dialog}
      className="confirmation-dialog"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-message`}
      onCancel={(event) => {
        event.preventDefault();
        finish(false);
      }}
    >
      <span className="scene-number">A MOMENT BEFORE YOU CONTINUE</span>
      <h2 id={`${id}-title`}>{request.title}</h2>
      <p id={`${id}-message`}>{request.message}</p>
      <div className="confirmation-actions">
        <button autoFocus className="text-button" onClick={() => finish(false)}>
          Cancel
        </button>
        <button
          className={`button ${request.destructive ? "button-destructive" : "button-dark"}`}
          onClick={() => finish(true)}
        >
          {request.confirmLabel || "Continue"} <span>→</span>
        </button>
      </div>
    </dialog>
  ) : null;
  return { confirm, confirmationDialog };
}
