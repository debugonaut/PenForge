"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { LoadingScene } from "./LoadingScene";

/** Keep instant navigations silent; only show the scene when a wait is real. */
const SHOW_DELAY = 140;
/** Once shown, hold it long enough to read rather than flicker. */
const MIN_VISIBLE = 420;
/** Never leave the veil up if a navigation is abandoned. */
const SAFETY = 6000;

const messages: { match: RegExp; message: string }[] = [
  { match: /^\/create/, message: "Opening your creative desk…" },
  { match: /^\/projects/, message: "Opening your workbench…" },
  { match: /^\/write/, message: "Pressing a fresh sheet…" },
  { match: /^\/account/, message: "Setting your desk in order…" },
];

function messageFor(pathname: string) {
  return (
    messages.find((entry) => entry.match.test(pathname))?.message ??
    "Turning the page…"
  );
}

/** The next internal URL for a plain left click, or null for anything else. */
function plainClickTarget(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return null;
  }
  const anchor = (event.target as Element | null)?.closest?.("a");
  if (!anchor || !(anchor instanceof HTMLAnchorElement)) return null;
  if (anchor.target && anchor.target !== "_self") return null;
  if (anchor.hasAttribute("download")) return null;
  const href = anchor.getAttribute("href");
  if (!href || href.startsWith("#")) return null;
  const url = new URL(anchor.href, window.location.href);
  if (url.origin !== window.location.origin) return null;
  if (
    url.pathname === window.location.pathname &&
    url.search === window.location.search
  ) {
    return null;
  }
  return url;
}

function RouteTransitionInner() {
  const pathname = usePathname();
  const search = useSearchParams();
  const [visible, setVisible] = useState(false);
  const [message, setMessage] = useState("Turning the page…");
  const showTimer = useRef<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const safetyTimer = useRef<number | null>(null);
  const shownAt = useRef(0);
  const startKey = useRef("");
  const key = `${pathname}?${search.toString()}`;

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const url = plainClickTarget(event);
      if (!url) return;
      startKey.current = window.location.pathname + window.location.search;
      setMessage(messageFor(url.pathname));
      if (showTimer.current) window.clearTimeout(showTimer.current);
      showTimer.current = window.setTimeout(() => {
        shownAt.current = performance.now();
        setVisible(true);
        if (safetyTimer.current) window.clearTimeout(safetyTimer.current);
        safetyTimer.current = window.setTimeout(
          () => setVisible(false),
          SAFETY,
        );
      }, SHOW_DELAY);
    };
    // Capture phase: Next's Link handler calls preventDefault while it navigates,
    // so a bubbling listener would see every internal link as already handled.
    document.addEventListener("click", onClick, true);
    return () => {
      document.removeEventListener("click", onClick, true);
      [showTimer, hideTimer, safetyTimer].forEach((timer) => {
        if (timer.current) window.clearTimeout(timer.current);
      });
    };
  }, []);

  // The destination rendered: fade the veil out once it has been readable.
  useEffect(() => {
    if (!visible || key === startKey.current) return;
    const remaining = Math.max(0, MIN_VISIBLE - (performance.now() - shownAt.current));
    hideTimer.current = window.setTimeout(() => setVisible(false), remaining);
    return () => {
      if (hideTimer.current) window.clearTimeout(hideTimer.current);
    };
  }, [key, visible]);

  return (
    <div
      className="route-transition"
      data-visible={visible}
      aria-hidden={visible ? undefined : true}
    >
      <LoadingScene inline message={message} />
    </div>
  );
}

/**
 * App-wide navigation veil. Any internal link that leaves the current page gets
 * the Penforge loading scene if the destination takes more than a moment.
 */
export function RouteTransition() {
  return (
    <Suspense fallback={null}>
      <RouteTransitionInner />
    </Suspense>
  );
}
