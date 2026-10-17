# Dodo Payments merchant review — what to submit and where the reviewer finds it

Dodo Payments checks a merchant's live website before it switches on live payments. Its reviewer
(a person or a crawler) looks for a working product, pricing, terms of service, a privacy policy, a
refund and cancellation policy and a monitored contact route, all reachable without an account, and
for a site that says the same things as the form. Mismatches between the form and the site are a
common reason a review is held.

## What to type on Dodo's form

| Field | Value |
|---|---|
| Account type | Individual (sole proprietor): **Sabarish N**, India |
| Identity | Government ID and a selfie (Persona); bank account in your own name |
| Website URL | `https://servabo.app` (fallback: `https://getservabo.com`) |
| Product category | Software as a service (B2B business software) |
| **Product description** (one sentence) | **Servabo is online software for finance and operations teams that reads invoices, purchase orders, contracts and statements, checks them against each other and plans what to pay, hold and chase, sold as a per-seat monthly or yearly subscription and available immediately after sign-up.** |
| Delivery method | Online software, available immediately after sign-up |
| Pricing | Developer $49, Business $299, Enterprise $799 per seat per month; yearly Developer $490 and Business $2,990 per seat; a 7-day trial with no card needed |
| Refund policy | `https://servabo.app/legal/refunds` |
| Support email | `sabarishwarne@gmail.com` (switch to `support@servabo.app` when forwarding works, on the site and here) |

The same description sentence is `SITE.description` in `website/src/config/site.ts` and appears in
the home page's structured data, so the form and the site say the same thing word for word.

## Every requirement and where it is on the site

| Dodo looks for | URL | Linked from |
|---|---|---|
| What the product is and who it is for | `/`, `/product`, `/helm`, `/solutions/*` | Header and footer of every page |
| Pricing with the billing interval (per seat, per month or year) | `/pricing` | Header, footer, home page |
| Trial terms (7 days, no card) | `/pricing#trial` | Pricing, home page FAQ |
| Taxes and merchant of record ("Prices exclude taxes; Dodo Payments, our merchant of record, calculates and collects tax at checkout") | `/pricing`, `/legal/terms#payment` | Pricing page, every page's footer line "Payments are processed by Dodo Payments, our merchant of record" |
| Terms of Service | `/legal/terms` | Footer of every page |
| Privacy Policy (with a grievance officer, Sabarish N) | `/legal/privacy` | Footer of every page |
| Refund and Cancellation Policy | `/legal/refunds` | Footer of every page, pricing FAQ, the app's Billing page |
| A monitored contact route (email, response time two business days) | `/contact`, and the email in every page's footer | Footer of every page |
| Who operates the business (individual, India) | `/about`, footer copyright line, `/legal/terms#who` | Footer of every page |
| Delivery method ("online software, available immediately after sign-up") | Home page hero, footer copyright line, `/legal/terms#service` | Every page |
| Acceptable use | `/legal/acceptable-use` | Footer |
| Who processes data | `/legal/subprocessors` | Footer, privacy policy |

No page is a placeholder and none says "draft". Each legal page shows its "Last updated" date.

## Dodo's guidance followed

Dodo's documentation (docs.dodopayments.com) could not be opened from the session's sandbox, so its
pages were read through web search results on 2026-10-16. What they say, and what the site does:

- **Required pages, linked from a consistent place such as the footer**: pricing, terms, privacy,
  refund and cancellation, contact (Dodo's verification guide). Every one is in every page's footer.
- **The site must be live, accessible and match the form**: the product description, pricing and
  delivery method above are the site's own words.
- **Dodo is the merchant of record and the legal seller; its name appears on the customer's card
  statement and receipt** (Dodo's "Merchant of Record" introduction). The terms (section 5), the
  pricing page, the refund policy and every footer say so; the terms say the purchase is also
  subject to Dodo's buyer terms, shown at checkout.
- **Assumed** (no Dodo page found that states it): that a sentence naming Dodo as merchant of
  record on the pricing page and in the terms is enough, with no specific wording required. If
  Dodo's reviewer asks for particular wording, it is one line in `website/src/pages/pricing.astro`
  and `website/src/pages/legal/terms.astro`.
- Typical review time: 24 to 72 working hours (Dodo's verification guide); longer if more documents
  are needed. A rejection comes by email with the reason, and you can reapply after fixing it.

## Before you submit

1. The site is live at the URL you give (`curl -sI https://servabo.app` answers `200`).
2. Click every footer link on the live site once.
3. The contact email on the site is one you read every day.
4. The prices on the live pricing page match what you type on the form.
