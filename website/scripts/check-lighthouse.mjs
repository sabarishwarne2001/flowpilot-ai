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
/** One Lighthouse run. A category without a score means the measurement itself failed (a cold
 *  first load can time out); that is retried, never counted as a low score or as a pass. */
async function measure(path) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const result = await lighthouse(server.url + path, {
      port: chrome.port,
      output: "json",
      logLevel: "error",
      onlyCategories: ["performance", "accessibility", "best-practices", "seo"],
    });
    const categories = Object.entries(result.lhr.categories);
    if (categories.every(([, v]) => typeof v.score === "number")) {
      return Object.fromEntries(categories.map(([k, v]) => [k, Math.round(v.score * 100)]));
    }
    const why = result.lhr.runtimeError?.message ?? "a category came back without a score";
    console.log(`${path}: measurement failed (attempt ${attempt}): ${why}`);
  }
  return null;
}

try {
  await fetch(server.url + "/").catch(() => undefined); // the server's first answer is not the page's speed
  for (const path of PAGES) {
    const scores = await measure(path);
    if (scores === null) {
      failures.push(`${path}: Lighthouse could not measure it in three tries`);
      continue;
    }
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
