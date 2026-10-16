// Lighthouse on the home page, pricing and Helm: every category at least 95 (performance,
// accessibility, best practices, SEO). Runs Lighthouse's default (mobile) profile against the
// built site served with its real headers, in Playwright's Chromium.
import lighthouse from "lighthouse";
import { launch } from "chrome-launcher";
import { chromium } from "@playwright/test";

import { serve } from "./serve-dist.mjs";

const MIN = Number(process.env.LIGHTHOUSE_MIN ?? 95);
const PAGES = ["/", "/pricing", "/helm"];
const server = await serve(Number(process.env.PORT ?? 4324));
const chrome = await launch({ chromePath: chromium.executablePath(), chromeFlags: ["--headless=new", "--no-sandbox"] });
const failures = [];
try {
  for (const path of PAGES) {
    const result = await lighthouse(server.url + path, {
      port: chrome.port,
      output: "json",
      logLevel: "error",
      onlyCategories: ["performance", "accessibility", "best-practices", "seo"],
    });
    const scores = Object.fromEntries(Object.entries(result.lhr.categories).map(([k, v]) => [k, Math.round((v.score ?? 0) * 100)]));
    console.log(`${path}: ${Object.entries(scores).map(([k, v]) => `${k} ${v}`).join(", ")}`);
    for (const [k, v] of Object.entries(scores)) if (v < MIN) failures.push(`${path}: ${k} ${v} < ${MIN}`);
  }
} finally {
  chrome.kill();
  await server.close();
}
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
