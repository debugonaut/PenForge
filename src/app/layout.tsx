import type { Metadata } from "next";
import "@fontsource/fraunces/latin-400.css";
import "@fontsource/fraunces/latin-400-italic.css";
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-500.css";
import "@fontsource/dm-sans/latin-700.css";
import "@fontsource/dm-sans/latin-800.css";
import "@fontsource/dm-sans/latin-900.css";
import "@fontsource-variable/caveat/index.css";
import "./globals.css";
import "./studio.css";
import { MotionPreference } from "@/components/shared/MotionPreference";
import { RouteTransition } from "@/components/shared/RouteTransition";

export const metadata: Metadata = {
  title: "Penforge — Turn your handwriting into a font",
  description:
    "An illustrated, hands-on place to turn the character of your handwriting into a font.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body>
        <MotionPreference />
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        {children}
        <RouteTransition />
      </body>
    </html>
  );
}
