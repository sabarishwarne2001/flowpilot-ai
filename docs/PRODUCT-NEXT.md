# What to build next (campaign session 4, §E)

A short list, best first, of ideas that build on what FlowPilot already has. Each says what it is
worth, what it costs to build, and what it costs to run. **None of them is built.** At launch,
stability earns more than novelty: build one only after the launch checklist (HANDOFF "Owner launch
checklist") is done and the first customers are happy.

Build cost is in focused engineering days for one person who knows the code. Running cost is what
it adds to the monthly bill (COSTS.md has the baseline).

## 1. Email-in: forward invoices to a workspace address

**What:** each workspace gets an address (for example `ap-acme@in.flowpilot.ai`). Suppliers'
invoices forwarded there become documents, exactly as if uploaded, with the sender kept as their
source.

**Why first:** accounts-payable teams live in a shared inbox. Today someone must download each
attachment and upload it again; every competitor in invoice capture offers this, and it is the
feature most likely to turn a trial into daily use. Nothing in FlowPilot receives mail yet.

**Build:** about 5 days. Postmark's inbound webhook (FlowPilot already sends through Postmark,
N-027) posts each message as JSON; a new public route checks Postmark's signature, finds the
workspace by address, refuses senders the workspace has not allowed, and hands each attachment to
the existing upload path (`document_intake_service`), so plan limits, file checks and processing
all apply unchanged.

**Run:** Postmark inbound is included in the same plan; documents cost what uploads cost.

## 2. Final overage invoice when a subscription is cancelled

**What:** overage is billed in arrears on the next renewal (N-071). A customer who cancels at the
end of a month with overage has no next renewal, so that last month's overage is never charged.
Close the period on cancellation and send one last invoice for it.

**Why:** revenue that is currently given away, in exactly the case (a heavy user leaving) where it
is largest. Small and low risk: the period-close code (`period_close_service`) already exists.

**Build:** about 2 days: call the period close when the reconcile code applies a cancellation, with
a one-off Stripe invoice instead of the renewal draft, plus tests with the fake gateway.

**Run:** none.

## 3. Yearly billing switch on the plan cards

**What:** Developer $490 and Business $2,990 per seat per year, about two months free (N-051).

**Why:** yearly customers pay up front and churn far less. The price book, the checkout and the
price-book interval selector already support a yearly price (`ARCH50-S1:checkout-interval`); only the
Stripe prices and their ids are missing.

**Build:** half a day in the code (the owner creates two yearly prices in Stripe and sets their ids;
RUNBOOK §9.3), then one browser test.

**Run:** none.

## 4. Error tracking the owner can read

**What:** a self-hosted GlitchTip (Sentry-compatible, free) or Sentry's free tier, wired to the API,
the workers and the web app.

**Why:** the operator alerts (RUNBOOK §9.6) say *that* something failed; an error tracker shows
*which line, for whom, how often*, and groups repeats. It shortens every support conversation.

**Build:** about 1 day: the SDKs, scrubbing of personal data from what is sent, and a setting to
turn it off. **Run:** free tier, or about 1 GB of memory if self-hosted on the same server.

## 5. Accounting-system connections that post bills for the customer

**What:** FlowPilot already prepares ERP postings and delivers them to a configured target
(`erp.deliver_posting`, proven against the in-repo mock target). Ship ready-made connections for
Xero and QuickBooks Online with their sign-in (OAuth) flow, so a small business clicks "Connect" instead
of configuring an endpoint.

**Why:** closes the loop from "invoice received" to "bill entered", which is what customers pay
for; a strong reason to choose Business over Developer.

**Build:** about 10 days for both (OAuth, token refresh, each system's bill format, sandbox tests).
**Run:** none beyond API calls; both have free developer sandboxes.

## 6. Archive (zip) upload

**What:** upload one `.zip` of scanned documents and get each file as its own document.

**Why:** the first-run experience for a customer with a backlog; the handler already exists
(`batch.expand_archive`, dormant, with zip-bomb limits) and only the upload path and screen are
missing.

**Build:** about 2 days. **Run:** none.

## 7. Premium models inside the plans, counted as credits

**What:** let plans include Gemini 2.5 Pro and other dearer models by counting one of their tokens as
several of the default model's (N-054, "reversible later").

**Why:** customers who want the strongest model today must bring their own key (BYOK). Credits let a
Business customer use it without a second vendor account.

**Build:** about 5 days: a credit unit, every usage screen and limit email in credits, migration of
the allowances. **Run:** the dearer model's tokens; priced at twice cost or more like the rest.

## 8. Industry benchmarks from many customers' documents

**What:** "your supplier prices rose 8% this year; similar companies saw 3%", from anonymized,
aggregated TruthMesh and extraction data across organizations that opt in.

**Why:** a differentiator nobody with a single customer's data can copy.

**Build:** about 15 days, most of it privacy work: opt-in, aggregation thresholds so no customer
can be singled out, and the owner's legal review. **Run:** a nightly aggregation job, small.
Only worth doing after there are enough customers for the numbers to mean something.

## 9. Correct an extracted field beside the page

**What:** click a value in the document viewer, see where it was read on the page, type the right
value and save, without opening the review queue (N-020 item 7, dropped for launch).

**Why:** the fastest fix for one wrong field. Today a correction goes through the review queue, which
records who changed what; this would record the same, from the viewer.

**Build:** about 4 days: the viewer already draws each field's box (F-092); the save reuses the
review's resolve rules (who may change a value, the audit entry, re-running automations).

---

**Recommendation:** build 2 and 3 first. Together they take about three days, earn money from the
first customers, and touch only code that is already tested. Then 1, the biggest product win.
