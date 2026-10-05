/**
 * Deterministic sample documents for the browser tests.
 *
 * The repository has no invoice, purchase order or receipt PDFs outside the
 * off-limits evidence folders, so the tests build their own: small, real PDF
 * files with a text layer (so `ML_STUBS=true` still extracts real text; only
 * images and scanned pages get stub OCR). The bytes are identical on every
 * run (no creation date), which keeps the seed idempotent and lets the
 * duplicate-detection checks rely on identical content.
 *
 * Two real files from the repository are used as-is: the PNG logo in
 * frontend/public and a policy PDF from backend/evaluation/corpus.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const FIXTURE_DIR = path.resolve(HERE, "..", ".fixtures");
export const REPO_ROOT = path.resolve(HERE, "..", "..", "..");

type Page = readonly string[];

function escapePdfText(text: string): string {
  return text.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}

/** Build a minimal, valid multi-page PDF with Helvetica text lines. */
export function buildPdf(pages: readonly Page[]): Buffer {
  const objects: string[] = [];
  // 1: catalog, 2: pages, 3: font, then per page: page object + content stream
  const pageObjectIds: number[] = [];
  const bodies: string[] = [];
  let nextId = 4;
  for (const lines of pages) {
    const pageId = nextId++;
    const contentId = nextId++;
    pageObjectIds.push(pageId);
    const ops: string[] = ["BT", "/F1 11 Tf", "14 TL", "56 780 Td"];
    for (const line of lines) {
      ops.push(`(${escapePdfText(line)}) Tj`, "T*");
    }
    ops.push("ET");
    const stream = ops.join("\n");
    bodies[pageId] =
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] ` +
      `/Resources << /Font << /F1 3 0 R >> >> /Contents ${contentId} 0 R >>`;
    bodies[contentId] = `<< /Length ${Buffer.byteLength(stream, "latin1")} >>\nstream\n${stream}\nendstream`;
  }
  bodies[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  bodies[2] = `<< /Type /Pages /Kids [${pageObjectIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pageObjectIds.length} >>`;
  bodies[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>";

  let out = "%PDF-1.4\n%\xE2\xE3\xCF\xD3\n";
  const offsets: number[] = [];
  for (let id = 1; id < nextId; id++) {
    offsets[id] = Buffer.byteLength(out, "latin1");
    objects.push(`${id} 0 obj\n${bodies[id]}\nendobj\n`);
    out += objects[objects.length - 1];
  }
  const xrefOffset = Buffer.byteLength(out, "latin1");
  out += `xref\n0 ${nextId}\n0000000000 65535 f \n`;
  for (let id = 1; id < nextId; id++) {
    out += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  }
  out += `trailer\n<< /Size ${nextId} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

const INVOICE_1001: Page = [
  "INVOICE",
  "Vendor: Acme Industrial Supplies Ltd",
  "Vendor Address: 12 Foundry Road, Leeds LS1 4AB, United Kingdom",
  "Vendor Bank Account: GB29 NWBK 6016 1331 9268 19",
  "Bill To: Caretakers Global Inc",
  "Invoice Number: INV-E2E-1001",
  "Invoice Date: 2026-09-15",
  "Due Date: 2026-10-15",
  "PO Number: PO-E2E-5001",
  "Currency: USD",
  "Line items:",
  "1. Industrial safety gloves  Qty 100  Unit Price 5.00  Amount 500.00",
  "2. Steel toe boots  Qty 10  Unit Price 60.00  Amount 600.00",
  "Subtotal: 1100.00",
  "Tax (VAT 13.64%): 150.00",
  "Total Amount Due: 1250.00 USD",
  "Payment terms: Net 30",
];

const INVOICE_1002_CHANGED_BANK: Page = [
  "INVOICE",
  "Vendor: Acme Industrial Supplies Ltd",
  "Vendor Address: 12 Foundry Road, Leeds LS1 4AB, United Kingdom",
  "Vendor Bank Account: GB94 BARC 1020 1530 0934 59",
  "Bill To: Caretakers Global Inc",
  "Invoice Number: INV-E2E-1002",
  "Invoice Date: 2026-09-20",
  "Due Date: 2026-10-20",
  "PO Number: PO-E2E-5001",
  "Currency: USD",
  "Line items:",
  "1. Industrial safety gloves  Qty 100  Unit Price 5.00  Amount 500.00",
  "2. Steel toe boots  Qty 10  Unit Price 60.00  Amount 600.00",
  "Subtotal: 1100.00",
  "Tax (VAT 13.64%): 150.00",
  "Total Amount Due: 1250.00 USD",
  "Note: our bank details have changed, please pay the new account.",
];

const PURCHASE_ORDER_5001: Page = [
  "PURCHASE ORDER",
  "Buyer: Caretakers Global Inc",
  "Vendor: Acme Industrial Supplies Ltd",
  "PO Number: PO-E2E-5001",
  "PO Date: 2026-09-01",
  "Currency: USD",
  "1. Industrial safety gloves  Qty 100  Unit Price 5.00  Amount 500.00",
  "2. Steel toe boots  Qty 10  Unit Price 60.00  Amount 600.00",
  "PO Total: 1100.00 USD",
];

const GOODS_RECEIPT_7001: Page = [
  "GOODS RECEIPT NOTE",
  "Receiver: Caretakers Global Inc warehouse 3",
  "Vendor: Acme Industrial Supplies Ltd",
  "Receipt Number: GR-E2E-7001",
  "PO Number: PO-E2E-5001",
  "Received Date: 2026-09-12",
  "1. Industrial safety gloves  Qty received 98",
  "2. Steel toe boots  Qty received 10",
];

const CONTRACT_MSA: Page = [
  "MASTER SERVICES AGREEMENT",
  "Agreement Number: MSA-E2E-2026",
  "Between Caretakers Global Inc (Customer) and Acme Industrial Supplies Ltd (Supplier)",
  "Effective Date: 2026-01-01. Term: 12 months. Renewal Date: 2026-12-31.",
  "1. Payment. Customer shall pay each undisputed invoice within 30 days of receipt.",
  "2. Reporting. Supplier shall deliver a monthly service report within 10 days",
  "   after the end of each calendar month.",
  "3. Insurance. Supplier shall maintain liability insurance of at least USD 1,000,000.",
  "4. Termination. Either party may terminate with 60 days written notice before 2026-11-01.",
  "5. Liability cap. Supplier liability is limited to 12 months of fees.",
  "6. Governing law. This agreement is governed by the laws of England and Wales.",
  "7. Confidentiality. Each party shall keep the other party's information confidential.",
];

const PACKET_PAGES: readonly Page[] = [
  INVOICE_1001,
  PURCHASE_ORDER_5001,
  GOODS_RECEIPT_7001,
  CONTRACT_MSA,
];

const TABLE_CSV = [
  "line,description,quantity,unit_price,amount",
  "1,Industrial safety gloves,100,5.00,500.00",
  "2,Steel toe boots,10,60.00,600.00",
  "",
].join("\n");

export const SAMPLE = {
  invoice1001: "invoice-INV-E2E-1001.pdf",
  invoice1002: "invoice-INV-E2E-1002.pdf",
  purchaseOrder: "purchase-order-PO-E2E-5001.pdf",
  goodsReceipt: "goods-receipt-GR-E2E-7001.pdf",
  contract: "contract-MSA-E2E-2026.pdf",
  packet: "scanned-packet-E2E.pdf",
  tableCsv: "invoice-lines-E2E.csv",
  logoPng: "flowpilot-logo.png",
  policyPdf: "Procurement_Policy.pdf",
} as const;

export type SampleName = keyof typeof SAMPLE;

export function samplePath(name: SampleName): string {
  return path.join(FIXTURE_DIR, SAMPLE[name]);
}

/** Write every sample into e2e/.fixtures. Safe to call many times. */
export function writeSampleDocuments(): void {
  fs.mkdirSync(FIXTURE_DIR, { recursive: true });
  const files: Record<string, Buffer> = {
    [SAMPLE.invoice1001]: buildPdf([INVOICE_1001]),
    [SAMPLE.invoice1002]: buildPdf([INVOICE_1002_CHANGED_BANK]),
    [SAMPLE.purchaseOrder]: buildPdf([PURCHASE_ORDER_5001]),
    [SAMPLE.goodsReceipt]: buildPdf([GOODS_RECEIPT_7001]),
    [SAMPLE.contract]: buildPdf([CONTRACT_MSA]),
    [SAMPLE.packet]: buildPdf(PACKET_PAGES),
    [SAMPLE.tableCsv]: Buffer.from(TABLE_CSV, "utf8"),
    [SAMPLE.logoPng]: fs.readFileSync(path.join(REPO_ROOT, "frontend", "public", "flowpilot-logo.png")),
    [SAMPLE.policyPdf]: fs.readFileSync(
      path.join(REPO_ROOT, "backend", "evaluation", "corpus", "Procurement_Policy.pdf"),
    ),
  };
  for (const [name, bytes] of Object.entries(files)) {
    const target = path.join(FIXTURE_DIR, name);
    if (!fs.existsSync(target) || !fs.readFileSync(target).equals(bytes)) {
      fs.writeFileSync(target, bytes);
    }
  }
}
