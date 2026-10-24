"use client";

import { useEffect, useSyncExternalStore } from "react";

/** OS preference selects gentler choreography; saved flags never disable the product. */
export function readSystemMotionPreference() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
function subscribe(callback: () => void) {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", callback);
  return () => {
    media.removeEventListener("change", callback);
  };
}
export function useMotionPreference() {
  const reduced = useSyncExternalStore(
    subscribe,
    readSystemMotionPreference,
    () => false,
  );
  return { reduced };
}
export function MotionPreference() {
  const { reduced } = useMotionPreference();
  useEffect(() => {
    document.documentElement.dataset.motion = "on";
    document.documentElement.dataset.motionVariant = reduced
      ? "gentle"
      : "full";
  }, [reduced]);
  return null;
}
