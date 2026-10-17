// Read by astro.config.mjs (plain JavaScript, before Vite) and by src/config/site.ts.
// One place for the website's addresses: change a domain here or with the build variables.

/** The website's own address, with no trailing slash. */
export function siteUrl() {
  return (process.env.PUBLIC_SITE_URL || "https://servabo.app").replace(/\/+$/, "");
}
