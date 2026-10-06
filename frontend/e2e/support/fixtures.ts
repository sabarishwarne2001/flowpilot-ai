/**
 * The strict test fixture every spec imports instead of @playwright/test.
 *
 * A test FAILS (in fixture teardown, after its own assertions) when the page
 * produced any of:
 *   - an uncaught exception (`pageerror`), including React render crashes;
 *   - an unhandled promise rejection (an init script reports them);
 *   - a `console.error` message;
 *   - an HTTP response with status >= 400 from the app or the API;
 *   - a failed request (connection refused, CORS), except navigation aborts.
 *
 * A test that EXPECTS an error says so up front, with a reason:
 *   problems.allowHttp(/\/billing\/checkout/, [402], "Developer plan lacks it");
 * Allowed HTTP statuses also excuse Chromium's matching
 * "Failed to load resource" console line.
 *
 * KNOWN ISSUES. A defect that would fail EVERY test (so nothing else could be
 * seen) is listed in KNOWN_ISSUES with its FINDINGS.md id. It is still
 * recorded, attached to the report as a `known-issue` annotation and proven by
 * its own test in tests/known-issues.spec.ts (which flips red once the bug is
 * fixed, so the entry gets removed). E2E_STRICT_KNOWN=1 fails on them too.
 *
 * Sign-in: `test.use({ user: "C.owner" })` gives the test a FRESH session.
 * Refresh tokens rotate and a reused token ends the whole session family, so
 * sessions are never shared between tests. The fixture signs in through the
 * API (POST /auth/login), puts the refresh cookie in the browser exactly where
 * the server would, and marks the persisted auth store as signed in; the app
 * then performs its normal /auth/refresh bootstrap. Login through the real
 * form is covered by tests/auth.spec.ts.
 */
import fs from "node:fs";

import { test as base, expect, type Page, type TestInfo } from "@playwright/test";

import { api, loginAs, type ApiResult, type ApiSession } from "./api";
import { BROWSER_API_ORIGIN, TENANTS, type UserKey } from "./env";

export interface Problem {
  readonly kind: "pageerror" | "unhandledrejection" | "console" | "http" | "requestfailed";
  message: string;
  readonly url?: string;
  readonly status?: number;
  readonly method?: string;
  readonly at: string;
}

interface AllowRule {
  readonly pattern: RegExp;
  readonly statuses: readonly number[] | "any";
  readonly reason: string;
}

export class ProblemTracker {
  readonly all: Problem[] = [];
  private readonly httpRules: AllowRule[] = [];
  private readonly consoleRules: { pattern: RegExp; reason: string }[] = [];
  private pageUrl = "";

  setPageUrl(url: string): void {
    this.pageUrl = url;
  }

  record(problem: Omit<Problem, "at">): Problem {
    const recorded: Problem = { ...problem, at: this.pageUrl };
    this.all.push(recorded);
    return recorded;
  }

  /** Declare an HTTP error this test expects. `statuses: "any"` for any >= 400. */
  allowHttp(pattern: RegExp | string, statuses: readonly number[] | "any", reason: string): void {
    const regex = typeof pattern === "string" ? new RegExp(pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) : pattern;
    this.httpRules.push({ pattern: regex, statuses, reason });
  }

  /** Declare a console.error line this test expects. */
  allowConsole(pattern: RegExp, reason: string): void {
    this.consoleRules.push({ pattern, reason });
  }

  private httpAllowed(url: string | undefined, status: number | undefined): boolean {
    if (!url || status === undefined) return false;
    return this.httpRules.some(
      (rule) => rule.pattern.test(url) && (rule.statuses === "any" || rule.statuses.includes(status)),
    );
  }

  private allowedStatusesSeen(): Set<number> {
    const seen = new Set<number>();
    for (const problem of this.all) {
      if (problem.kind === "http" && this.httpAllowed(problem.url, problem.status) && problem.status) {
        seen.add(problem.status);
      }
    }
    return seen;
  }

  /** Known issues seen in this test, by finding id. */
  knownSeen(): Map<string, number> {
    const seen = new Map<string, number>();
    for (const problem of this.all) {
      const issue = KNOWN_ISSUES.find((known) => known.matches(problem));
      if (issue) seen.set(issue.finding, (seen.get(issue.finding) ?? 0) + 1);
    }
    return seen;
  }

  unexpected(): Problem[] {
    const allowedStatuses = this.allowedStatusesSeen();
    return this.all.filter((problem) => {
      if (!STRICT_KNOWN && KNOWN_ISSUES.some((known) => known.matches(problem))) {
        return false;
      }
      if (problem.kind === "http") {
        return !this.httpAllowed(problem.url, problem.status);
      }
      if (problem.kind === "console") {
        // Chromium logs every failed fetch as a console error; the response
        // listener already judged it, so only an unexplained one counts.
        const resource = /Failed to load resource: the server responded with a status of (\d+)/.exec(
          problem.message,
        );
        if (resource) {
          return !allowedStatuses.has(Number(resource[1]));
        }
        return !this.consoleRules.some((rule) => rule.pattern.test(problem.message));
      }
      return true;
    });
  }

  summary(problems = this.unexpected()): string {
    return problems
      .map((p) => {
        const where = p.url ? ` ${p.method ?? ""} ${p.url}`.trimEnd() : "";
        const status = p.status ? ` [${p.status}]` : "";
        return `- ${p.kind}${status}${where} :: ${p.message.slice(0, 500)} (page: ${p.at})`;
      })
      .join("\n");
  }
}

export interface KnownIssue {
  readonly finding: string;
  readonly summary: string;
  readonly matches: (problem: Problem) => boolean;
}

/**
 * Empty since F-049 was fixed (has_avatar on /auth/me; the sidebar asks for an
 * avatar only when there is one). Add an entry only for a finding that is
 * recorded in FINDINGS.md and not fixed yet, with a proof in known-issues.spec.ts.
 */
export const KNOWN_ISSUES: readonly KnownIssue[] = [];

const STRICT_KNOWN = process.env.E2E_STRICT_KNOWN === "1";

const IGNORED_REQUEST_FAILURES = /net::ERR_ABORTED|NS_BINDING_ABORTED|net::ERR_BLOCKED_BY_CLIENT/;

export function attachProblemListeners(page: Page, tracker: ProblemTracker): void {
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) tracker.setPageUrl(frame.url());
  });
  page.on("pageerror", (error) => {
    tracker.record({ kind: "pageerror", message: `${error.name}: ${error.message}\n${error.stack ?? ""}` });
  });
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (text.startsWith("[e2e:unhandledrejection]")) {
      tracker.record({ kind: "unhandledrejection", message: text });
      return;
    }
    tracker.record({ kind: "console", message: text, url: message.location().url });
  });
  page.on("response", (response) => {
    const status = response.status();
    if (status < 400) return;
    const problem = tracker.record({
      kind: "http",
      status,
      url: response.url(),
      method: response.request().method(),
      message: response.statusText() || `HTTP ${status}`,
    });
    // The body is the evidence (validation detail, error code); keep its start.
    response
      .text()
      .then((body) => {
        if (body) problem.message = `${problem.message} body=${body.replace(/\s+/g, " ").slice(0, 300)}`;
      })
      .catch(() => undefined);
  });
  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText ?? "unknown";
    if (IGNORED_REQUEST_FAILURES.test(failure)) return;
    tracker.record({ kind: "requestfailed", message: failure, url: request.url(), method: request.method() });
  });
}

const UNHANDLED_REJECTION_SCRIPT = () => {
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason as { stack?: string; message?: string } | undefined;
    // eslint-disable-next-line no-console
    console.error("[e2e:unhandledrejection]", String(reason?.stack ?? reason?.message ?? reason));
  });
};

const AUTH_STORAGE_KEY = "flowpilot_auth_session";

export async function signInContext(page: Page, userKey: UserKey): Promise<ApiSession> {
  const session = await loginAs(userKey);
  await page.context().addCookies([
    {
      name: "flowpilot_refresh",
      value: session.refreshToken,
      domain: new URL(BROWSER_API_ORIGIN).hostname,
      path: "/api/v1/auth/refresh",
      httpOnly: true,
      secure: false,
      sameSite: "Lax",
    },
  ]);
  const persisted = JSON.stringify({ state: { user: session.me, isAuthenticated: true }, version: 0 });
  await page.context().addInitScript(
    ({ key, value }) => {
      // Once per tab: a later sign-out must not be undone by the next navigation.
      if (!window.sessionStorage.getItem("__e2e_auth_seeded")) {
        window.localStorage.setItem(key, value);
        window.sessionStorage.setItem("__e2e_auth_seeded", "1");
      }
    },
    { key: AUTH_STORAGE_KEY, value: persisted },
  );
  return session;
}

async function captureEvidence(page: Page, testInfo: TestInfo, tracker: ProblemTracker): Promise<void> {
  const problems = tracker.unexpected();
  if (problems.length === 0) return;
  await testInfo.attach("problems.json", {
    body: JSON.stringify(problems, null, 2),
    contentType: "application/json",
  });
  try {
    const shot = testInfo.outputPath("problem.png");
    await page.screenshot({ path: shot, fullPage: false, timeout: 5_000 });
    if (fs.existsSync(shot)) {
      await testInfo.attach("problem.png", { path: shot, contentType: "image/png" });
    }
  } catch {
    // the page may already be closed; the problem list is the evidence
  }
}

export type CallApi = <T = unknown>(method: string, pathname: string, body?: unknown) => Promise<ApiResult<T>>;

interface Fixtures {
  user: UserKey | null;
  problems: ProblemTracker;
  session: ApiSession | null;
  callApi: CallApi;
}

export const test = base.extend<Fixtures>({
  user: [null, { option: true }],

  problems: [
    async ({ page }, use, testInfo) => {
      const tracker = new ProblemTracker();
      await page.context().addInitScript(UNHANDLED_REJECTION_SCRIPT);
      attachProblemListeners(page, tracker);
      await use(tracker);
      for (const [finding, count] of tracker.knownSeen()) {
        testInfo.annotations.push({ type: "known-issue", description: `${finding} seen ${count}x` });
      }
      await captureEvidence(page, testInfo, tracker);
      const problems = tracker.unexpected();
      if (problems.length > 0) {
        throw new Error(
          `The page reported ${problems.length} unexpected problem(s) (strict e2e policy):\n${tracker.summary(problems)}`,
        );
      }
    },
    { auto: true },
  ],

  session: [
    async ({ page, user }, use) => {
      if (!user) {
        await use(null);
        return;
      }
      await use(await signInContext(page, user));
    },
    { auto: true },
  ],

  callApi: async ({ session }, use) => {
    await use((method, pathname, body) => api(session, method, pathname, body));
  },
});

export { expect };

/** Fail if the page shows the error boundary, a 404 page or nothing at all. */
export async function expectHealthyPage(page: Page): Promise<void> {
  await expect(page.getByText("Something went wrong", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Page not found" })).toHaveCount(0);
  // Polled: right after a navigation the next screen may still be loading its code.
  // A page that stays blank still fails.
  await expect
    .poll(async () => (await page.locator("body").innerText()).trim().length, {
      message: "page body is blank",
      timeout: 15_000,
    })
    .toBeGreaterThan(20);
}

/** Wait until no spinner-only splash is shown and the network is quiet. */
export async function settle(page: Page, quietMs = 500): Promise<void> {
  await page.waitForLoadState("domcontentloaded");
  try {
    await page.waitForLoadState("networkidle", { timeout: 15_000 });
  } catch {
    // long-polling pages never go idle; the assertions that follow decide
  }
  await page.waitForTimeout(quietMs);
}

export const tenantName = (key: keyof typeof TENANTS): string => TENANTS[key].name;
