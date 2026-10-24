import Link from "next/link";
import type { ReactNode } from "react";
import { BrandMark } from "@/components/brand/Characters";
import { SiteFooter } from "@/components/shared/SiteFooter";

export type LegalSection = {
  heading: string;
  body: ReactNode;
};

export function LegalPage({
  eyebrow,
  title,
  lede,
  sections,
  updated,
}: {
  eyebrow: string;
  title: string;
  lede: string;
  sections: LegalSection[];
  updated: string;
}) {
  return (
    <div className="legal-page">
      <header className="legal-header">
        <Link href="/" className="legal-home" aria-label="Penforge home">
          <BrandMark />
        </Link>
        <Link href="/" className="legal-back">
          ← Back to the story
        </Link>
      </header>
      <main id="main-content" className="legal-main">
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p className="legal-lede">{lede}</p>
        <div className="legal-sections">
          {sections.map((section) => (
            <section key={section.heading}>
              <h2>{section.heading}</h2>
              <div className="legal-section-body">
                {typeof section.body === "string" ? (
                  <p>{section.body}</p>
                ) : (
                  section.body
                )}
              </div>
            </section>
          ))}
        </div>
        <p className="legal-updated">{updated}</p>
      </main>
      <SiteFooter />
    </div>
  );
}
