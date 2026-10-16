// Every page, in light and dark: no serious or critical axe-core finding (WCAG 2.1 A and AA),
// no console error (a CSP violation is one), and no sideways scroll at 360 px.
import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { serve } from "./serve-dist.mjs";
import { pages } from "./pages.mjs";

const server = await serve(Number(process.env.PORT ?? 4323));
const browser = await chromium.launch();
const failures = [];
let runs = 0;
try {
  for (const theme of ["light", "dark"]) {
    for (const width of [360, 1280]) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme });
      await context.addInitScript((t) => window.localStorage.setItem("servabo-site-theme", t), theme);
      for (const path of await pages()) {
        const page = await context.newPage();
        const errors = [];
        page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
        page.on("pageerror", (e) => errors.push(String(e)));
        await page.goto(server.url + path, { waitUntil: "networkidle" });
        const label = `${path} (${theme}, ${width}px)`;
        const dark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
        if (dark !== (theme === "dark")) failures.push(`${label}: the ${theme} theme was not applied`);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        if (overflow > 1) failures.push(`${label}: scrolls sideways by ${overflow}px`);
        if (width === 1280) {
          const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
          for (const v of result.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""))) {
            failures.push(`${label}: ${v.impact} ${v.id} — ${v.help} (${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("; ")})`);
          }
        }
        for (const e of errors) if (!(path === "/this-page-does-not-exist" && /404/.test(e))) failures.push(`${label}: console error: ${e}`);
        runs++;
        await page.close();
      }
      await context.close();
    }
  }
} finally {
  await browser.close();
  await server.close();
}
if (failures.length) {
  console.error(failures.join("\n"));
  console.error(`${failures.length} problem(s)`);
  process.exit(1);
}
console.log(`accessibility: ${runs} page views (light and dark, 360 and 1280 px), no serious or critical finding`);
