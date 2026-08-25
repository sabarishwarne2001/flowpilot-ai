/**
 * Client-side security primitives for FlowPilot AI.
 *
 * Pure functions, no imports. Everything here is reachable from the axios
 * interceptor, which runs outside React, and from route guards, which run
 * inside it — so this module must not touch a store, a hook, or `window`
 * beyond what each function explicitly reads.
 *
 * WHY OPEN-REDIRECT VALIDATION IS ITS OWN MODULE
 * ==============================================
 *
 * The 401 path in `client.ts` sends the user to `/login?redirect=<path>` and
 * `Login.tsx` reads that parameter back and navigates to it. That is a value
 * which left the application, sat in a URL bar where anyone can edit it, and
 * came back. Treating it as a path is the entire vulnerability: an attacker
 * mails `/login?redirect=https://flowpilot-ai.evil/login`, the victim signs in
 * for real, and the app forwards them to a pixel-perfect clone that asks them
 * to sign in "again".
 *
 * The rule is allow-list, not deny-list. Every function below answers "is this
 * one of the shapes I accept" rather than "does this contain something bad",
 * because the set of bad shapes is unbounded and grows with every browser URL
 * parsing quirk.
 */

/* ==========================================================================
 * Redirect validation
 * ========================================================================== */

/**
 * Path prefixes that are structurally valid but must never be a post-login
 * destination.
 *
 * Sending a freshly authenticated user back to `/login` produces a redirect
 * loop; sending them to `/logout` signs them straight back out. Both are
 * reachable by hand-editing the parameter, and neither is malicious enough to
 * warrant an error — they are simply dropped in favour of the default.
 */
const REDIRECT_DENY_PREFIXES: readonly string[] = [
  "/login",
  "/register",
  "/logout",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
];

/** Where a rejected or absent redirect lands. */
export const DEFAULT_REDIRECT_PATH = "/";

/**
 * True when `candidate` is a same-origin, path-only destination this app may
 * navigate to after authentication.
 *
 * Accepts exactly one shape: a string beginning with a single `/`, optionally
 * followed by a query and a fragment. Everything else is refused.
 *
 * The refusals, and why each one is not paranoia:
 *
 * - `https://evil.com/x` — absolute URL. The obvious case.
 * - `//evil.com/x` — protocol-relative. Browsers resolve this to
 *   `https://evil.com/x`, but a naive `startsWith("/")` check passes it. This
 *   is the single most commonly missed open-redirect shape.
 * - `/\evil.com` and `\\evil.com` — several browsers normalise a backslash to
 *   a forward slash during URL parsing, so this is `//evil.com` wearing a hat.
 * - `javascript:alert(1)` — refused by the leading-slash requirement, but
 *   worth naming because it is what makes `href={redirect}` dangerous rather
 *   than merely wrong.
 * - `/a/../../b` — traversal. Cannot escape the origin in a browser, but it
 *   can escape a *tenant* path segment, which is the boundary that matters
 *   here. `/acme/ws/../../globex/ws` is same-origin and still crosses tenants.
 * - Control characters and whitespace — `/\tjavascript:...` and
 *   `/%0A//evil.com` survive some sanitisers and are stripped by the parser
 *   afterwards.
 *
 * @param candidate - Untrusted value, typically from a query parameter.
 * @returns Whether the value may be passed to `navigate()`.
 */
export const isSafeRedirectPath = (candidate: unknown): candidate is string => {
  if (typeof candidate !== "string") {
    return false;
  }

  // Length ceiling. A redirect longer than this is not a route in this app;
  // it is someone probing the parser.
  if (candidate.length === 0 || candidate.length > 2048) {
    return false;
  }

  // Reject anything containing a control character, including the tab,
  // newline, and carriage return that browsers strip *after* a naive check
  // has already approved the string.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001F\u007F]/.test(candidate)) {
    return false;
  }

  // Must be path-absolute. This alone refuses `javascript:`, `data:`,
  // `https://`, and every other scheme.
  if (!candidate.startsWith("/")) {
    return false;
  }

  // Protocol-relative, in both its honest and its backslash-disguised forms.
  // Checked on the raw string before any normalisation, because normalisation
  // is exactly what turns the disguise back into the attack.
  if (
    candidate.startsWith("//") ||
    candidate.startsWith("/\\") ||
    candidate.startsWith("/%2F") ||
    candidate.toLowerCase().startsWith("/%5c")
  ) {
    return false;
  }

  // A backslash anywhere in the path portion is refused outright. There is no
  // legitimate route in this application containing one, and its parsing is
  // inconsistent across browsers — which is precisely the property an attacker
  // needs.
  const pathPortion = candidate.split(/[?#]/, 1)[0] ?? "";
  if (pathPortion.includes("\\")) {
    return false;
  }

  // Traversal. Refused on the decoded form so that `%2e%2e` is caught with
  // `..`. Decoding can throw on a malformed sequence — that is itself a
  // refusal, not an error to report.
  let decodedPath: string;
  try {
    decodedPath = decodeURIComponent(pathPortion);
  } catch {
    return false;
  }

  if (decodedPath.split("/").includes("..")) {
    return false;
  }

  // Structural parse against a throwaway origin. If the result does not land
  // back on that origin, something in the string redirected it — and whatever
  // that something was, this is not a path.
  try {
    const probe = new URL(candidate, "https://redirect-probe.invalid");
    if (probe.origin !== "https://redirect-probe.invalid") {
      return false;
    }
  } catch {
    return false;
  }

  // Finally, the application-level deny list.
  const lowered = decodedPath.toLowerCase();
  return !REDIRECT_DENY_PREFIXES.some(
    (prefix) => lowered === prefix || lowered.startsWith(`${prefix}/`),
  );
};

/**
 * Returns `candidate` when it is a safe redirect, and the default otherwise.
 *
 * Prefer this at call sites over calling `isSafeRedirectPath` and branching:
 * a rejected redirect is a normal event — a stale bookmark, a copied link, a
 * user editing the URL — and it should quietly resolve to the default rather
 * than surface an error.
 *
 * @param candidate - Untrusted value, typically from a query parameter.
 * @param fallback - Destination used when the candidate is refused.
 */
export const sanitizeRedirectPath = (
  candidate: unknown,
  fallback: string = DEFAULT_REDIRECT_PATH,
): string => (isSafeRedirectPath(candidate) ? candidate : fallback);

/**
 * Builds the `/login?redirect=…` URL for a session that has just ended.
 *
 * Centralised so that the interceptor, the route guards, and the step-up modal
 * cannot drift into three different encodings of the same idea. The current
 * location is validated on the way in: it is same-origin by construction, but
 * it can still be a denied path, and bouncing `/login` back to `/login` is the
 * loop this exists to prevent.
 *
 * @param currentPath - Usually `location.pathname + location.search`.
 */
export const buildLoginRedirect = (currentPath: string): string => {
  if (!isSafeRedirectPath(currentPath)) {
    return "/login";
  }
  return `/login?redirect=${encodeURIComponent(currentPath)}`;
};

/* ==========================================================================
 * Fragment credential reader
 * ========================================================================== */

/**
 * Reads a one-time token from the URL fragment and erases it in the same tick.
 *
 * WHY THE FRAGMENT AND NOT THE QUERY STRING
 * =========================================
 *
 * The fragment is never sent to a server. A token in `?token=` reaches the
 * origin server, every proxy in front of it, and both access logs, and it
 * lands in the `Referer` header of the next outbound request the page makes.
 * A token in `#token=` stays in the browser.
 *
 * This matters for the SSO and invitation-acceptance handoffs, where the
 * backend redirects the browser to the SPA carrying a credential.
 *
 * The history entry is replaced rather than pushed, so the credential does not
 * survive a back-navigation and does not appear in the session history that a
 * screen-share or a shoulder-surfer can reach.
 *
 * @param key - Fragment parameter name. Defaults to `token`.
 * @returns The token, or null when the fragment does not carry one.
 */
export const consumeFragmentToken = (key = "token"): string | null => {
  if (typeof window === "undefined" || !window.location.hash) {
    return null;
  }

  const raw = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;

  let token: string | null = null;

  try {
    token = new URLSearchParams(raw).get(key);
  } catch {
    return null;
  }

  if (!token) {
    return null;
  }

  // Erase before returning. If anything downstream throws, the credential is
  // already out of the address bar rather than waiting for a cleanup that
  // never runs.
  try {
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${window.location.search}`,
    );
  } catch {
    // A replaceState failure is not a reason to discard a valid token. The
    // fragment remains visible, which is worse than the alternative but not
    // worse than forcing the user back through the whole handoff.
  }

  return token;
};
