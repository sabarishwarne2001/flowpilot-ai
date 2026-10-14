/**
 * Campaign session 4 (CAMPAIGN-MAP 4.3) — terms of service, a placeholder for the owner's
 * legal review (N-029: no legal or compliance claims). Headings only; counsel writes the text.
 */
import React from "react";

import { LegalPlaceholder, type LegalSection } from "@/components/public/LegalPlaceholder";

const SECTIONS: readonly LegalSection[] = [
  { id: "parties", heading: "Who these terms are between", covers: "the parties to the agreement and when it applies." },
  { id: "accounts", heading: "Accounts and organizations", covers: "creating an account, organizations, members and roles, and keeping sign-in details safe." },
  { id: "plans", heading: "Plans, seats and payment", covers: "plans, seats, billing periods, payment, renewals, refunds and taxes." },
  { id: "trial", heading: "The free trial", covers: "the trial, and what happens when it ends." },
  { id: "acceptable-use", heading: "Acceptable use", covers: "what the service may and may not be used for." },
  { id: "content", heading: "Your content", covers: "ownership of the documents and data an organization uploads, and the permissions needed to provide the service." },
  { id: "ai", heading: "AI features", covers: "the use of AI features and their outputs." },
  { id: "third-parties", heading: "Third-party services", covers: "services and integrations provided by others." },
  { id: "availability", heading: "Availability and changes to the service", covers: "changes to the service and any service commitments." },
  { id: "termination", heading: "Suspension and termination", covers: "how either party may suspend or end the agreement, and what happens to data afterwards." },
  { id: "liability", heading: "Disclaimers and limitation of liability", covers: "warranties, disclaimers and limits of liability." },
  { id: "changes", heading: "Changes to these terms", covers: "how these terms may change and how changes are announced." },
  { id: "law", heading: "Governing law and disputes", covers: "the law that governs the agreement and how disputes are resolved." },
  { id: "contact", heading: "Contact", covers: "how to reach the company about these terms." },
];

const Terms: React.FC = () => (
  <LegalPlaceholder title="Terms of service" documentName="terms of service" sections={SECTIONS} />
);

export default Terms;
