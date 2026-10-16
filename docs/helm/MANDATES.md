# Helm's mandates, in plain words

A **mandate** is an outcome or a limit the company states once, in words, and Helm keeps. Each one is
an instrument on the cockpit (holding, drifting, breached, or unknown when Helm cannot see enough),
proposes actions in the plan, and, when it is a **limit**, is checked by the Guard before every
action, so an action that would break it never passes.

Every mandate starts in **Propose** mode: Helm proposes, a person approves. **Act** mode runs actions
by itself, but only up to the **act cap** (₹0 until an owner or admin sets it) and only when the
Guard passes. **Watch** mode only shows the instrument. Editing a mandate makes a new version; plans
and certificates record the version they used. Nothing in Helm moves money: a "payment" is a line on
the payment run that a person pays from their bank.

The library (Business and Enterprise; Helm preview shows them read-only):

| # | Mandate | Kind | What Helm does | Settings (defaults) |
|---|---|---|---|---|
| 1 | **Never pay above the agreed price** | limit | Holds an invoice billed above its purchase order's price and drafts a dispute with the evidence (the PO, the price, the difference). The Guard refuses paying it. | Tolerance 0 %; also allow matching's published price tolerance (on). |
| 2 | **Never pay a duplicate** | limit | A probable duplicate (the same supplier and number, the same file, near-identical text) is held until a reviewer clears it. | Similar-looking invoices count too (off: similarity is evidence, not proof). |
| 3 | **Never pay a changed bank account until verified** | limit | An invoice whose bank account differs from the supplier's usual one is held, and a phone-verification task is opened. | Open the task (on). |
| 4 | **Every invoice above an amount has a matching PO** | limit | Above the amount, an invoice without a matched purchase order is held and a review task opened. | The amount (the owner sets it; the gallery's ₹50,000 is only an example). |
| 5 | **Pay every supplier on time** | goal | Schedules each payable on a payment-run day by its due date. | Hard deadline (off); grace days (0). |
| 6 | **Capture early-payment discounts** | goal | Pays early when the discount beats the cost of money, while every other mandate holds. | Minimum annual rate 18 %. |
| 7 | **Keep cash above a floor** | set point | A floor the plan respects in one currency; 1,000 simulated futures say how likely it holds. | The floor (the owner sets it); 90 % likelihood. |
| 8 | **Pay micro and small suppliers within the legal limit** (India) | limit | The MSMED Act (section 15) and income-tax section 43B(h) deadline is a hard deadline in the plan. | Buffer days (0). |
| 9 | **Decide every renewal before its notice deadline** | limit | A decision task (renew, cancel, renegotiate) before each contract's notice deadline. | 30 days before; the contract owner. |
| 10 | **Supplier compliance documents never expire** | limit | Asks suppliers for renewed insurance, licences and certificates before they expire, through a secure upload link. | 30 days before; every 7 days; at most 3 requests. |
| 11 | **Chase overdue customers** | goal | Reminders 3, 10 and 20 days after the due date, more firmly each time, until the payment arrives. | The days. |
| 12 | **Review backlog under N days** | set point | Tells owners and admins before review items wait too long (one notice per workspace per day). | 5 days; warn 1 day before. |
| 13 | **Reconcile every supplier statement each month** (owner addition B) | goal | Compares each supplier's statement of account with the ledger: invoices we do not have, amounts that differ, invoices they left out, payments and credit notes they have not applied, credits we cannot place, and the balance. Drafts one reconciliation email per statement that differs. | Rounding 1 unit of the currency; statements from the last 45 days. |
| — | **Custom condition** (Enterprise) | limit | The owner's own rule: all or any of up to eight conditions over the invoice's facts (amount, supplier, days to due, PO matched, MSME, facts checked, bank account changed, …), and what to do: hold, ask a person before paying, or notify. | Built in the mandate editor. |

## What every action carries

- **The Guard's checks**, each pass, fail or needs approval, with its evidence: in the mandate's
  scope, every limit, the facts verified (or calibrated), no open conflicts, no fraud or duplicate
  flag, cash above the floor, the act cap, the destination allowed (for emails and webhooks), not
  already run, the account in good standing.
- **A certificate**: the action, the facts it relied on, the mandate versions in force and every
  check, with its SHA-256 fingerprint. Certificates are never changed.
- **An audit row** and, for holds, schedules, payments recorded and tasks, an **undo**. An email that
  has left cannot be undone, and the screen says so.

## Honesty

- Value held is counted only when it happened, once, with its evidence: an overcharge credited, a
  discount taken, a late fee avoided. A replay (the Time machine) labels each amount confirmed,
  likely or potential and never adds them up as if certain.
- A "paid" that rests on a bank line names the line; one a person recorded names the person.
- Where Helm cannot see enough (no cash figure, no statements, facts not read), the instrument says
  unknown, and the visibility meter shows how much of the business Helm can see.
