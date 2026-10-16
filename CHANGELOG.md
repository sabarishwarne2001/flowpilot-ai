# Changelog

_FlowPilot AI was renamed Servabo on 2026-10-16. Entries below that date keep the old name._

All notable changes to this project will be documented here.

## [Unreleased]

### Changed — FlowPilot AI is now Servabo (2026-10-16)

There are no customers yet, so every protocol identifier that carried the old name changes once,
now. Anything built against the old names (a webhook receiver, a script that holds an API key, a
DNS record) must use the new ones:

| What | Before | After |
|---|---|---|
| Outgoing webhook signature header | `X-FlowPilot-Signature` | `X-Servabo-Signature` |
| Outgoing webhook delivery headers | `X-FlowPilot-Delivery-Id`, `X-FlowPilot-Event-Type`, `X-FlowPilot-Attempt` | `X-Servabo-Delivery-Id`, `X-Servabo-Event-Type`, `X-Servabo-Attempt` |
| Outgoing User-Agent strings | `FlowPilot-Webhooks/1.0`, `FlowPilot-ERP/1`, `FlowPilot-ARCH26/1.0` | `Servabo-Webhooks/1.0`, `Servabo-ERP/1`, `Servabo-ARCH26/1.0` |
| Public API version header | `X-FlowPilot-API-Version` | `X-Servabo-API-Version` |
| Spend-limit and export response headers | `X-FlowPilot-Spend-Limit`, `-Spend-Period`, `-Spend-Resets-At`, `X-FlowPilot-Export-Truncated` | `X-Servabo-Spend-Limit`, `-Spend-Period`, `-Spend-Resets-At`, `X-Servabo-Export-Truncated` |
| Domain-verification TXT record value | `flowpilot-site-verification=…` | `servabo-site-verification=…` |
| Domain-verification TXT record name (subdomain form) | `_flowpilot.<domain>` | `_servabo.<domain>` |
| Custom-domain challenge record | `_flowpilot-challenge.<host>` | `_servabo-challenge.<host>` |
| Live-review WebSocket subprotocol | `flowpilot.review.v1` | `servabo.review.v1` |
| Refresh cookie | `flowpilot_refresh` | `servabo_refresh` (everyone signs in again once) |
| API key prefixes | `fp_live_…` / `fp_test_…` | `svb_live_…` / `svb_test_…` (re-create local keys) |
| Browser storage keys | `flowpilot_auth_session`, `flowpilot_ui_preferences`, `flowpilot_tenant_selection` | `servabo_auth_session`, `servabo_ui_preferences`, `servabo_tenant_selection` (theme and last workspace reset once) |
| Sovereign licence format | `flowpilot-licence/1` | `servabo-licence/1` |

### Planned

- Backend foundation
- Frontend foundation
- PostgreSQL integration
- Authentication
- Work Item processing
- AI pipeline
- Automation Engine
