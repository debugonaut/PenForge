"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

type SectionBounds = { top: number; bottom: number; target: number };
type Navigation = { goTo: (index: number) => void; next: () => void };

const gestureGap = 180;
const wheelThreshold = 48;
const boundaryTolerance = 4;

function isInput(target: Element) {
  return Boolean(
    target.closest(
      'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="slider"], [role="spinbutton"], [role="combobox"], [role="listbox"], [data-native-scroll]',
    ),
  );
}

/** Leave scrollable specimens, text areas, and other nested panels in control. */
function nestedScrollerCanMove(
  target: Element,
  root: HTMLElement,
  delta: number,
) {
  for (
    let element: Element | null = target;
    element && element !== root;
    element = element.parentElement
  ) {
    if (!(element instanceof HTMLElement)) continue;
    const overflow = window.getComputedStyle(element).overflowY;
    if (
      !/(auto|scroll)/.test(overflow) ||
      element.scrollHeight <= element.clientHeight + 1
    )
      continue;
    if (
      delta < 0
        ? element.scrollTop > 1
        : element.scrollTop + element.clientHeight < element.scrollHeight - 1
    )
      return true;
  }
  return false;
}

/**
 * Wheel gestures move between scenes; touch, keyboard, and scrollbar navigation
 * remain native. Pair with viewport-height sections and CSS scroll snapping.
 * The OS's gentle mode changes only the transition style, never navigation.
 */
export function useSectionNavigation(
  rootRef: RefObject<HTMLElement | null>,
  gentle: boolean,
) {
  const [activeSection, setActiveSection] = useState(0);
  const navigation = useRef<Navigation | null>(null);

  useEffect(() => {
    const mountedRoot = rootRef.current;
    if (!mountedRoot) return;
    const root: HTMLElement = mountedRoot;
    const sections = Array.from(
      root.querySelectorAll<HTMLElement>("[data-landing-section]"),
    );
    if (!sections.length) return;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let bounds: SectionBounds[] = [];
    let frame = 0;
    let settledTimer = 0;
    let gestureTimer = 0;
    let accumulated = 0;
    let lastWheel = 0;
    let lastDirection = 0;
    let claimedGesture = false;
    let scrollSettled = true;
    let disposed = false;

    function currentIndex() {
      const top = window.scrollY;
      const bottom = top + window.innerHeight;
      let largestVisible = -1;
      let current = 0;
      bounds.forEach((section, index) => {
        const visible = Math.max(
          0,
          Math.min(bottom, section.bottom) - Math.max(top, section.top),
        );
        if (visible > largestVisible) {
          largestVisible = visible;
          current = index;
        }
      });
      return current;
    }

    function updateActive() {
      frame = 0;
      if (!disposed) setActiveSection(currentIndex());
    }

    function measure() {
      const scrollTop = window.scrollY;
      const scrollPadding =
        parseFloat(
          window.getComputedStyle(document.documentElement).scrollPaddingTop,
        ) || 0;
      const maxScroll = Math.max(
        0,
        document.documentElement.scrollHeight - window.innerHeight,
      );
      bounds = sections.map((section) => {
        const rect = section.getBoundingClientRect();
        const margin =
          parseFloat(window.getComputedStyle(section).scrollMarginTop) || 0;
        const top = rect.top + scrollTop;
        return {
          top,
          bottom: top + rect.height,
          target: Math.min(
            maxScroll,
            Math.max(0, top - margin - scrollPadding),
          ),
        };
      });
      if (!frame) frame = window.requestAnimationFrame(updateActive);
    }

    // A smooth scroll and the trackpad's momentum must both end before another
    // gesture can select a scene. The delay tracks quiet input, not film length.
    function releaseGesture() {
      window.clearTimeout(gestureTimer);
      const quietFor = performance.now() - lastWheel;
      if (!scrollSettled || quietFor < gestureGap) {
        if (scrollSettled)
          gestureTimer = window.setTimeout(
            releaseGesture,
            gestureGap - quietFor,
          );
        return;
      }
      claimedGesture = false;
      accumulated = 0;
    }

    function onScrollEnd() {
      window.clearTimeout(settledTimer);
      scrollSettled = true;
      releaseGesture();
      if (!frame) frame = window.requestAnimationFrame(updateActive);
    }

    function onScroll() {
      scrollSettled = false;
      window.clearTimeout(settledTimer);
      settledTimer = window.setTimeout(onScrollEnd, 140);
      if (!frame) frame = window.requestAnimationFrame(updateActive);
    }

    function goTo(index: number) {
      measure();
      const target =
        bounds[Math.min(bounds.length - 1, Math.max(0, Math.round(index)))];
      if (!target) return;
      scrollSettled = false;
      window.clearTimeout(settledTimer);
      // Also settle when the destination equals the present position and no
      // scroll event will fire. Instant avoids inherited CSS smooth scrolling.
      settledTimer = window.setTimeout(onScrollEnd, 140);
      window.scrollTo({
        top: target.target,
        behavior: !gentle && !reducedMotion.matches ? "smooth" : "instant",
      });
    }

    function onWheel(event: WheelEvent) {
      if (
        event.defaultPrevented ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        !event.cancelable
      )
        return;
      const target = event.target instanceof Element ? event.target : null;
      if (!target || !root.contains(target) || isInput(target)) return;
      if (Math.abs(event.deltaX) > Math.abs(event.deltaY) || !event.deltaY)
        return;
      const delta =
        event.deltaY *
        (event.deltaMode === WheelEvent.DOM_DELTA_LINE
          ? 16
          : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
            ? window.innerHeight
            : 1);
      if (nestedScrollerCanMove(target, root, delta)) return;

      const now = performance.now();
      const direction = Math.sign(delta);
      if (claimedGesture) {
        event.preventDefault();
        lastWheel = now;
        releaseGesture();
        return;
      }

      const index = currentIndex();
      const section = bounds[index];
      const oversized =
        section.bottom - section.top > window.innerHeight + boundaryTolerance;
      // A small screen can make a scene taller than the viewport. Its content
      // stays readable with ordinary scrolling before moving to the next scene.
      if (
        oversized &&
        (direction > 0
          ? section.bottom >
            window.scrollY + window.innerHeight + boundaryTolerance
          : section.top < window.scrollY - boundaryTolerance)
      ) {
        accumulated = 0;
        lastWheel = now;
        lastDirection = direction;
        return;
      }

      const next = index + direction;
      if (next < 0 || next >= bounds.length) return;
      event.preventDefault();
      if (now - lastWheel > gestureGap || lastDirection !== direction)
        accumulated = 0;
      lastWheel = now;
      lastDirection = direction;
      accumulated += delta;
      if (Math.abs(accumulated) < wheelThreshold) return;
      claimedGesture = true;
      accumulated = 0;
      goTo(next);
      releaseGesture();
    }

    navigation.current = { goTo, next: () => goTo(currentIndex() + 1) };
    measure();
    const resizeObserver = new ResizeObserver(measure);
    resizeObserver.observe(root);
    sections.forEach((section) => resizeObserver.observe(section));
    document.fonts.ready.then(() => {
      if (!disposed) measure();
    });
    window.addEventListener("resize", measure);
    window.visualViewport?.addEventListener("resize", measure);
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("scrollend", onScrollEnd);
    window.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      disposed = true;
      navigation.current = null;
      resizeObserver.disconnect();
      window.cancelAnimationFrame(frame);
      window.clearTimeout(settledTimer);
      window.clearTimeout(gestureTimer);
      window.removeEventListener("resize", measure);
      window.visualViewport?.removeEventListener("resize", measure);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("scrollend", onScrollEnd);
      window.removeEventListener("wheel", onWheel);
    };
  }, [rootRef, gentle]);

  const goToSection = useCallback(
    (index: number) => navigation.current?.goTo(index),
    [],
  );
  const nextSection = useCallback(() => navigation.current?.next(), []);
  return { activeSection, goToSection, nextSection };
}
