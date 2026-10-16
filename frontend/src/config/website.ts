/**
 * The public website (website session, B7). Pricing, the legal pages and the product story live
 * on the website (website/ in this repository, https://servabo.app); the app links there instead
 * of keeping its own copies, so the two never disagree.
 *
 * VITE_WEBSITE_URL overrides the address (the fallback domain, a preview deployment, or the
 * browser tests' stand-in). Development defaults to the website's dev server on port 4321
 * (`start_dev.sh --website`), production to https://servabo.app.
 */
const configured = (import.meta.env.VITE_WEBSITE_URL as string | undefined)?.trim();

export const WEBSITE_URL = (configured || (import.meta.env.DEV ? "http://localhost:4321" : "https://servabo.app")).replace(
  /\/+$/,
  "",
);

/** The website's pages the app links to. */
export const WEBSITE = {
  home: `${WEBSITE_URL}/`,
  pricing: `${WEBSITE_URL}/pricing`,
  terms: `${WEBSITE_URL}/legal/terms`,
  privacy: `${WEBSITE_URL}/legal/privacy`,
  refunds: `${WEBSITE_URL}/legal/refunds`,
} as const;

/** The website's host name, for link text ("servabo.app"). */
export const WEBSITE_HOST = new URL(WEBSITE_URL).host;
