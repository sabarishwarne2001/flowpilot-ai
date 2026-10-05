#!/usr/bin/env node
/**
 * A tiny SMTP server for the browser tests. It accepts every message and
 * writes it to E2E_MAIL_DIR (default frontend/e2e/.mail) as one JSON file:
 * { to: [...], from, receivedAt, raw }. Nothing is relayed anywhere.
 *
 * The backend sends sign-up verification, password reset and invitation
 * mail through PLATFORM_SMTP_HOST/PORT; the e2e backend .env points those at
 * 127.0.0.1:1025 with PLATFORM_SMTP_ENCRYPTION=NONE, so tests can read the
 * links a real user would click (support/mail.ts).
 */
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const MAIL_DIR = process.env.E2E_MAIL_DIR ?? path.resolve(here, "..", ".mail");
const PORT = Number(process.env.E2E_SMTP_PORT ?? 1025);

fs.mkdirSync(MAIL_DIR, { recursive: true });
let counter = 0;

const server = net.createServer((socket) => {
  socket.setEncoding("utf8");
  let buffer = "";
  let inData = false;
  let from = "";
  let to = [];
  let data = [];
  const reply = (line) => socket.write(`${line}\r\n`);

  reply("220 flowpilot-e2e-smtp ready");

  let authStep = 0; // AUTH LOGIN: 1 = expecting username, 2 = expecting password
  const handleLine = (line) => {
    if (authStep === 1) {
      authStep = 2;
      reply("334 UGFzc3dvcmQ6");
      return;
    }
    if (authStep === 2) {
      authStep = 0;
      reply("235 Authentication successful");
      return;
    }
    if (inData) {
      if (line === ".") {
        inData = false;
        const raw = data.join("\r\n");
        const name = `${Date.now()}-${String(counter++).padStart(5, "0")}.json`;
        fs.writeFileSync(
          path.join(MAIL_DIR, name),
          JSON.stringify({ from, to, receivedAt: new Date().toISOString(), raw }),
        );
        data = [];
        reply("250 OK queued");
        return;
      }
      data.push(line.startsWith("..") ? line.slice(1) : line);
      return;
    }
    const upper = line.toUpperCase();
    if (upper.startsWith("EHLO")) {
      socket.write("250-flowpilot-e2e-smtp\r\n250-8BITMIME\r\n250-AUTH PLAIN LOGIN\r\n250 SMTPUTF8\r\n");
    } else if (upper.startsWith("HELO")) {
      reply("250 flowpilot-e2e-smtp");
    } else if (upper.startsWith("MAIL FROM:")) {
      from = line.slice(10).trim().replace(/^<|>.*$/g, "");
      to = [];
      reply("250 OK");
    } else if (upper.startsWith("RCPT TO:")) {
      to.push(line.slice(8).trim().replace(/^<|>.*$/g, "").toLowerCase());
      reply("250 OK");
    } else if (upper === "DATA") {
      inData = true;
      reply("354 End data with <CR><LF>.<CR><LF>");
    } else if (upper === "RSET") {
      from = "";
      to = [];
      data = [];
      reply("250 OK");
    } else if (upper === "NOOP") {
      reply("250 OK");
    } else if (upper === "QUIT") {
      reply("221 Bye");
      socket.end();
    } else if (upper.startsWith("AUTH LOGIN")) {
      // Any credentials are accepted: this sink only records mail.
      const inline = line.split(" ")[2];
      authStep = inline ? 2 : 1;
      reply(inline ? "334 UGFzc3dvcmQ6" : "334 VXNlcm5hbWU6");
    } else if (upper.startsWith("AUTH")) {
      reply("235 Authentication successful");
    } else {
      reply("250 OK");
    }
  };

  socket.on("data", (chunk) => {
    buffer += chunk;
    let index;
    while ((index = buffer.indexOf("\r\n")) >= 0) {
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      handleLine(line);
    }
  });
  socket.on("error", () => {});
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[smtp-sink] listening on 127.0.0.1:${PORT}, writing to ${MAIL_DIR}`);
});
