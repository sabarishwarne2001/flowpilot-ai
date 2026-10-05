/**
 * The harness proves its own strictness: a page that logs console.error, throws,
 * rejects a promise or receives a 500 must fail the test.
 */
import fs from "node:fs";

import { test, expect, ProblemTracker, attachProblemListeners } from "../support/fixtures";
import { API_BASE, STATE_FILE } from "../support/env";

test.describe("harness", () => {
  test("the API is healthy and the seed state exists", async () => {
    const response = await fetch(`${API_BASE}/health`);
    expect(response.status).toBe(200);
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    expect(Object.keys(state.seed.users)).toHaveLength(11);
    for (const tenant of ["A", "B", "C"]) {
      const items = state.documents[tenant].items as Array<{ status: string }>;
      expect(items.length, `tenant ${tenant} has sample documents`).toBeGreaterThan(0);
    }
  });

  test("the strict listeners catch every kind of page problem", async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    const tracker = new ProblemTracker();
    attachProblemListeners(page, tracker);
    await page.addInitScript(() => {
      window.addEventListener("unhandledrejection", (event) => {
        console.error("[e2e:unhandledrejection]", String(event.reason));
      });
    });
    await page.route("http://harness.e2e/500", (route) => route.fulfill({ status: 500, body: "boom" }));
    await page.route("http://harness.e2e/", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<html><body>harness</body></html>" }),
    );
    await page.goto("http://harness.e2e/");
    await page.evaluate(async () => {
      console.error("deliberate console error");
      setTimeout(() => {
        throw new Error("deliberate uncaught error");
      }, 0);
      void Promise.reject(new Error("deliberate rejection"));
      await fetch("http://harness.e2e/500").catch(() => undefined);
    });
    await page.waitForTimeout(500);
    const kinds = new Set(tracker.unexpected().map((p) => p.kind));
    expect(kinds, tracker.summary(tracker.all)).toEqual(new Set(["console", "pageerror", "unhandledrejection", "http"]));
    await context.close();
  });
});
