/**
 * Read the mail the backend sent to the local SMTP sink (support/smtp-sink.mjs).
 */
import fs from "node:fs";
import path from "node:path";

import { MAIL_DIR } from "./env";

export interface Mail {
  readonly to: string[];
  readonly from: string;
  readonly receivedAt: string;
  readonly raw: string;
  readonly text: string;
}

/** Undo quoted-printable soft breaks and =XX escapes; good enough for links. */
function decode(raw: string): string {
  return raw
    .replace(/=\r?\n/g, "")
    .replace(/=([0-9A-F]{2})/g, (_m, hex: string) => String.fromCharCode(parseInt(hex, 16)));
}

export function readMails(to: string, since = 0): Mail[] {
  if (!fs.existsSync(MAIL_DIR)) return [];
  return fs
    .readdirSync(MAIL_DIR)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => JSON.parse(fs.readFileSync(path.join(MAIL_DIR, name), "utf8")) as Omit<Mail, "text">)
    .filter((mail) => mail.to.includes(to.toLowerCase()) && Date.parse(mail.receivedAt) >= since)
    .map((mail) => ({ ...mail, text: decode(mail.raw) }));
}

/** Wait for a mail to `to` received after `since` whose text matches `pattern`. */
export async function waitForMail(to: string, pattern: RegExp, since: number, timeoutMs = 45_000): Promise<Mail> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const match = readMails(to, since).reverse().find((mail) => pattern.test(mail.text));
    if (match) return match;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`no mail to ${to} matching ${pattern} within ${timeoutMs} ms (sink: ${MAIL_DIR})`);
}

/** The first link in the mail whose path matches `pathPattern`, made relative to the web app. */
export function linkFrom(mail: Mail, pathPattern: RegExp): string {
  const links = mail.text.match(/https?:\/\/[^\s"'<>]+/g) ?? [];
  const link = links.find((candidate) => pathPattern.test(candidate));
  if (!link) {
    throw new Error(`no link matching ${pathPattern} in mail; links: ${links.slice(0, 5).join(" ")}`);
  }
  const url = new URL(link.replace(/&amp;/g, "&"));
  // Tokens may ride in the fragment (#token=...) so they never reach server logs.
  return `${url.pathname}${url.search}${url.hash}`;
}
