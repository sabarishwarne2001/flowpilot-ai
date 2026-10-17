/**
 * The website's settings, in one place.
 *
 * Every address the site prints or links to comes from here. A domain change is one build
 * variable (PUBLIC_SITE_URL, PUBLIC_APP_URL) or one line below; docs/website/DEPLOY.md shows the
 * switch to the fallback domain (getservabo.com). Nothing on a shipped page is a placeholder.
 */

const env = import.meta.env;

function clean(value: string | undefined, fallback: string): string {
  const v = (value ?? "").trim();
  return (v || fallback).replace(/\/+$/, "");
}

/** `astro dev` links to the app's dev server; a build links to the real app. */
const DEV_APP_URL = "http://localhost:5173";

export const SITE = {
  name: "Servabo",
  /** Latin, "I will keep", from pacta sunt servanda. */
  meaning: "Latin for “I will keep”, from pacta sunt servanda: agreements must be kept.",
  tagline: "The AI back office that keeps every promise your documents make.",
  /** The one-sentence product description, word for word as on the Dodo Payments form. */
  description:
    "Servabo is online software for finance and operations teams that reads invoices, purchase orders, contracts and statements, checks them against each other and plans what to pay, hold and chase, sold as a per-seat monthly or yearly subscription and available immediately after sign-up.",
  url: clean(env.PUBLIC_SITE_URL, "https://servabo.app"),
  appUrl: env.DEV ? clean(env.PUBLIC_APP_URL, DEV_APP_URL) : clean(env.PUBLIC_APP_URL, "https://app.servabo.app"),
  /** One setting: the launch checklist switches it to support@servabo.app. */
  contactEmail: (env.PUBLIC_CONTACT_EMAIL ?? "").trim() || "sabarishwarne@gmail.com",
  /** Optional, never committed: shown on the contact page only when the build sets it. */
  supportPhone: (env.PUBLIC_SUPPORT_PHONE ?? "").trim(),
  operator: {
    name: "Sabarish N",
    kind: "an individual (sole proprietor)",
    country: "India",
  },
  responseTime: "two business days",
  /** Shown as "Last updated" on every legal page. */
  legalUpdated: "2026-10-16",
  merchantOfRecord: "Dodo Payments",
} as const;

export const APP = {
  signIn: `${SITE.appUrl}/login`,
  register: `${SITE.appUrl}/register`,
};

/** Absolute URL for a path on this site. */
export function absolute(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return `${SITE.url}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Footer links, on every page: the five things Dodo's reviewers look for are all here. */
export const FOOTER = {
  product: [
    { href: "/helm", label: "Helm" },
    { href: "/product", label: "Product tour" },
    { href: "/pricing", label: "Pricing" },
    { href: "/security", label: "Security" },
  ],
  solutions: [
    { href: "/solutions/accounts-payable", label: "Finance & accounts payable" },
    { href: "/solutions/procurement", label: "Procurement" },
    { href: "/solutions/legal-compliance", label: "Legal & compliance" },
    { href: "/solutions/operations", label: "Operations" },
  ],
  company: [
    { href: "/about", label: "About" },
    { href: "/contact", label: "Contact" },
  ],
  legal: [
    { href: "/legal/terms", label: "Terms of Service" },
    { href: "/legal/privacy", label: "Privacy Policy" },
    { href: "/legal/refunds", label: "Refund & Cancellation" },
    { href: "/legal/acceptable-use", label: "Acceptable Use" },
    { href: "/legal/subprocessors", label: "Subprocessors" },
    { href: "/legal/cookies", label: "Cookies" },
  ],
} as const;

export const NAV = [
  { href: "/helm", label: "Helm" },
  { href: "/product", label: "Product" },
  { href: "/solutions/accounts-payable", label: "Solutions", match: "/solutions" },
  { href: "/pricing", label: "Pricing" },
  { href: "/security", label: "Security" },
] as const;
