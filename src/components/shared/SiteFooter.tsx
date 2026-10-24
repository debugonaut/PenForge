"use client";

import Link from "next/link";
import type { MouseEvent } from "react";
import {
  PenforgeWordmark,
  QuillCharacter,
} from "@/components/brand/Characters";

type SiteFooterProps = {
  /** Landing pages jump between chapters instead of moving the scrollbar. */
  onNavigateSection?: (index: number) => void;
};

const learning = [
  { label: "How it works", id: "how-it-works", index: 1 },
  { label: "Find your style", id: "make-it-yours", index: 3 },
  { label: "FAQs", id: "before-you-begin", index: 4 },
] as const;

export function SiteFooter({ onNavigateSection }: SiteFooterProps) {
  const backToTop = () => {
    if (onNavigateSection) {
      onNavigateSection(0);
      return;
    }
    window.scrollTo({
      top: 0,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
    document
      .querySelector<HTMLElement>("header a")
      ?.focus({ preventScroll: true });
  };

  const navigate = (event: MouseEvent<HTMLAnchorElement>, index: number) => {
    if (
      !onNavigateSection ||
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    onNavigateSection(index);
  };

  return (
    <footer className="pf-footer" aria-label="Penforge footer">
      <span className="pf-footer-character" aria-hidden="true">
        <span className="pf-footer-blob" />
        <QuillCharacter mood="happy" />
      </span>
      <div className="pf-footer-top">
        <div className="pf-footer-brand">
          <Link
            href="/"
            className="pf-footer-wordmark"
            aria-label="Penforge home"
          >
            <PenforgeWordmark />
          </Link>
          <p>Handwriting, kept as handwriting.</p>
        </div>
        <nav className="pf-footer-nav" aria-label="Footer">
          <div className="pf-footer-group">
            <h2>Create</h2>
            <Link href="/create">Start a font</Link>
            <Link href="/projects">Creative Workbench</Link>
            <Link href="/write">Writing Desk</Link>
          </div>
          <div className="pf-footer-group">
            <h2>Discover</h2>
            {learning.map((entry) => (
              <Link
                key={entry.id}
                href={`/#${entry.id}`}
                onClick={(event) => navigate(event, entry.index)}
              >
                {entry.label}
              </Link>
            ))}
          </div>
          <div className="pf-footer-group">
            <h2>PenForge</h2>
            <Link href="/account">Your account</Link>
            <a href="https://github.com/debugonaut/PenForge">
              Source on GitHub
            </a>
          </div>
          <div className="pf-footer-group">
            <h2>Legal</h2>
            <Link href="/privacy">Privacy</Link>
            <Link href="/terms">Terms</Link>
            <Link href="/report-abuse">Report abuse</Link>
          </div>
        </nav>
      </div>
      <div className="pf-footer-base">
        <button
          type="button"
          className="pf-footer-top-link"
          onClick={backToTop}
          aria-label="Back to top"
          title="Back to top"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M12 19V5m-6 6 6-6 6 6" />
          </svg>
        </button>
        <span className="pf-footer-copyright">© 2026 PenForge</span>
        <span className="pf-footer-version">Version 1</span>
      </div>
    </footer>
  );
}
