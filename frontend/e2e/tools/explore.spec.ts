/**
 * Page inventory: visits every page as one user and writes, per page, the
 * headings, buttons, links, inputs, problems and a screenshot into
 * e2e/.explore/. Not part of the suite; run it when a page changes:
 *   E2E_TEST_DIR=./tools E2E_EXPLORE_USER=C.owner npx playwright test -c e2e
 */
import fs from "node:fs";
import path from "node:path";

import { test, settle } from "../support/fixtures";
import { E2E_DIR, org, ws, type UserKey } from "../support/env";
import { ORGANIZATION_PAGES, PLATFORM_PAGES, WORKSPACE_PAGES } from "../support/routes";

const USER = (process.env.E2E_EXPLORE_USER ?? "C.owner") as UserKey;
const TENANT = USER.split(".")[0] as "A" | "B" | "C" | "P";
const OUT = path.join(E2E_DIR, ".explore", USER);

test.use({ user: USER });
test.setTimeout(600_000);

test(`inventory every page as ${USER}`, async ({ page, problems }) => {
  fs.mkdirSync(OUT, { recursive: true });
  const targets = [
    ...WORKSPACE_PAGES.map((p) => ({ name: `ws-${p.sub || "overview"}`, url: ws(TENANT, p.sub) })),
    ...ORGANIZATION_PAGES.map((p) => ({ name: `org-${p.sub}`, url: org(TENANT, p.sub) })),
    ...PLATFORM_PAGES.map((p) => ({ name: `platform-${p.path.split("/").pop()}`, url: p.path })),
  ];
  const report: Record<string, unknown> = {};
  for (const target of targets) {
    const before = problems.all.length;
    await page.goto(target.url);
    await settle(page, 1200);
    const inventory = await page.evaluate(() => {
      const visible = (el: Element) => {
        const r = (el as HTMLElement).getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      const text = (el: Element) => ((el as HTMLElement).innerText || el.getAttribute("aria-label") || el.getAttribute("title") || "").trim().replace(/\s+/g, " ").slice(0, 80);
      const main = document.querySelector("main") ?? document.body;
      return {
        url: location.pathname,
        headings: [...main.querySelectorAll("h1,h2,h3")].filter(visible).map(text).slice(0, 25),
        buttons: [...main.querySelectorAll("button,[role=button],[role=tab],[role=switch]")].filter(visible).map((b) => `${text(b)}${(b as HTMLButtonElement).disabled ? " [disabled]" : ""}`).slice(0, 60),
        links: [...main.querySelectorAll("a[href]")].filter(visible).map((a) => `${text(a)} -> ${a.getAttribute("href")}`).slice(0, 40),
        inputs: [...main.querySelectorAll("input,select,textarea")].filter(visible).map((i) => `${i.tagName.toLowerCase()}[${i.getAttribute("type") ?? ""}] name=${i.getAttribute("name") ?? ""} placeholder=${i.getAttribute("placeholder") ?? ""} label=${i.getAttribute("aria-label") ?? ""} id=${i.id}`).slice(0, 40),
        bodyStart: (main as HTMLElement).innerText.replace(/\s+/g, " ").slice(0, 600),
      };
    });
    const newProblems = problems.all.slice(before).map((p) => `${p.kind} ${p.status ?? ""} ${p.method ?? ""} ${p.url ?? ""} :: ${p.message.slice(0, 200)}`);
    report[target.name] = { ...inventory, problems: newProblems };
    await page.screenshot({ path: path.join(OUT, `${target.name}.png`) });
  }
  fs.writeFileSync(path.join(OUT, "inventory.json"), JSON.stringify(report, null, 2));
  // The inventory is a tool, not an assertion: problems are reported, not failed.
  problems.allowHttp(/.*/, "any", "inventory tool");
  problems.allowConsole(/.*/, "inventory tool");
  problems.all.splice(0, problems.all.length);
});
