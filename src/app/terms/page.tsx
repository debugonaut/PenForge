import type { Metadata } from "next";
import { LegalPage } from "@/components/legal/LegalPage";
import policy from "../../../packages/contracts/policies/service-use.v1.json";

export const metadata: Metadata = {
  title: "Terms — Penforge",
  description:
    "Penforge’s pre-release terms notice for account and local demo use.",
};
export default function Page() {
  return (
    <LegalPage
      eyebrow="Terms · pre-release draft"
      title="Create with care."
      lede="Use your own handwriting, respect other people’s rights, and review your font before use."
      updated={`Draft · ${policy.version} · Operator identity and contact are not yet published. Indian legal review and operator details are required before public launch.`}
      sections={policy.sections}
    />
  );
}
