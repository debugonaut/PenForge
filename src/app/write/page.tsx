import type { Metadata } from "next";
import WritingDesk from "@/components/writing/WritingDesk";

export const metadata: Metadata = {
  title: "Writing desk — Penforge",
  description:
    "Write a personal letter, note, or card. Style your paper and export a real image with your words in handwriting.",
};

export default function WritingPage() {
  return <WritingDesk />;
}
