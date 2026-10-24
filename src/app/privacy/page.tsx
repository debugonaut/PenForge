import type { Metadata } from "next";
import { LegalPage } from "@/components/legal/LegalPage";
import policy from "../../../packages/contracts/policies/service-use.v1.json";

export const metadata: Metadata = {
  title: "Privacy — Penforge",
  description:
    "Penforge’s pre-release privacy notice for account and local demo use.",
};
export default function Page() {
  return (
    <LegalPage
      eyebrow="Privacy · pre-release draft"
      title="Your handwriting stays yours."
      lede="Choose what stays on this device and what you save to your account."
      updated={`Draft · ${policy.version} · Operator identity and contact are not yet published. Indian legal review and operator details are required before public launch.`}
      sections={policy.privacy}
    />
  );
}
