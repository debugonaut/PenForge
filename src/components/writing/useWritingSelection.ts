"use client";

import {
  useEffect,
  useRef,
  useState,
  type RefObject,
  type MouseEvent,
} from "react";
import {
  writingSelection,
  type WritingSelection,
  type WritingTextField,
  type WritingTextValues,
} from "@/lib/writing-text";

/** Native text selection stays separate from the document and its undo history. */
export function useWritingSelection(
  values: WritingTextValues,
  preview: RefObject<HTMLDivElement | null>,
  pageId: string | null,
  enabled = true,
) {
  const [selection, setSelection] = useState<WritingSelection | null>(null);
  const [scope, setScope] = useState<"page" | "selection">("page");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const controls = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const source = useRef<HTMLElement | null>(null);
  const latest = useRef(values);
  latest.current = values;
  const previous = useRef(values);

  useEffect(() => {
    if (
      selection &&
      previous.current[selection.field] !== values[selection.field]
    ) {
      setSelection(null);
      setScope("page");
      setMenu(null);
    }
    previous.current = values;
  }, [values, selection]);

  useEffect(() => {
    setSelection(null);
    setScope("page");
    setMenu(null);
  }, [pageId]);

  function capture(target?: EventTarget | null): WritingSelection | null {
    if (!enabled) return null;
    const element =
      target instanceof HTMLElement ? target : window.document.activeElement;
    let selected: WritingSelection | null = null;
    if (
      (element instanceof HTMLInputElement ||
        element instanceof HTMLTextAreaElement) &&
      element.dataset.writingField
    ) {
      selected = writingSelection(latest.current, {
        field: element.dataset.writingField as WritingTextField,
        start: element.selectionStart ?? 0,
        end: element.selectionEnd ?? 0,
      });
      if (selected) source.current = element;
    } else {
      const native = window.getSelection();
      if (native?.rangeCount && !native.isCollapsed && preview.current) {
        const range = native.getRangeAt(0);
        const endpoint = (node: Node, offset: number) => {
          const text = (
            node.nodeType === Node.ELEMENT_NODE
              ? (node as Element)
              : node.parentElement
          )?.closest("text[data-field][data-source-start]");
          if (!text || !preview.current?.contains(text)) return null;
          const prefix = window.document.createRange();
          prefix.selectNodeContents(text);
          try {
            prefix.setEnd(node, offset);
          } catch {
            return null;
          }
          return {
            field: text.getAttribute("data-field") as WritingTextField,
            offset:
              Number(text.getAttribute("data-source-start")) +
              prefix.toString().length,
          };
        };
        const start = endpoint(range.startContainer, range.startOffset);
        const end = endpoint(range.endContainer, range.endOffset);
        if (start && end && start.field === end.field)
          selected = writingSelection(latest.current, {
            field: start.field,
            start: start.offset,
            end: end.offset,
          });
        if (selected) source.current = preview.current;
      }
    }
    if (selected)
      setSelection((old) =>
        old?.field === selected.field &&
        old.start === selected.start &&
        old.end === selected.end
          ? old
          : selected,
      );
    return selected;
  }

  useEffect(() => {
    if (!enabled) return;
    let dragging = false,
      timer: ReturnType<typeof setTimeout> | undefined;
    const start = () => {
      dragging = true;
    };
    const finish = (event: Event) => {
      dragging = false;
      capture(event.target);
    };
    const changed = () => {
      clearTimeout(timer);
      if (!dragging) timer = setTimeout(() => capture(), 60);
    };
    window.document.addEventListener("pointerdown", start);
    window.document.addEventListener("pointerup", finish);
    window.document.addEventListener("keyup", finish);
    window.document.addEventListener("selectionchange", changed);
    return () => {
      clearTimeout(timer);
      window.document.removeEventListener("pointerdown", start);
      window.document.removeEventListener("pointerup", finish);
      window.document.removeEventListener("keyup", finish);
      window.document.removeEventListener("selectionchange", changed);
    };
    // Capture reads current text from a ref; listeners must not interrupt a drag.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  useEffect(() => {
    if (!menu) return;
    menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenu(null);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" || event.key === "Tab") {
        setMenu(null);
        if (event.key === "Escape") source.current?.focus();
      }
    };
    const close = () => setMenu(null);
    window.document.addEventListener("pointerdown", dismiss);
    window.document.addEventListener("keydown", key);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.document.removeEventListener("pointerdown", dismiss);
      window.document.removeEventListener("keydown", key);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [menu]);

  function fineTune() {
    if (!selection) return;
    setScope("selection");
    setMenu(null);
    requestAnimationFrame(() =>
      controls.current?.focus({ preventScroll: false }),
    );
  }
  function contextMenu(event: MouseEvent<HTMLElement>) {
    if (!enabled) return;
    if (!capture(event.target) && !selection) return;
    event.preventDefault();
    const box = event.currentTarget.getBoundingClientRect();
    setMenu({
      x: Math.max(
        8,
        Math.min(event.clientX || box.left + 20, window.innerWidth - 230),
      ),
      y: Math.max(
        8,
        Math.min(event.clientY || box.top + 20, window.innerHeight - 66),
      ),
    });
  }
  function clear() {
    setSelection(null);
    setScope("page");
    setMenu(null);
  }
  return {
    selection,
    scope,
    setScope,
    controls,
    menu,
    menuRef,
    capture,
    contextMenu,
    fineTune,
    clear,
  };
}
