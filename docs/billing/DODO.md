# Dodo Payments in Servabo

Dodo Payments is Servabo's payment gateway and **merchant of record**: Dodo is the legal seller,
charges the customer, adds and files sales tax / VAT / GST, issues the tax invoice and appears on the
card statement. Servabo records what Dodo did and decides what the customer may use.

This page lists every Dodo endpoint, field and event Servabo uses, where each is documented, and
which of them have **not yet been confirmed against a real Dodo test account** (everything here was
built and tested against the stand-in `backend/tests/fakes/fake_dodo.py`, written from Dodo's
documentation). Written in the website and payments session (C14), 2026-10-17.

Code: `backend/app/services/billing/dodo_gateway.py` (the API calls and the webhook signature),
`dodo_reconcile_service.py` (what each event changes), `dodo_invoice_service.py`,
`plan_change_service.py`, `hold_notice_service.py`, `dispute_service.py`.
Setup check: `backend/scripts/dodo_setup.py` (below).

## 1. Hosts and keys

| Setting | Meaning |
| --- | --- |
| `BILLING_GATEWAY=DODO` | Dodo is the gateway (the default since this session). |
| `DODO_API_KEY` | Bearer key, sent as `Authorization: Bearer …`. |
| `DODO_WEBHOOK_SECRET` | The webhook signing secret (`whsec_…`), from Dodo's dashboard. |
| `DODO_LIVEMODE` | `false`: test host `https://test.dodopayments.com`; `true`: `https://live.dodopayments.com`. |
| `DODO_API_BASE` | Leave empty: it follows `DODO_LIVEMODE`. A mismatch (live host with test mode, or the reverse) refuses to start. |

Docs: <https://docs.dodopayments.com/api-reference/introduction>

## 2. API calls

All money is in the currency's smallest unit (cents for USD). Servabo stores micros: cents × 10,000.

| Call | When | Request fields sent | Response fields read | Docs |
| --- | --- | --- | --- | --- |
| `POST /checkouts` | Owner picks a paid plan or add-on | `product_cart[{product_id, quantity}]` (quantity = seats), `return_url`, `metadata{organization_id, quota_tier_key, client_reference_id}`, `customer{customer_id}` or `customer{email}`, `discount_code` | `checkout_url`, `session_id` | <https://docs.dodopayments.com/developer-resources/checkout-session> |
| `GET /subscriptions/{id}` | Every subscription event (never trust the event body, D-9) | — | `subscription_id`, `status`, `customer.customer_id`, `customer.email`, `product_id`, `quantity`, `previous_billing_date`, `next_billing_date`, `cancel_at_next_billing_date`, `cancelled_at`, `currency`, `metadata` | <https://docs.dodopayments.com/api-reference/subscriptions/get-subscriptions> |
| `POST /subscriptions/{id}/change-plan/preview` | Showing the price of a plan or seat change | `product_id`, `quantity`, `proration_billing_mode` | `immediate_charge.summary.total_amount`, `immediate_charge.summary.tax`, `immediate_charge.summary.currency`, `new_plan.next_billing_date` | <https://docs.dodopayments.com/api-reference/subscriptions/preview-change-plan> |
| `POST /subscriptions/{id}/change-plan` | Upgrade (now), seats, scheduled downgrade (at renewal) | `product_id`, `quantity`, `proration_billing_mode` (`prorated_immediately` for upgrades and seats, `do_not_bill` for downgrades), `on_payment_failure` (`prevent_change` for upgrades, `apply_change` for a due downgrade) | none (an empty body is accepted); the subscription is re-fetched | <https://docs.dodopayments.com/api-reference/subscriptions/change-plan>, <https://docs.dodopayments.com/developer-resources/subscription-upgrade-downgrade> |
| `GET /payments/{id}` | Every payment, refund and dispute event | — | `payment_id`, `status`, `subscription_id`, `customer.customer_id`, `currency`, `total_amount`, `tax`, `created_at`, `refunds[{amount, status}]`, `disputes[{dispute_id, dispute_status, amount}]` | <https://docs.dodopayments.com/api-reference/payments/get-payments-1> |
| `GET /invoices/payments/{payment_id}` | Customer clicks "Download invoice" | — | the PDF bytes | <https://docs.dodopayments.com/api-reference/payments/get-invoice> |
| `PATCH /customers/{id}` | Owner changes billing email or company name | `email`, `name` | the customer | <https://docs.dodopayments.com/api-reference/customers/patch-customer> |
| `POST /customers/{id}/customer-portal/session` | "Manage payment method" / "Update payment method" | `return_url` | `link` | <https://docs.dodopayments.com/api-reference/customers/create-customer-portal-session> |
| `GET /products`, `GET /products/{id}`, `POST /products` | `scripts/dodo_setup.py` only | `name`, `description`, `tax_category`, `price{type, price, currency, discount, purchasing_power_parity, tax_inclusive, payment_frequency_count, payment_frequency_interval, subscription_period_count, subscription_period_interval}` | `items[]`, `product_id`, `is_recurring`, `tax_category`, `price{…}` | <https://docs.dodopayments.com/api-reference/products/post-products> |

Cancellation is done by the customer in Dodo's customer portal; Servabo learns of it from the events.

## 3. Webhooks

Signed as **Standard Webhooks**: headers `webhook-id`, `webhook-timestamp`, `webhook-signature`
(`v1,<base64 HMAC-SHA256 of "id.timestamp.body">`, several may be listed). Servabo verifies the
signature and a timestamp window (`DODO_WEBHOOK_TOLERANCE_SECONDS`, 30 minutes), stores each
`webhook-id` once (a replay is answered 200 and not stored again) and answers at once; the
`--loop billing` worker reconciles later. Body: `{business_id, type, timestamp, data{payload_type, …}}`.
Docs: <https://docs.dodopayments.com/developer-resources/webhooks>,
<https://docs.dodopayments.com/developer-resources/webhooks/intents/webhook-events-guide>

Endpoint to register in Dodo's dashboard: `https://<api host>/api/v1/billing/webhooks/dodo`.

| Event | What Servabo does |
| --- | --- |
| `subscription.active`, `subscription.renewed`, `subscription.updated`, `subscription.plan_changed` | Re-fetch the subscription; record plan, seats, period, cancellation at period end. The plan is the one whose product Dodo bills (`product_id`); the checkout's `quota_tier_key` metadata is only a fallback. |
| `subscription.on_hold` | Renewal declined: `past_due` with a grace window (`BILLING_ON_HOLD_GRACE_DAYS`, 13). Owner told "Payment failed"; the hourly `billing.hold_notices` job sends a reminder 7 days before, a final notice 2 days before, and "read-only" when the window closes. |
| `subscription.failed` | Payment at checkout declined: nothing granted. |
| `subscription.cancelled`, `subscription.expired` | Subscription ended: back to Free (or read-only if over Free's limits). |
| `payment.succeeded` | Re-fetch the payment; record an invoice (issued by Dodo, downloadable); clear a "payment reversed" hold. |
| `payment.failed` | Re-fetch the subscription (the hold is driven by its status). |
| `payment.processing`, `payment.cancelled` | Ignored (no state to record). |
| `refund.succeeded`, `refund.failed` | Re-fetch the payment; record the refunded total on its invoice; tell owners and billing members; RevOps counts it. |
| `dispute.opened`, `.challenged`, `.accepted`, `.cancelled`, `.expired`, `.won`, `.lost` | Record the dispute; alert the operator; a lost dispute (bank reversed the payment) makes the organization read-only until a later payment succeeds or the operator lifts it. Docs: <https://docs.dodopayments.com/developer-resources/webhooks/intents/dispute> |

Any other event type is stored and marked ignored.

## 4. Return from checkout

Dodo sends the customer back to `return_url` with `subscription_id` and `status` added to the query
string. Servabo treats them as a hint only: the return page waits (up to a minute) for the
subscription's webhook to be reconciled before saying "Payment confirmed".

## 5. Fees (for the margin console and COSTS.md)

4% + $0.40 per payment, +1.5% for international cards, +0.5% for subscriptions; $30 per chargeback
(`app/core/payment_fees.py`). Docs: <https://docs.dodopayments.com/miscellaneous/pricing-and-fee-structure>

## 6. Not yet confirmed against a real Dodo test account

Built from the documentation and proven only against the stand-in. Run these once in Dodo's test
mode before going live (RUNBOOK §9 has the steps):

1. `GET /payments/{id}` includes `subscription_id`, `refunds[]` and `disputes[]` in the shapes above.
2. `change-plan` answers with an empty body or the subscription (both are accepted);
   `proration_billing_mode=do_not_bill` is accepted (it appears in Dodo's guide but not in every
   API reference page); `on_payment_failure` is accepted.
3. The preview's `immediate_charge.summary.total_amount` is the amount actually charged.
4. Dodo keeps the checkout's `metadata` after a plan change (Servabo no longer depends on it either way).
5. The return URL carries `subscription_id` and `status`.
6. `GET /products` takes `page_size` / `page_number` and answers `{items: […]}`;
   `GET /products/{id}` carries the full `price` object.
7. A live key and a test key cannot be told apart from the key itself (Dodo documents only a
   placeholder); Servabo relies on `DODO_LIVEMODE` and the host instead.
8. Dodo's payment-retry ("recovery") window for an on-hold subscription; Servabo's own grace window
   is 13 days and does not depend on it.
9. The webhook timestamp format and that no `livemode` flag is sent in the body.
10. The fee figures above (from Dodo's pricing page and secondary sources).

## 7. Historical names in the database

The database kept its table and column names when Dodo became the gateway; renaming them would be a
risky migration for no customer benefit. They hold Dodo data too:

| Name | Holds |
| --- | --- |
| table `stripe_inbound_events` | every gateway's received webhooks; `gateway` = `DODO` or `STRIPE`, Dodo's `webhook-id` in `gateway_event_id` |
| `billing_accounts.stripe_customer_id` | Stripe only; Dodo's customer id is in `gateway_customer_id` |
| `subscriptions.stripe_subscription_id`, `stripe_state_version` | Stripe only / the re-fetch ordering stamp for both gateways; Dodo's id is in `gateway_subscription_id` |
| `invoices.stripe_invoice_id` | Stripe only; a Dodo invoice's payment id is in `gateway_invoice_id` |
| worker loop `--loop stripe`, service `worker-stripe` | old names of `--loop billing` / `worker-billing`, accepted for one release |

## 8. Checking a test account: `scripts/dodo_setup.py`

```
cd backend
python scripts/dodo_setup.py --create-test-products   # creates the 5 plan products and 2 add-ons, prints env lines
python scripts/dodo_setup.py                          # checks every configured id
```

It refuses to run with `DODO_LIVEMODE=true` or the live host. The check reports each product as OK,
NOT SET or WRONG (missing, one-time price, wrong price, currency or interval, tax included in the
price, another tax category). Plan products are priced per seat (the checkout quantity is the number
of seats); add-ons are one per organization.
