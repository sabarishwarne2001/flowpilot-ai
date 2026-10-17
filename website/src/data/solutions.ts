/**
 * The solution pages: honest use cases. Each names the engines it uses (by id from engines.json,
 * so the plan badges come from the price list) and says plainly what Servabo does not do.
 */
export interface Solution {
  slug: string;
  nav: string;
  title: string;
  description: string;
  who: string;
  problem: string[];
  steps: { title: string; body: string }[];
  engines: string[];
  notThis: string[];
}

export const SOLUTIONS: Solution[] = [
  {
    slug: "accounts-payable",
    nav: "Finance & accounts payable",
    title: "Pay what you owe, once, at the agreed price.",
    description:
      "Servabo for finance and accounts payable teams: invoices read and matched to orders and receipts, duplicates and changed bank details held, and a payment plan that keeps your rules.",
    who: "Finance teams and accounting firms that process supplier invoices every week.",
    problem: [
      "An accounts payable team checks each invoice against an order and a delivery note by eye, keys its fields into the books, and builds the payment run in a spreadsheet. The mistakes that cost money are the quiet ones: a price above the order, the same invoice paid twice, a bank account changed on a single invoice, a small supplier paid after the legal deadline.",
      "Servabo does the reading and the checking, and leaves the decisions with your team.",
    ],
    steps: [
      { title: "Invoices in", body: "Upload invoices as they arrive, or let your systems send them through the API. Servabo reads the supplier, number, dates, lines, tax and totals, each with the page it came from." },
      { title: "Matched and checked", body: "Each invoice is matched to its purchase order and goods receipt line by line. Duplicates, changed bank details and unusual amounts are held with the evidence." },
      { title: "A plan you approve", body: "Helm schedules each payable by its due date and your cash floor, takes the early-payment discounts worth taking, and lists the payment run for a person to approve and pay from your bank." },
      { title: "Posted and reconciled", body: "Approved bills post to your ERP exactly once. Import the bank statement and payments are matched back to their invoices; supplier statements are reconciled each month." },
    ],
    engines: ["extraction", "matching", "radar", "helm", "erp", "entities"],
    notThis: [
      "Servabo does not pay suppliers. It plans the payment run; your team pays it from your bank.",
      "It does not replace your accounting system: it posts approved documents into it.",
    ],
  },
  {
    slug: "procurement",
    nav: "Procurement",
    title: "Hold suppliers to the price, quantity and terms you agreed.",
    description:
      "Servabo for procurement teams: orders, receipts and invoices compared line by line, contract prices checked on every invoice, and supplier documents kept current.",
    who: "Procurement and purchasing teams that negotiate prices and need them honoured.",
    problem: [
      "Procurement agrees a price and terms; weeks later finance receives an invoice that does not quite match, and nobody has the order, the receipt and the contract side by side. Insurance certificates and licences expire unnoticed until an audit asks for them.",
    ],
    steps: [
      { title: "Orders and receipts read", body: "Purchase orders and goods receipts are read like invoices, so quantities and prices are available to compare." },
      { title: "Every invoice compared", body: "Three-way matching compares each invoice line with what was ordered and received, within the tolerances you set, and opens one case per invoice for a person to approve or dispute." },
      { title: "Contracts checked against invoices", body: "TruthMesh finds documents that disagree across the workspace, such as a contract price that no invoice honours." },
      { title: "Supplier documents kept current", body: "Obligations track certificate and licence expiries, and Helm can ask suppliers for renewed documents through a secure upload link before they lapse." },
    ],
    engines: ["matching", "truthmesh", "entities", "obligations", "helm", "cases"],
    notThis: [
      "Servabo does not run supplier sourcing, tenders or catalogues.",
      "It compares the documents you have; it does not negotiate on your behalf.",
    ],
  },
  {
    slug: "legal-compliance",
    nav: "Legal & compliance",
    title: "Know what every contract commits you to, and when.",
    description:
      "Servabo for legal and compliance teams: clause checks on every new contract, versions compared clause by clause, obligations and notice periods tracked, personal data redacted.",
    who: "In-house legal, compliance and contract-management teams.",
    problem: [
      "Contracts arrive in many versions and formats. The clause that matters (a liability cap, an auto-renewal, a notice period) is on page 14, and the date it bites is in someone's calendar, if anywhere. Sharing a document outside the company means redacting it by hand.",
    ],
    steps: [
      { title: "Clauses checked on arrival", body: "Write the rules your contracts must meet as sentences; every contract that arrives is checked against them, with the clause each result rests on." },
      { title: "Versions compared", body: "Compare two to five drafts clause by clause, with every difference in a matrix and a report you can download." },
      { title: "Obligations on a calendar", body: "Renewals, notice periods and payment dates are read into obligations with owners and reminders, and a calendar feed puts them where your team looks." },
      { title: "Redacted and audited", body: "Personal data is cut out of the page itself before a document is shared, and every sensitive action is in an audit log nobody can edit." },
    ],
    engines: ["assertions", "corroborator", "obligations", "redaction", "audit", "assistant"],
    notThis: [
      "Servabo is not a law firm and gives no legal advice. It shows you what your documents say.",
      "Clause checks and comparisons support a lawyer's review; they do not replace it.",
    ],
  },
  {
    slug: "operations",
    nav: "Operations",
    title: "Move documents through your team without losing any.",
    description:
      "Servabo for operations teams: scanned bundles split and read, automations that route work as it arrives, live shared review, and process intelligence that shows where documents wait.",
    who: "Operations and shared-service teams that handle high volumes of paperwork.",
    problem: [
      "Paperwork arrives as scanned bundles and attachments; someone splits it, someone keys it, someone chases the missing page. Nobody can say where a document is waiting or which ones will miss their deadline today.",
    ],
    steps: [
      { title: "Bundles split and read", body: "A scanned bundle is cut into its documents automatically and each one is read; tables that run across pages are rebuilt and checked." },
      { title: "Routed as it arrives", body: "Automations route, tag and notify as documents arrive, and batch operations handle hundreds at once with export packages anyone can verify." },
      { title: "Reviewed together", body: "A live shared review queue shows who is looking at what, with item locks so two people never decide the same item." },
      { title: "Where work waits", body: "Process intelligence shows how documents actually move, predicts which will breach their service level, and proposes fixes for a person to approve." },
    ],
    engines: ["cases", "tables", "automations", "batches", "review", "process"],
    notThis: [
      "Servabo reads documents you give it; it does not scan paper for you.",
      "Process intelligence describes how documents move inside Servabo, not in your other systems.",
    ],
  },
];
