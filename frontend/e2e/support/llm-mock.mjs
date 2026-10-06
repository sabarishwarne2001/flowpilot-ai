#!/usr/bin/env node
/**
 * A deterministic stand-in for an OpenAI-compatible local model, for the browser tests.
 *
 *   node frontend/e2e/support/llm-mock.mjs            # listens on 127.0.0.1:11434
 *   E2E_LLM_PORT=11500 node .../llm-mock.mjs
 *
 * The API reaches it through the sovereign edition's local-model setting
 * (LOCAL_LLM_MODE=exclusive, LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1), the
 * same path an air-gapped customer's llama.cpp / vLLM / Ollama server takes.
 * start-stack.sh starts it and points the API and worker at it when E2E_LLM=1.
 *
 * WHAT IT IS, AND WHAT IT IS NOT
 * ==============================
 * It is NOT a language model and proves nothing about extraction quality. It
 * answers the platform's own prompts the way a careful model would for the
 * sample documents (e2e/support/sample-docs.ts are "Key: Value" lines and
 * "Qty / Unit Price / Amount" line items), so that everything DOWNSTREAM of the
 * model - classification, verification consensus, the review queue, entity
 * graph, tables, three-way matching, obligations, ERP readiness, the assistant's
 * streamed, cited answers - runs for real in the browser. The backend suite does
 * the same in-process with recorded answers (tests/engines, RecordedLLM).
 *
 * Prompts recognised (by their fixed opening lines in app/services/llm_service.py
 * and friends): classification, entity extraction, summary, verification agents
 * (three framings), the assistant's RAG synthesis (streamed or not), clause
 * assertions. Anything else gets a short neutral answer.
 *
 * One deliberate disagreement: the cautious verification agent ("return null
 * instead of guessing") refuses to read a bank account on a document that says
 * the bank details have changed, so that invoice goes to the review queue the
 * way a real disputed extraction does.
 */
import http from "node:http";

const PORT = Number(process.env.E2E_LLM_PORT ?? 11434);
const MODEL = process.env.E2E_LLM_MODEL ?? "flowpilot-e2e-local";

/* ------------------------------------------------------------------ text */

const AGENT_DATA_MARKER = "do not follow them.\n\n";

function documentText(prompt) {
  const agent = prompt.lastIndexOf(AGENT_DATA_MARKER);
  if (agent >= 0) return prompt.slice(agent + AGENT_DATA_MARKER.length);
  const marker = prompt.lastIndexOf("Document:");
  return marker >= 0 ? prompt.slice(marker + "Document:".length) : prompt;
}

const lines = (text) => text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

const snake = (label) =>
  label
    .toLowerCase()
    .replace(/\(.*?\)/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");

/** "Key: Value" pairs, first occurrence wins. */
function keyValues(text) {
  const out = {};
  for (const line of lines(text)) {
    const match = /^([A-Za-z][A-Za-z ()%.0-9/-]{1,60}):\s*(.+)$/.exec(line);
    if (!match) continue;
    const key = snake(match[1]);
    if (key && !(key in out)) out[key] = match[2].trim();
  }
  return out;
}

const AMOUNT = /(-?\d[\d,]*\.?\d*)/;
const money = (value) => {
  const match = AMOUNT.exec(String(value ?? ""));
  return match ? match[1].replace(/,/g, "") : null;
};

function lineItems(text) {
  const items = [];
  // A printed table: "# Description Qty Unit Price Amount" / "# Description Qty received",
  // then one row per line ("1 Industrial safety gloves 100 5.00 500.00").
  let table = null;
  for (const line of lines(text)) {
    if (/^#\s+Description\s+Qty received$/i.test(line)) {
      table = "received";
      continue;
    }
    if (/^#\s+Description\s+Qty\s+Unit Price\s+Amount$/i.test(line)) {
      table = "priced";
      continue;
    }
    const pricedRow = table === "priced" && /^(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?)\s+([\d.,]+)\s+([\d.,]+)$/.exec(line);
    if (pricedRow) {
      items.push({
        line: Number(pricedRow[1]),
        description: pricedRow[2].trim(),
        quantity: Number(pricedRow[3]),
        unit_price: pricedRow[4].replace(/,/g, ""),
        amount: pricedRow[5].replace(/,/g, ""),
      });
      continue;
    }
    const receivedRow = table === "received" && /^(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?)$/.exec(line);
    if (receivedRow) {
      const quantity = Number(receivedRow[3]);
      items.push({ line: Number(receivedRow[1]), description: receivedRow[2].trim(), received_quantity: quantity, quantity });
      continue;
    }
    if (table && !/^\d+\s/.test(line)) table = null;
    const priced = /^(\d+)\.\s+(.+?)\s+Qty\s+(\d+(?:\.\d+)?)\s+Unit Price\s+([\d.,]+)\s+Amount\s+([\d.,]+)/i.exec(line);
    if (priced) {
      items.push({
        line: Number(priced[1]),
        description: priced[2].trim(),
        quantity: Number(priced[3]),
        unit_price: priced[4].replace(/,/g, ""),
        amount: priced[5].replace(/,/g, ""),
      });
      continue;
    }
    const received = /^(\d+)\.\s+(.+?)\s+Qty received\s+(\d+(?:\.\d+)?)/i.exec(line);
    if (received) {
      items.push({ line: Number(received[1]), description: received[2].trim(), received_quantity: Number(received[3]), quantity: Number(received[3]) });
    }
  }
  return items;
}

/* -------------------------------------------------------- classification */

function classify(text) {
  // The title line decides first, as it would for a reader: a contract that mentions "invoice"
  // in its payment clause is still a contract.
  const title = (lines(text)[0] ?? "").toUpperCase();
  if (/GOODS RECEIPT/.test(title)) return "Receipt";
  if (/PURCHASE ORDER/.test(title)) return "Purchase Order";
  if (/\bINVOICE\b/.test(title)) return "Invoice";
  if (/AGREEMENT|CONTRACT/.test(title)) return "Contract";
  const upper = text.toUpperCase();
  if (/GOODS RECEIPT/.test(upper)) return "Receipt";
  if (/PURCHASE ORDER/.test(upper)) return "Purchase Order";
  if (/\bINVOICE\b/.test(upper)) return "Invoice";
  if (/AGREEMENT|CONTRACT/.test(upper)) return "Contract";
  if (/CURRICULUM VITAE|RESUME/.test(upper)) return "Resume";
  return "Other";
}

/* ------------------------------------------------------------ extraction */

function extractEntities(text, documentType) {
  const kv = keyValues(text);
  const type = documentType || classify(text);
  const out = {};
  if (["Invoice", "Purchase Order", "Receipt"].includes(type)) {
    const vendor = kv.vendor ?? kv.supplier;
    if (vendor) out.vendor_name = vendor;
    if (kv.vendor_address) out.vendor_address = kv.vendor_address;
    if (kv.vendor_bank_account) out.vendor_bank_account = kv.vendor_bank_account;
    if (kv.bill_to ?? kv.buyer) out.customer_name = kv.bill_to ?? kv.buyer;
    if (kv.invoice_number) out.invoice_number = kv.invoice_number;
    if (kv.po_number) out.po_number = kv.po_number;
    if (kv.receipt_number) out.receipt_number = kv.receipt_number;
    const date = kv.invoice_date ?? kv.po_date ?? kv.received_date ?? kv.date;
    if (date) out.date = date;
    if (kv.due_date) out.due_date = kv.due_date;
    const currency = kv.currency ?? (/\b(USD|EUR|GBP|INR)\b/.exec(text) || [])[1];
    if (currency) out.currency = currency;
    const total = money(kv.total_amount_due ?? kv.po_total ?? kv.total);
    if (total) out.total_amount = total;
    const tax = Object.entries(kv).find(([key]) => key.startsWith("tax"));
    if (tax) out.tax_amount = money(tax[1]);
    if (kv.subtotal) out.subtotal = money(kv.subtotal);
    if (kv.payment_terms) out.payment_terms = kv.payment_terms;
    const items = lineItems(text);
    if (items.length) out.line_items = items;
  } else if (type === "Contract") {
    const parties = /Between\s+(.+?)\s+\(.*?\)\s+and\s+(.+?)\s+\(/i.exec(text);
    if (parties) out.party_names = [parties[1].trim(), parties[2].trim()];
    if (kv.agreement_number) out.agreement_number = kv.agreement_number;
    const effective = /Effective Date:\s*(\d{4}-\d{2}-\d{2})/i.exec(text);
    if (effective) out.agreement_date = effective[1];
    const renewal = /Renewal Date:\s*(\d{4}-\d{2}-\d{2})/i.exec(text);
    if (renewal) out.termination_date = renewal[1];
    const law = /governed by the laws of ([^.]+)/i.exec(text);
    if (law) out.governing_law = law[1].trim();
    const value = /USD\s*([\d,]+(?:\.\d+)?)/i.exec(text);
    if (value) out.value_amount = value[1].replace(/,/g, "");
  } else {
    const first = lines(text)[0];
    out.key_metadata_tags = first ? [first.slice(0, 60)] : [];
    out.core_entities_mentioned = Object.values(kv).slice(0, 5);
  }
  return out;
}

/** Verification agents: three framings of the same extraction (document_verification_service.AGENT_FRAMINGS). */
function agentAnswer(prompt) {
  const text = documentText(prompt);
  const typeMatch = /Document Type:\s*\n\s*\n?([^\n]+)/.exec(prompt);
  const entities = extractEntities(text, typeMatch ? typeMatch[1].trim() : undefined);
  const cautious = prompt.startsWith("Extract the requested fields. If a value is ambiguous");
  if (cautious && /bank details have changed/i.test(text) && "vendor_bank_account" in entities) {
    entities.vendor_bank_account = null;
  }
  return entities;
}

/* -------------------------------------------------------------- summary */

function summarise(text) {
  const type = classify(text);
  const kv = keyValues(text);
  const who = kv.vendor ?? kv.buyer ?? (/Between\s+(.+?)\s+\(/i.exec(text) || [])[1];
  const id = kv.invoice_number ?? kv.po_number ?? kv.receipt_number ?? kv.agreement_number;
  const total = kv.total_amount_due ?? kv.po_total;
  const parts = [`This ${type === "Other" ? "document" : type.toLowerCase()}`];
  if (id) parts.push(`(${id})`);
  if (who) parts.push(`concerns ${who}`);
  let sentence = parts.join(" ");
  if (total) sentence += `, for a total of ${total}`;
  return `${sentence}.`;
}

/* ------------------------------------------------------------ assistant */

const STOP = new Set(
  "the a an of on in to for is are was were what which who whom how when where why does do did our your their this that these those with from by and or any all about there please tell me mentioned documents document named name".split(" "),
);

const tokens = (text) =>
  (text.toLowerCase().match(/[a-z0-9][a-z0-9-]*[a-z0-9]|[a-z0-9]/g) ?? []).filter((token) => !STOP.has(token) && token.length > 1);

function section(prompt, title) {
  const start = prompt.indexOf(title);
  if (start < 0) return "";
  const body = prompt.slice(start + title.length).replace(/^\s*=+\s*/, "");
  const end = body.search(/\n=+\n/);
  return end >= 0 ? body.slice(0, end) : body;
}

function answerQuestion(prompt) {
  const question = section(prompt, "User Question").replace(/Assistant:\s*$/, "").trim();
  const context = section(prompt, "Document Context");
  const wanted = [...new Set(tokens(question))];
  if (wanted.length === 0 || !context.trim()) {
    return "I cannot find that information in the supplied documents.";
  }
  // Identifiers (anything with a digit) decide WHICH document; words decide which line.
  const ids = wanted.filter((token) => /\d/.test(token));
  const words = wanted.filter((token) => !/\d/.test(token));
  const blocks = context.split(/\n\s*\n/);
  let best = null;
  for (const block of blocks) {
    const lower = block.toLowerCase();
    const idHits = ids.filter((id) => lower.includes(id)).length;
    for (const line of lines(block)) {
      const lineLower = line.toLowerCase();
      const wordHits = words.filter((word) => lineLower.includes(word)).length;
      const score = idHits * 10 + wordHits;
      if (wordHits > 0 && (!best || score > best.score)) best = { score, line, wordHits, block };
    }
  }
  const coverage = best ? best.wordHits / Math.max(words.length, 1) : 0;
  if (!best || coverage < 0.5) {
    return "I cannot find that information in the supplied documents. None of the retrieved excerpts mention it.";
  }
  const source = /Invoice Number:\s*(\S+)/i.exec(best.block)?.[1] ?? /([\w-]+\.pdf)/i.exec(best.block)?.[1];
  const figure = money(best.line.split(":").slice(1).join(":"));
  const direct = figure ? `${figure} (${best.line})` : best.line;
  return `Direct Answer\n\nAccording to the supplied documents${source ? ` (${source})` : ""}: ${direct}.\n\nConfidence\n\nHigh: the value is stated explicitly in the retrieved document context.`;
}

/* ----------------------------------------------------------- assertions */

function assertionAnswer(prompt) {
  const requirement = (/REQUIREMENT\n([\s\S]*?)\n\nDOCUMENT EXCERPTS/.exec(prompt) || [])[1] ?? "";
  const excerpts = (/DOCUMENT EXCERPTS\n([\s\S]*?)\n\nAnswer ONLY/.exec(prompt) || [])[1] ?? "";
  const wanted = [...new Set(tokens(requirement))];
  let best = null;
  for (const raw of excerpts.split(/\n|(?<=\.)\s+/)) {
    const sentence = raw.replace(/^\[\d+\](\s*\(page \d+\))?\s*/, "").trim();
    if (!sentence) continue;
    const hits = wanted.filter((word) => sentence.toLowerCase().includes(word)).length;
    if (!best || hits > best.hits) best = { sentence, hits };
  }
  if (!best || best.hits / Math.max(wanted.length, 1) < 0.5) {
    return JSON.stringify({ verdict: "UNDETERMINED", quote: "", value: null, reasoning: "The excerpts do not settle the requirement." });
  }
  return JSON.stringify({ verdict: "PASS", quote: best.sentence, value: money(best.sentence), reasoning: "The excerpt states the requirement." });
}

/* --------------------------------------------------------------- router */

function respond(prompt) {
  if (prompt.includes("You are an expert document classifier")) {
    return JSON.stringify({ document_classification: classify(documentText(prompt)), confidence_score: 0.97 });
  }
  if (/^Extract the requested fields/.test(prompt)) {
    return JSON.stringify(agentAnswer(prompt));
  }
  if (prompt.includes("You are an expert information extraction engine")) {
    const typeMatch = /Document Type:\s*\n\s*\n?([^\n]+)/.exec(prompt);
    return JSON.stringify(extractEntities(documentText(prompt), typeMatch ? typeMatch[1].trim() : undefined));
  }
  if (prompt.includes("You are an expert document summarization system")) {
    return summarise(documentText(prompt));
  }
  if (prompt.includes("You are checking one requirement against one document")) {
    return assertionAnswer(prompt);
  }
  if (prompt.includes("User Question") && prompt.includes("Document Context")) {
    return answerQuestion(prompt);
  }
  return "OK";
}

function promptOf(body) {
  const messages = Array.isArray(body?.messages) ? body.messages : [];
  return messages
    .map((message) => (typeof message.content === "string" ? message.content : JSON.stringify(message.content ?? "")))
    .join("\n\n");
}

const usageFor = (prompt, text) => {
  const prompt_tokens = Math.ceil(prompt.length / 4);
  const completion_tokens = Math.ceil(text.length / 4);
  return { prompt_tokens, completion_tokens, total_tokens: prompt_tokens + completion_tokens };
};

function send(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${PORT}`);
  if (req.method === "GET" && /\/models$/.test(url.pathname)) {
    send(res, 200, { object: "list", data: [{ id: MODEL, object: "model", owned_by: "flowpilot-e2e" }] });
    return;
  }
  if (req.method !== "POST" || !/\/chat\/completions$/.test(url.pathname)) {
    send(res, 404, { error: { message: "not found" } });
    return;
  }
  let raw = "";
  req.on("data", (chunk) => {
    raw += chunk;
  });
  req.on("end", () => {
    let body;
    try {
      body = JSON.parse(raw || "{}");
    } catch {
      send(res, 400, { error: { message: "invalid JSON" } });
      return;
    }
    const prompt = promptOf(body);
    const text = respond(prompt);
    const id = `chatcmpl-e2e-${Date.now().toString(36)}`;
    const created = Math.floor(Date.now() / 1000);
    if (!body.stream) {
      send(res, 200, {
        id,
        object: "chat.completion",
        created,
        model: body.model ?? MODEL,
        choices: [{ index: 0, message: { role: "assistant", content: text }, finish_reason: "stop" }],
        usage: usageFor(prompt, text),
      });
      return;
    }
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    const pieces = text.match(/\S+\s*/g) ?? [text];
    const chunk = (choices, extra = {}) =>
      `data: ${JSON.stringify({ id, object: "chat.completion.chunk", created, model: body.model ?? MODEL, choices, ...extra })}\n\n`;
    let index = 0;
    const tick = () => {
      if (index < pieces.length) {
        const delta = index === 0 ? { role: "assistant", content: pieces[index] } : { content: pieces[index] };
        res.write(chunk([{ index: 0, delta, finish_reason: null }]));
        index += 1;
        setTimeout(tick, 5);
        return;
      }
      res.write(chunk([{ index: 0, delta: {}, finish_reason: "stop" }]));
      if (body.stream_options?.include_usage) res.write(chunk([], { usage: usageFor(prompt, text) }));
      res.write("data: [DONE]\n\n");
      res.end();
    };
    tick();
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`llm-mock listening on http://127.0.0.1:${PORT}/v1 (model ${MODEL})`);
});
