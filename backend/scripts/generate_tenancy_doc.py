#!/usr/bin/env python3
"""Campaign session 1 — write docs/hardening/TENANCY-AND-PLANS.md from the code.

    python scripts/generate_tenancy_doc.py          # rewrite the document
    python scripts/generate_tenancy_doc.py --check  # exit 1 when the document is stale

Every table in the document is computed here by calling the code that decides it:
the role tables call `organization_permissions` and `workspace_permissions` for every
role, the plan tables read `seed_quota_tiers.PLACEHOLDER_TIERS` and `COMMERCIALS`
(the rows the deploy publishes), the capability names come from `capability_gate`,
the add-on prices from `entitlement_service.ADDON_CATALOG`, and every function the
prose names is imported so a rename breaks the build instead of the document.
`tests/scripts/test_tenancy_doc_is_current.py` fails when code and document drift.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DOC = REPO / "docs" / "hardening" / "TENANCY-AND-PLANS.md"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _seed() -> Any:
    spec = importlib.util.spec_from_file_location("seed_quota_tiers_for_doc", BACKEND / "scripts" / "seed_quota_tiers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ref(dotted: str) -> str:
    """`module.attribute`, verified to exist, as inline code."""
    module_name, _, attribute = dotted.rpartition(".")
    module = importlib.import_module(module_name)
    if not hasattr(module, attribute):
        raise SystemExit(f"generate_tenancy_doc: {dotted} does not exist; update the document's prose.")
    return f"`{dotted}`"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _yes(value: bool) -> str:
    return "yes" if value else "—"


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def _organization_role_table() -> str:
    from app.core import organization_permissions as op
    from app.models.organization import OrganizationRole

    roles = [OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.BILLING, OrganizationRole.MEMBER]
    actions: list[tuple[str, Callable[[Any], bool]]] = [
        ("See plan, invoices, usage", op.can_view_billing),
        ("Change plan, payment method, cancel", op.can_manage_billing),
        ("Buy or release seats", op.can_manage_seats),
        ("Rename the organization, branding", op.can_manage_organization_settings),
        ("Invite people", op.can_invite_members),
        ("Change roles, remove members", op.can_manage_members),
        ("Create a workspace", op.can_create_workspace),
        ("Archive or delete a workspace", op.can_delete_workspace),
        ("Read the audit log", op.can_view_audit_log),
        ("Create API keys", op.can_manage_api_keys),
        ("Manage outgoing webhooks", op.can_manage_webhooks),
        ("Configure single sign-on", op.can_configure_sso),
        ("Set the security policy", op.can_manage_security_policy),
        ("Transfer ownership", op.can_transfer_ownership),
        ("Delete (archive) the organization", op.can_delete_organization),
    ]
    rows = [[label, *[_yes(check(role)) for role in roles]] for label, check in actions]
    return _table(["Organization action", *[r.value.title() for r in roles]], rows)


def _assign_table() -> str:
    from app.core import organization_permissions as op
    from app.models.organization import OrganizationRole

    roles = [OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.BILLING, OrganizationRole.MEMBER]
    rows = []
    for actor in roles:
        assigns = [r.value.title() for r in roles if op.can_assign_organization_role(actor, r)]
        modifies = [r.value.title() for r in roles if op.can_modify_member(actor, r)]
        rows.append([actor.value.title(), ", ".join(assigns) or "nobody", ", ".join(modifies) or "nobody"])
    return _table(["Actor", "May invite or assign the role", "May change or remove a member who is"], rows)


def _effective_role_table() -> str:
    from app.core.workspace_permissions import resolve_effective_workspace_role
    from app.models.organization import OrganizationRole
    from app.models.workspace import WorkspaceRole

    grants: list[Any] = [None, WorkspaceRole.VIEWER, WorkspaceRole.CONTRIBUTOR, WorkspaceRole.ADMIN]
    rows = []
    for org_role in [OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.BILLING, OrganizationRole.MEMBER]:
        cells = []
        for grant in grants:
            effective = resolve_effective_workspace_role(org_role, grant)
            cells.append(effective.value.title() if effective else "no access (404)")
        rows.append([org_role.value.title(), *cells])
    return _table(
        ["Organization role", "No workspace grant", "Viewer grant", "Contributor grant", "Admin grant"], rows
    )


def _workspace_role_table() -> str:
    from app.core import workspace_permissions as wp
    from app.models.workspace import WorkspaceRole

    roles = [WorkspaceRole.ADMIN, WorkspaceRole.CONTRIBUTOR, WorkspaceRole.VIEWER]
    actions: list[tuple[str, Callable[[Any], bool]]] = [
        ("Read documents, results, conversations", wp.can_view_content),
        ("Upload, create, edit own work", wp.can_create_content),
        ("Edit anyone's work, delete documents", wp.can_edit_any_content),
        ("Use the assistant", wp.can_use_assistant),
        ("Manage automations", wp.can_manage_automation),
        ("Export data", wp.can_export_data),
        ("Change workspace settings", wp.can_manage_workspace_settings),
        ("Manage the workspace team", wp.can_manage_workspace_members),
        ("Invite to the workspace", wp.can_invite_to_workspace),
    ]
    rows = [[label, *[_yes(check(role)) for role in roles]] for label, check in actions]
    return _table(["Workspace action (effective role)", *[r.value.title() for r in roles]], rows)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

PLAN_ORDER = ("free", "developer", "business", "enterprise")

METER_WORDS = {
    "document.upload": "Document uploads",
    "ocr.page": "OCR pages",
    "assistant.message": "Assistant messages",
    "storage.gb_month": "Storage (GB, a hard ceiling only where REFUSE)",
    "llm.input_token": "AI input tokens (safety net)",
    "llm.output_token": "AI output tokens (safety net)",
    "*": "Usage cost ceiling, USD (safety net)",
}
LIMIT_WORDS = {
    "limit.seats": "Seats",
    "limit.workspaces": "Workspaces",
    "limit.file_size_mb": "File size, MB",
    "limit.pages_per_document": "Pages per document",
}
POLICY_WORDS = {"REFUSE": "refuse", "ALLOW_AND_BILL": "bill overage", "ALLOW_AND_WARN": "warn"}


def _num(value: Any) -> str:
    number = Decimal(str(value)).normalize()
    if number == number.to_integral_value():
        return f"{int(number):,}"
    return format(number, "f")


def _entries(tiers: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {row["limit_key"]: row for row in tiers[key]["entries"]}


def _price_table(seed: Any) -> str:
    rows = []
    for key in PLAN_ORDER:
        terms = seed.COMMERCIALS[key]
        amount = int(terms["unit_amount_micros"]) / 1_000_000
        price = "$0" if amount == 0 else f"${amount:,.0f} per seat per {terms['billing_interval']}"
        rows.append([seed.PLACEHOLDER_TIERS[key]["display_name"], price])
    return _table(["Plan", "Price"], rows)


def _allowance_table(seed: Any) -> str:
    tiers = seed.PLACEHOLDER_TIERS
    rows = []
    for meter, words in METER_WORDS.items():
        cells = []
        for key in PLAN_ORDER:
            row = _entries(tiers, key).get(meter)
            if row is None:
                cells.append("no ceiling" if meter != "*" else "—")
                continue
            if meter == "*":
                value = f"${int(row['max_cost_micros']) / 1_000_000:,.0f}"
            else:
                value = _num(row["max_quantity"])
            per = " / seat" if key != "free" else ""
            cells.append(f"{value}{per} ({POLICY_WORDS[row.get('overage_policy', 'REFUSE')]})")
        rows.append([words, *cells])
    return _table(["Per month", *[tiers[k]["display_name"] for k in PLAN_ORDER]], rows)


def _limit_table(seed: Any) -> str:
    from app.core.config import settings

    platform = {
        "limit.seats": "purchased seats",
        "limit.workspaces": "unlimited",
        "limit.file_size_mb": f"{int(settings.MAX_UPLOAD_SIZE) // (1024 * 1024)} (platform maximum)",
        "limit.pages_per_document": f"{int(settings.MAX_DOCUMENT_PAGES)} (platform maximum)",
    }
    tiers = seed.PLACEHOLDER_TIERS
    rows = []
    for limit, words in LIMIT_WORDS.items():
        cells = []
        for key in PLAN_ORDER:
            row = _entries(tiers, key).get(limit)
            cells.append(_num(row["max_quantity"]) if row is not None else platform[limit])
        rows.append([words, *cells])
    return _table(["Per organization", *[tiers[k]["display_name"] for k in PLAN_ORDER]], rows)


def _capability_table(seed: Any) -> str:
    from app.api.capability_gate import _DISPLAY_NAMES
    from app.core import entitlements

    tiers = seed.PLACEHOLDER_TIERS
    held = {key: set(_entries(tiers, key)) for key in PLAN_ORDER}
    rows = []
    for capability in sorted(entitlements.CAPABILITY_KEYS):
        name = _DISPLAY_NAMES.get(capability, capability)
        rows.append([f"{name} (`{capability}`)", *[_yes(capability in held[k]) for k in PLAN_ORDER]])
    rows.append(["AI on the platform's provider account (`llm.platform_key`)",
                 *[_yes("llm.platform_key" in held[k]) for k in PLAN_ORDER]])
    return _table(["Capability", *[tiers[k]["display_name"] for k in PLAN_ORDER]], rows)


def _addon_table(seed: Any) -> str:
    from app.services.billing.entitlement_service import ADDON_CATALOG

    tiers = seed.PLACEHOLDER_TIERS
    rows = []
    for key, offer in sorted(ADDON_CATALOG.items()):
        cells = []
        for plan in PLAN_ORDER:
            if key in _entries(tiers, plan):
                cells.append("included")
            elif plan == "free":
                cells.append("—")
            else:
                cells.append(f"${offer.monthly_price_micros / 1_000_000:,.0f}/month")
        rows.append([f"{offer.display_name} (`{key}`)", *cells])
    return _table(["Add-on", *[tiers[k]["display_name"] for k in PLAN_ORDER]], rows)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def render() -> str:
    seed = _seed()
    parts = [
        "# Tenancy, roles and plans — the source of truth",
        "",
        "_Generated from the code by `backend/scripts/generate_tenancy_doc.py`. Do not edit by hand:"
        " change the code, run the script, commit both. `tests/scripts/test_tenancy_doc_is_current.py`"
        " fails when the two drift._",
        "",
        "## 1. Who is the customer",
        "",
        "The **organization** is the customer. It holds **one** subscription: a plan and, on a paid plan,"
        " a number of seats. Every person in the organization occupies **one seat**; members never pay and"
        " never need a plan of their own. A person can belong to several organizations: inside each, what"
        " they may do comes from **that** organization's plan and their role **there**, so switching"
        " organization switches plan and role. Workspaces live inside an organization and share its plan"
        " and its usage allowance.",
        "",
        "## 2. Entities and how they relate",
        "",
        _table(
            ["Entity", "Table", "What it is"],
            [
                ["Account", "`users`", "A person's login. Owns nothing commercial by itself."],
                ["Organization", "`organizations`", "The customer. Points at its plan (`quota_tier_id`)."],
                ["Organization membership", "`organization_members`",
                 "A person in an organization, with one role (Owner, Admin, Billing, Member). An ACTIVE"
                 " membership is a seat."],
                ["Workspace", "`workspaces`", "A space for documents inside one organization."],
                ["Workspace grant", "`workspace_members`",
                 "A member's role in one workspace (Admin, Contributor, Viewer)."],
                ["Invitation", "`organization_invitations`",
                 "A pending offer of a membership. It holds the seat its acceptance will take."],
                ["Seat", "`billable_seats` (view)",
                 "Active memberships. Capacity: the plan's `limit.seats` on Free, `subscriptions."
                 "seats_purchased` on a paid plan (" + _ref("app.services.seat_capacity_service.seat_capacity") + ")."],
                ["Billing account", "`billing_accounts`", "The organization's customer record at the gateway."],
                ["Subscription", "`subscriptions`",
                 "The live gateway subscription: plan version, price book and seats purchased."],
                ["Plan version", "`quota_tiers` + `quota_tier_entries`",
                 "An immutable published version of a plan. A live subscription pins the version it was"
                 " sold (grandfathering); without one the organization follows its plan's newest version."],
                ["Price book", "`price_books` + `price_book_entries`",
                 "Unit prices and cost bases for every meter, seats and overage. Subscriptions pin one."],
                ["Entitlement / capability", "rows in `quota_tier_entries`",
                 "`capability.*`, `addon.*`, `llm.platform_key`: granted by the row's presence."],
                ["Quota entry", "rows in `quota_tier_entries`",
                 "A meter's monthly ceiling and overage policy, or a static `limit.*`."],
                ["Add-on", "`organization_addons`", "A purchased extra (custom domain, warehouse sync)."],
                ["API key", "`api_keys`",
                 "Acts for the organization with the scopes it was given; its creator must remain a member."],
                ["Partner tenancy", "`partner_organizations`",
                 "A reseller's book of organizations. Partner staff are not members and take no seats."],
            ],
        ),
        "",
        "## 3. Roles",
        "",
        "### 3.1 Organization roles",
        "",
        _organization_role_table(),
        "",
        "### 3.2 Who may give which role",
        "",
        "Nobody can give a role above what they may give, nobody can raise themselves, the last active owner"
        " cannot leave or be demoted, and an owner is moved only by the two-party ownership transfer.",
        "",
        _assign_table(),
        "",
        "### 3.3 Effective role in a workspace",
        "",
        "Owners and admins of the organization are admins of every workspace. Billing managers and members"
        " get exactly their workspace grant, and without one they see nothing in that workspace (it answers"
        " 404, so its existence is not revealed). Computed by "
        + _ref("app.core.workspace_permissions.resolve_effective_workspace_role") + ".",
        "",
        _effective_role_table(),
        "",
        "### 3.4 Workspace roles",
        "",
        _workspace_role_table(),
        "",
        "## 4. Plans",
        "",
        "### 4.1 Prices",
        "",
        _price_table(seed),
        "",
        "### 4.2 Monthly allowances",
        "",
        "Free is per organization, and **one Free allowance is shared by every Free organization an"
        " account owns** (archived ones included): " + _ref("app.services.quota_service.usage_pool") + "."
        " A paid plan's allowances are **per seat, pooled across the organization**: the figure times the"
        " seats the subscription holds (" + _ref("app.services.quota_service.seat_factor") + ")."
        " Documents, OCR pages, assistant messages and storage are what a customer plans by; the token and"
        " cost ceilings are the platform's safety net above them. Overage: *refuse* stops at the ceiling,"
        " *bill overage* continues and bills each unit above it at the price book's overage price, *warn*"
        " continues free of charge and the usage screens show the overrun.",
        "",
        _allowance_table(seed),
        "",
        "### 4.3 Per-organization limits",
        "",
        _limit_table(seed),
        "",
        "### 4.4 Capabilities",
        "",
        _capability_table(seed),
        "",
        "### 4.5 Add-ons",
        "",
        _addon_table(seed),
        "",
        "## 5. How a request is resolved: tenant, then role, then plan, then quota",
        "",
        _table(
            ["Caller", "Tenant", "Role", "Plan and quota"],
            [
                ["Browser", "the organization and workspace in the URL; a verified email is required ("
                 + _ref("app.api.deps.get_verified_user") + ")",
                 "the membership and grant, resolved on every request (no cached role)",
                 "the live subscription's pinned plan, else the organization's plan ("
                 + _ref("app.services.quota_service.resolve_tier") + "); capabilities by "
                 + _ref("app.api.capability_gate.require_capability") + "; meters by "
                 + _ref("app.services.spend_control_service.ensure_within_limits") + "; uploads, workspaces and"
                 " assistant messages by " + _ref("app.services.plan_admission.admit_document") + ", "
                 + _ref("app.services.plan_admission.assert_workspace_available") + " and "
                 + _ref("app.services.plan_admission.admit_assistant_message")],
                ["API key", "the key's organization", "the key's scopes, while its creator is a member",
                 "the same plan; the Developer API capability is required to use a key at all"],
                ["Worker", "the job's organization", "a system principal",
                 "the same meters checked again before the work is charged (OCR pages, tokens); a document"
                 " is charged when accepted, so a job never starts work the plan cannot pay for"],
                ["Public document-request link", "the request's organization", "none (the link is the"
                 " permission)", "the same intake and the same document charge; a refusal tells the"
                 " uploader only to contact the sender"],
                ["SCIM", "the token's organization", "the directory's mapped role",
                 "the organization's seats (" + _ref("app.services.seat_capacity_service.assert_seat_available")
                 + "); past them, a SCIM 403"],
                ["Single sign-on (JIT)", "the identity provider's organization", "the mapped role",
                 "the organization's seats, then the provider's own cap if CAPPED"],
            ],
        ),
        "",
        "## 6. Seats",
        "",
        "One check bounds every way in (invitation issue, resend and accept; SSO and SCIM provisioning;"
        " SCIM reactivation): " + _ref("app.services.seat_capacity_service.assert_seat_available") + "."
        " Used seats are active members plus pending invitations. Free holds the seats its plan declares."
        " A paid plan holds the seats its subscription bought; owners, admins and billing managers buy more"
        " (or release unused ones) with " + _ref("app.services.billing.seat_service.set_purchased_seats")
        + " after the price is shown (`GET .../billing/price-book/seat`), and a purchase at any other price"
        " is refused. Removing a member frees the seat for someone else; the subscription keeps it until it"
        " is released (the gateway credits the rest of the period). Checkout cannot sell fewer seats than"
        " are in use.",
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed document is stale")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
        if current != text:
            print(f"{DOC} is stale: run python scripts/generate_tenancy_doc.py")
            return 1
        print("TENANCY-AND-PLANS.md is current.")
        return 0
    DOC.write_text(text, encoding="utf-8")
    print(f"wrote {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
