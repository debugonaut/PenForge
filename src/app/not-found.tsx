import Link from "next/link";
import { PenCharacter, QuillCharacter } from "@/components/brand/Characters";

export default function NotFound() {
  return (
    <main id="main-content" className="missing-page">
      <div className="intro-characters" aria-hidden="true">
        <PenCharacter mood="surprised" />
        <QuillCharacter mood="surprised" />
      </div>
      <span className="scene-number">404 / A STRAY PAGE</span>
      <h1>
        This page wandered
        <br />
        <em>off the desk.</em>
      </h1>
      <p>Let&apos;s get you back to making something.</p>
      <Link href="/" className="button button-dark">
        Return to Penforge <span>→</span>
      </Link>
    </main>
  );
}
