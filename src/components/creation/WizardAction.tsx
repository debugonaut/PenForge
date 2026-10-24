"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { createPortal } from "react-dom";
import styles from "./WizardAction.module.css";

function tooltipPosition(rect: DOMRect) {
  const below = rect.top < 64;
  return {
    left: Math.max(
      120,
      Math.min(window.innerWidth - 120, rect.left + rect.width / 2),
    ),
    top: below ? rect.bottom + 8 : rect.top - 8,
    below,
  };
}

/** A named tool with a tooltip outside the paper stage's clipped decoration. */
export function WizardAction({
  label,
  hint = label,
  icon,
  showLabel = false,
  className = "",
  ...props
}: Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "children" | "aria-label" | "title"
> & {
  label: string;
  hint?: string;
  icon: ReactNode;
  showLabel?: boolean;
}) {
  const tooltipId = useId();
  const anchor = useRef<HTMLSpanElement>(null);
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [position, setPosition] = useState<{
    left: number;
    top: number;
    below: boolean;
  } | null>(null);

  function clearTimeoutRef() {
    if (timeout.current !== null) clearTimeout(timeout.current);
    timeout.current = null;
  }
  function open() {
    clearTimeoutRef();
    const rect = anchor.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition(tooltipPosition(rect));
  }
  function closeSoon() {
    clearTimeoutRef();
    timeout.current = setTimeout(() => {
      if (!anchor.current?.contains(document.activeElement)) setPosition(null);
    }, 100);
  }
  useEffect(() => () => clearTimeoutRef(), []);
  useEffect(() => {
    if (!position) return;
    const close = () => setPosition(null);
    const onViewportChange = () => {
      const element = anchor.current;
      if (element?.contains(document.activeElement)) {
        // Focusing a tool may scroll it into view after its focus event.
        // Keep that keyboard hint attached instead of immediately hiding it.
        setPosition(tooltipPosition(element.getBoundingClientRect()));
      } else close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    document.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
      document.removeEventListener("keydown", escape);
    };
  }, [position]);

  return (
    <span
      ref={anchor}
      className={styles.anchor}
      onPointerEnter={(event) => {
        if (event.pointerType !== "touch") open();
      }}
      onPointerLeave={closeSoon}
      onFocus={open}
      onBlur={() => {
        clearTimeoutRef();
        setPosition(null);
      }}
    >
      <button
        {...props}
        type={props.type || "button"}
        className={`${styles.action} ${showLabel ? styles.withLabel : styles.iconOnly} ${className}`.trim()}
        aria-label={label}
        aria-describedby={position ? tooltipId : undefined}
      >
        {icon}
        {showLabel && <span>{label}</span>}
      </button>
      {position &&
        createPortal(
          <span
            id={tooltipId}
            role="tooltip"
            className={styles.tooltip}
            data-placement={position.below ? "below" : "above"}
            style={{ left: position.left, top: position.top }}
            onPointerEnter={clearTimeoutRef}
            onPointerLeave={closeSoon}
          >
            {hint}
          </span>,
          document.body,
        )}
    </span>
  );
}
