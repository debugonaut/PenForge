"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  BrandMark,
  PenCharacter,
  QuillCharacter,
} from "@/components/brand/Characters";
import { SiteFooter } from "@/components/shared/SiteFooter";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { useMotionPreference } from "@/components/shared/MotionPreference";
import HandwritingFilm from "./HandwritingFilm";
import FinishDemo from "./FinishDemo";
import HowItWorksDemo from "./HowItWorksDemo";
import WritingDesk from "@/components/writing/WritingDesk";
import { useSectionNavigation } from "./useSectionNavigation";
import styles from "./LandingPage.module.css";

type IconName = "arrow" | "down";
function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    arrow: <path d="M5 19 19 5M5 5h14v14" />,
    down: <path d="M12 4v16M5 13l7 7 7-7" />,
  };
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}

const sectionNames = [
  "Introduction",
  "How it works",
  "Writing desk",
  "Choose the finish",
  "Before you begin",
];
const sectionIds = [
  "introduction",
  "how-it-works",
  "playground",
  "make-it-yours",
  "before-you-begin",
];
const questions = [
  {
    question: "What can I start with?",
    answer:
      "Draw in the studio, upload a scan, or use a clear photo of your writing. Dark ink, even light and a plain background help keep your letters clear.",
  },
  {
    question: "Can I adjust my letters?",
    answer:
      "Yes. Review the letters, make your changes and approve the shapes you want to keep before creating your font. You can return to a letter and revise it.",
  },
  {
    question: "Can I download a font yet?",
    answer:
      "Approved letters can be exported as TTF, OTF and WOFF2 through the connected account studio. Local demo previews use a sample typeface.",
  },
  {
    question: "Do I need an account?",
    answer:
      "Use an account to save projects online and create fonts through the connected service. You can explore the local demo and try the Writing Desk without signing in.",
  },
  {
    question: "Where does my handwriting go?",
    answer:
      "Account projects are saved to your account when the service is connected. Local demo drafts stay in this browser until you choose to import them. Clearing browser storage can remove local drafts.",
  },
  {
    question: "Can I use cursive handwriting?",
    answer:
      "You can capture cursive samples and review individual letters. Connected words still need careful review; automatic separation and natural joins are not ready yet.",
  },
  {
    question: "Can I try a font I already have?",
    answer:
      "Yes. Import a TTF, OTF or WOFF2 file in the Writing Desk to write with its actual letters. Imported fonts stay on your device and need to be imported again on another device.",
  },
  {
    question: "What can I make in the Writing Desk?",
    answer:
      "Create a letter, note or card, choose your paper and ink, then download a PNG or SVG. You can also print your page or use your browser’s Save as PDF option.",
  },
  {
    question: "Is my handwriting used for training?",
    answer:
      "Creating a font does not give permission to train on your handwriting. Training permission is a separate, optional choice.",
  },
];

export default function LandingPage() {
  const root = useRef<HTMLDivElement>(null);
  const how = useRef<HTMLElement>(null);
  const { reduced } = useMotionPreference();
  const { activeSection, goToSection } = useSectionNavigation(root, reduced);
  const [penHappy, setPenHappy] = useState(false);

  useEffect(() => {
    document.documentElement.classList.add("penforge-section-scroll");
    return () =>
      document.documentElement.classList.remove("penforge-section-scroll");
  }, []);

  // Footer links elsewhere in the app arrive as /#section; honour them once the
  // chapter navigation has measured the sections.
  useEffect(() => {
    const id = window.location.hash.replace("#", "");
    const index = sectionIds.indexOf(id);
    if (index < 1) return;
    const timer = window.setTimeout(() => goToSection(index), 220);
    return () => window.clearTimeout(timer);
  }, [goToSection]);

  useEffect(() => {
    if (activeSection !== 0 || !root.current) return;
    let cancelled = false;
    let cleanup = () => {};
    void import("gsap").then(({ default: gsap }) => {
      if (cancelled || !root.current) return;
      const context = gsap.context(() => {
        gsap.fromTo(
          [`.${styles.heroPen}`, `.${styles.heroQuill}`],
          { y: reduced ? 0 : 16, opacity: reduced ? 0.6 : 1 },
          {
            y: 0,
            opacity: 1,
            stagger: 0.07,
            duration: 0.32,
            ease: "power2.out",
          },
        );
        gsap
          .timeline({ delay: 8.7 })
          .to(`.${styles.heroQuill}`, {
            rotation: reduced ? 12.5 : 14,
            duration: 0.24,
            ease: "sine.inOut",
          })
          .to(`.${styles.heroQuill}`, {
            rotation: 12,
            duration: 0.3,
            ease: "power2.out",
          });
      }, root.current);
      cleanup = () => context.revert();
    });
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [activeSection, reduced]);

  useEffect(() => {
    if (activeSection !== 1 || !how.current) return;
    let cancelled = false;
    let cleanup = () => {};
    void import("gsap").then(({ default: gsap }) => {
      if (cancelled || !how.current) return;
      const context = gsap.context(() => {
        gsap
          .timeline()
          .fromTo(
            ".forge-source-paper",
            {
              y: reduced ? 0 : 12,
              rotation: reduced ? -3 : -5,
              scale: reduced ? 1 : 0.97,
              opacity: reduced ? 0.65 : 1,
            },
            {
              y: 0,
              rotation: -3,
              scale: reduced ? 1 : 1.035,
              opacity: 1,
              duration: 0.2,
              ease: "power2.out",
            },
          )
          .to(".forge-source-paper", {
            scale: 1,
            duration: 0.12,
            ease: "power2.inOut",
          })
          .fromTo(
            ".forge-source-paper",
            { boxShadow: "1px 2px 0 rgba(25,46,53,.08)" },
            {
              boxShadow: "3px 4px 0 rgba(25,46,53,.13)",
              duration: 0.25,
              ease: "power2.out",
            },
            0.06,
          )
          .fromTo(
            ".forge-glyph",
            {
              y: reduced ? 0 : 9,
              scale: reduced ? 1 : 0.97,
              opacity: reduced ? 0.55 : 1,
            },
            {
              y: 0,
              scale: 1,
              opacity: 1,
              duration: 0.28,
              stagger: 0.035,
              ease: "power2.out",
            },
            0.12,
          )
          .fromTo(
            ".forge-composed-word",
            {
              clipPath: reduced ? "inset(0)" : "inset(0 100% 0 0)",
              opacity: reduced ? 0.5 : 1,
            },
            {
              clipPath: "inset(0)",
              opacity: 1,
              duration: 0.32,
              ease: "power2.out",
            },
            0.38,
          )
          .to(
            ".forge-process-hand",
            {
              x: reduced ? 1 : 4,
              y: reduced ? 0 : -1,
              duration: 0.25,
              repeat: 1,
              yoyo: true,
              ease: "sine.inOut",
            },
            0.18,
          );
      }, how.current);
      cleanup = () => context.revert();
    });
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [activeSection, reduced]);

  useEffect(() => {
    if (activeSection < 2 || !root.current) return;
    // The final chapter is already a quiet FAQ and footer. Keep its controls
    // ready as soon as it arrives instead of animating the accordion wrapper.
    if (activeSection === 4) return;
    const section = root.current.querySelectorAll<HTMLElement>(
      "[data-landing-section]",
    )[activeSection];
    const paper = section?.querySelector<HTMLElement>(
      activeSection === 2
        ? '[aria-label="Your writing preview"]'
        : activeSection === 3
          ? '[aria-label="Sample handwriting style proof"]'
          : `.${styles.questions}`,
    );
    if (!paper) return;
    let cancelled = false;
    let cleanup = () => {};
    void import("gsap").then(({ default: gsap }) => {
      if (cancelled) return;
      const context = gsap.context(() => {
        gsap.fromTo(
          paper,
          {
            y: reduced ? 0 : 14,
            scale: reduced ? 1 : 0.97,
            opacity: reduced ? 0.65 : 1,
          },
          { y: 0, scale: 1, opacity: 1, duration: 0.32, ease: "power2.out" },
        );
      }, section);
      cleanup = () => context.revert();
    });
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [activeSection, reduced]);

  function navigateToSection(index: number) {
    goToSection(index);
    root.current
      ?.querySelectorAll<HTMLElement>("[data-landing-section]")
      [index]?.focus({ preventScroll: true });
  }

  return (
    <div ref={root} className={styles.landing}>
      <header className={styles.header}>
        <button
          type="button"
          className={styles.wordmark}
          onClick={() => navigateToSection(0)}
          aria-label="Penforge, back to introduction"
        >
          <BrandMark />
        </button>
        <nav className={styles.navigation} aria-label="Main navigation">
          <button
            type="button"
            onClick={() => navigateToSection(1)}
            aria-current={activeSection === 1 ? "location" : undefined}
          >
            How it works
          </button>
          <button
            type="button"
            onClick={() => navigateToSection(2)}
            aria-current={activeSection === 2 ? "location" : undefined}
          >
            Writing desk
          </button>
          <Link href="/projects">Workbench</Link>
        </nav>
        <div className={styles.headerActions}>
          <Link className={styles.navCta} href="/create">
            Create my font <Icon name="arrow" />
          </Link>
        </div>
      </header>
      <main id="main-content">
        <section
          id="introduction"
          data-landing-section
          tabIndex={-1}
          className={`${styles.section} ${styles.hero}`}
          aria-labelledby="hero-title"
        >
          <div className={`${styles.sectionInner} ${styles.heroBody}`}>
            <div className={styles.heroCopy}>
              <h1 id="hero-title">
                <span data-hero-line className={styles.heroFirstLine}>
                  Your handwriting.
                </span>
                <br />
                <span data-hero-line className={styles.heroHandLine}>
                  A font of its own.
                </span>
              </h1>
            </div>
            <div className={styles.characterStage}>
              <div
                className={`${styles.heroCharacter} ${styles.heroPen}`}
                aria-hidden="true"
              >
                <PenCharacter mood={penHappy ? "happy" : "idle"} />
              </div>
              <div
                className={`${styles.heroCharacter} ${styles.heroQuill}`}
                aria-hidden="true"
              >
                <QuillCharacter mood="happy" />
              </div>
              <div className={styles.heroFilm}>
                <HandwritingFilm
                  gentle={reduced}
                  active={activeSection === 0}
                />
              </div>
            </div>
            <div className={styles.heroActions}>
              <Link
                href="/create"
                className={styles.primary}
                onMouseEnter={() => setPenHappy(true)}
                onMouseLeave={() => setPenHappy(false)}
              >
                Create my font <Icon name="arrow" />
              </Link>
              <button type="button" onClick={() => navigateToSection(1)}>
                How it works <Icon name="down" />
              </button>
            </div>
          </div>
        </section>
        <section
          ref={how}
          id="how-it-works"
          data-landing-section
          tabIndex={-1}
          className={`${styles.section} ${styles.how}`}
          aria-labelledby="how-title"
        >
          <div className={styles.sectionInner}>
            <div className={styles.sectionHeading}>
              <h2 id="how-title">How it works.</h2>
              <p>Your page → your letters → your font</p>
            </div>
            <HowItWorksDemo />
            <div className={styles.howBottom}>
              <button
                type="button"
                className={styles.textLink}
                onClick={() => navigateToSection(2)}
              >
                Put your font to work <Icon name="arrow" />
              </button>
            </div>
          </div>
          <div className={`${styles.sectionInner} ${styles.sceneFooter}`}>
            <button type="button" onClick={() => navigateToSection(2)}>
              Writing desk <Icon name="down" />
            </button>
          </div>
        </section>
        <section
          id="playground"
          data-landing-section
          tabIndex={-1}
          className={`${styles.section} ${styles.playground}`}
          aria-labelledby="playground-title"
        >
          <div className={`${styles.sectionInner} ${styles.playgroundBody}`}>
            <div className={styles.playgroundCopy}>
              <span className={styles.eyebrow}>THE WRITING DESK</span>
              <h2 id="playground-title">
                Your words.
                <br />
                <span>Ready to send.</span>
              </h2>
            </div>
            <WritingDesk compact />
          </div>
          <div className={`${styles.sectionInner} ${styles.sceneFooter}`}>
            <button type="button" onClick={() => navigateToSection(3)}>
              Make it yours <Icon name="down" />
            </button>
          </div>
        </section>
        <section
          id="make-it-yours"
          data-landing-section
          tabIndex={-1}
          className={`${styles.section} ${styles.finish}`}
          aria-labelledby="finish-title"
        >
          <div className={styles.sectionInner}>
            <FinishDemo />
          </div>
          <div className={`${styles.sectionInner} ${styles.sceneFooter}`}>
            <button type="button" onClick={() => navigateToSection(4)}>
              Before you begin <Icon name="down" />
            </button>
          </div>
        </section>
        <section
          id="before-you-begin"
          data-landing-section
          tabIndex={-1}
          className={`${styles.section} ${styles.closing}`}
          aria-labelledby="closing-title"
        >
          <div className={styles.closingContent}>
            <span className={styles.eyebrow}>BEFORE YOUR FIRST FONT</span>
            <h2 id="closing-title">
              A few things.
              <br />
              <span>Before your first font.</span>
            </h2>
            <Accordion
              type="single"
              collapsible
              className={styles.questions}
              role="region"
              aria-label="Frequently asked questions"
            >
              {questions.map(({ question, answer }) => (
                <AccordionItem
                  key={question}
                  value={question}
                  className={styles.questionItem}
                >
                  <AccordionTrigger className={styles.questionTrigger}>
                    {question}
                  </AccordionTrigger>
                  <AccordionContent className={styles.answer}>
                    <p>{answer}</p>
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
            <div className={styles.finalInvitation}>
              <div className={styles.finalMark} aria-hidden="true">
                <BrandMark />
              </div>
              <h3>Leave your own mark.</h3>
              <Link href="/create" className={styles.primary}>
                Create my font <Icon name="arrow" />
              </Link>
              <p>No perfect penmanship required.</p>
            </div>
          </div>
          <div className={styles.sectionInner}>
            <SiteFooter onNavigateSection={navigateToSection} />
          </div>
        </section>
      </main>
      <nav className={styles.sectionRail} aria-label="Page sections">
        {sectionNames.map((section, index) => (
          <button
            key={section}
            type="button"
            aria-label={section}
            aria-current={activeSection === index ? "location" : undefined}
            onClick={() => navigateToSection(index)}
          >
            <span />
          </button>
        ))}
      </nav>
    </div>
  );
}
