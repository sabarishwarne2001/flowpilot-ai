# Helm — the flagship, for the website and the sales conversation

_Written at the end of the flagship session (2026-10-15) for the website and payments session. Every
claim below is something the product does today and a test proves; where a figure is shown, it comes
from the sample company. Product name: Helm, inside the product renamed Servabo next session._

## The one-line promise

**Helm keeps your business promises.** You write the outcomes and the limits once ("never pay
above the agreed price", "keep cash above ₹10 lakh", "pay micro and small suppliers within 45
days"); Helm reads every promise in your documents, plans what to pay, hold and chase, checks every
step against your rules, and shows you what it held, with the evidence.

## Who it is for (N-083)

Companies that buy from many suppliers (distributors and wholesalers, manufacturers, logistics,
construction, multi-branch retail, restaurants, clinics; 50 to 1,000 people with a small finance
team) and the accounting firms that run payables for them (one workspace per client). India first:
INR, GST invoices, the MSMED Act and income-tax section 43B(h); any currency works.

## What it does, in the customer's words

1. **It reads your promises.** Every invoice, purchase order, goods receipt, credit note, contract,
   certificate and statement your team already uploads becomes a promise: what is owed, by whom, by
   when, at what price. The **Promise ledger** shows each one with the document it came from.
2. **You state the outcomes, in a sentence.** Thirteen ready mandates (the list is in
   `docs/helm/MANDATES.md`), or type a sentence and Helm reads it back as a mandate you confirm. The
   AI only drafts the sentence; deterministic code decides everything else, and Helm works with AI
   switched off.
3. **The cockpit.** One instrument per mandate (holding, drifting, breached, or unknown when Helm
   cannot see enough), the cash flight path for the coming weeks with your floor, and the plan: pay
   this on Tuesday, hold that, chase this customer, reconcile that supplier.
4. **The Guard.** Every action is checked before you see it and again before it runs: your limits,
   the facts checked, no open fraud or duplicate flag, cash above the floor, the act cap. Each
   action gets a **certificate** with every check and its evidence, sealed so it can never change.
5. **You stay in charge.** Everything starts in Propose mode: Helm proposes, a person approves.
   Act mode runs actions by itself only up to an amount the owner sets (₹0 by default). Holds,
   schedules and payments recorded can be undone. Nothing moves money: the payment run is a list
   your team pays from its bank.
6. **Bank statements close the loop** (owner addition A). Import your bank's CSV or Excel export;
   Helm reads it with the column mapping you confirm once per account, takes the closing balance as
   cash, and matches payments to the invoices it scheduled, by amount, date and reference or payee.
   "Paid" and "value held" rest on the bank line. Anything uncertain waits for a person, with the
   reasons in words.
7. **Supplier statements reconciled every month** (owner addition B). Each supplier's statement of
   account is compared with your books: invoices you never received, amounts that differ, payments
   they have not applied. Helm drafts the reconciliation email for you to send.
8. **The Time machine.** Replay the last 6 months (24 on Enterprise) of your own documents as if Helm
   had been on: what it would have held, each amount marked confirmed, likely or potential, never
   added up as if certain. Download it as a PDF for your accountant or board, with a fingerprint
   on every page that matches the one on screen.
9. **What if…** Beside the flight path: "what if customers pay 30 days later", "what if we hold
   these invoices", "what if ₹20 lakh comes in". Helm plans both and shows the difference, on the same
   1,000 futures, and saves nothing.
10. **The guided tour** (owner addition C) walks a first-time visitor through the sample company in
   six steps.
11. **Enterprise controls.** Approval chains: above an amount you choose, a payment needs two or three
   different people (and Helm's own approval never counts). Custom conditions: your own rule from
   up to eight facts of an invoice ("amount above ₹5 lakh and a new supplier"), to hold, to ask a
   person first, or to notify.

## Plans (N-082, unchanged prices)

| | Helm |
|---|---|
| Trial and Developer | **Helm preview**: the sample company, the guided tour, one replay a week of up to 3 months of your own documents |
| Business | Helm: the mandate library, Propose and Act, bank statement import, supplier reconciliation, a 6-month replay, the 90-day forecast, what-if, up to 15 active mandates |
| Enterprise | adds unlimited mandates, planning across all workspaces, a 24-month replay, custom condition mandates, approval chains, and Helm through API keys (`helm:read`, `helm:write`) and webhooks (a new plan, an action that ran, a mandate breached) |

## Proof points (the sample company, Meridian Wholesale Pvt Ltd, an invented Pune distributor)

Nine months of documents read by the same engines as yours. Its replay finds exactly what was
planted: overcharges above purchase-order prices, a duplicate invoice paid twice, a supplier's
changed bank account, MSME payments made late, missed early-payment discounts, a late-fee invoice,
a missed contract renewal, an expired insurance certificate, customers who paid late and a month
when cash dipped below the floor; and Sahyadri Packaging's statement of account with three
differences, explained to the paisa. Use it for screenshots (it is labelled "sample" everywhere and
anyone can delete it).

## What we never claim (N-085)

- Helm never pays: it plans, a person pays.
- "Value held" is only what happened, with its evidence; replays and forecasts are labelled.
- Helm gives recommendations, not financial or tax advice; the MSME mandate links to the law.
- Helm does not guess: where it cannot see enough it says so, and the visibility meter shows how
  much of the business it sees.

## Screenshots to take (sample company, light theme, 1440 px)

Cockpit with the tour on step 1; the plan with a Guard certificate open; the Time machine replay;
Bank statements after an import (one matched by Helm, one suggested); a reconciliation with its
differences and the drafted email; the mandate composer reading a sentence back.

## Identity: Night Bridge (Helm visual session, 2026-10-17)

Helm has its own look, **Night Bridge**, which replaced the brief's "calm instrument panel" (N-111;
the whole system is in `docs/helm/VISUAL.md`): a near-black canvas with a green undertone and a slow
aurora, frosted glass panels, one signal green, rings that encode a value, big tabular figures in Inter,
and a ship's-wheel mark. Light mode ("Day Bridge") is just as finished. Changing mode is a moment: the
switch's thumb glides, and Helm is revealed by a circle growing from the switch. Documents keeps a calm
look of its own, **Lumen** (N-113). For the website and sales: rupees always print in lakh and crore
grouping (₹6,59,440.64; N-112), and every figure in a screenshot is the sample company's real data.

Website screenshots now taken (`website/scripts/capture-screenshots.mjs`, as an Indian reader): the
cockpit, the plan (the payment run), a Guard certificate (`helm-certificate`), the Time machine replay,
bank statements after an import, and a reconciliation with its drafted email.
