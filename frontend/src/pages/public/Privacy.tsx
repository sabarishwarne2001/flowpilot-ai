/**
 * Campaign session 4 (CAMPAIGN-MAP 4.3) — privacy policy, a placeholder for the owner's legal
 * review (N-029: no legal or compliance claims, no sub-processors, retention periods,
 * jurisdictions or residency statements). Headings only; counsel writes the text.
 */
import React from "react";

import { LegalPlaceholder, type LegalSection } from "@/components/public/LegalPlaceholder";

const SECTIONS: readonly LegalSection[] = [
  { id: "controller", heading: "Who is responsible for your information", covers: "the company responsible and how to contact it." },
  { id: "collected", heading: "Information collected", covers: "the categories of information collected about account holders and visitors." },
  { id: "use", heading: "How information is used", covers: "the purposes information is used for, and the reasons that allow it." },
  { id: "customer-content", heading: "Documents and content organizations upload", covers: "how content uploaded by customer organizations is handled, and who decides about it." },
  { id: "ai", heading: "AI processing", covers: "how AI features process information." },
  { id: "sharing", heading: "Sharing and service providers", covers: "who information may be shared with, and why." },
  { id: "transfers", heading: "International transfers", covers: "where information may be processed." },
  { id: "retention", heading: "How long information is kept", covers: "how long each category of information is kept." },
  { id: "security", heading: "Security", covers: "how information is protected." },
  { id: "rights", heading: "Your choices and rights", covers: "the choices and rights people have over their information, and how to use them." },
  { id: "cookies", heading: "Cookies and similar technologies", covers: "the cookies and similar technologies the service uses." },
  { id: "children", heading: "Children", covers: "use of the service by children." },
  { id: "changes", heading: "Changes to this policy", covers: "how this policy may change and how changes are announced." },
  { id: "contact", heading: "Contact", covers: "how to ask a question or make a request about personal information." },
];

const Privacy: React.FC = () => (
  <LegalPlaceholder title="Privacy policy" documentName="privacy policy" sections={SECTIONS} />
);

export default Privacy;
