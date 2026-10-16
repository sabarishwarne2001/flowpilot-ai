"""Approval chains (Enterprise): above an amount, a payment waits for two or three different people.

THE CHAIN
=========
A book's chain is a list of tiers, kept in `helm_settings.preferences["approval_chain"]` (a key in
an existing JSONB column, so no migration):

    {"tiers": [{"above_micros": 1000000000000, "currency": "INR", "approvals": 2,
                "roles": ["OWNER", "ADMIN"]}],
     "updated_at": "2026-10-15T09:00:00+00:00", "updated_by_user_id": "..."}

A payment (scheduling or approving one) in a tier's currency and strictly above its amount needs that
many approvals from different people; `roles`, when set, says whose approvals count (otherwise
everyone who may approve a payment: owners, admins and the billing role). When several tiers apply,
the one with the highest amount decides. Holds, releases, a recorded payment and every action that
moves no money keep the ordinary single approval: a chain slows money going out, never a stop.

ONE RULE FOR EVERY PATH
=======================
`requirement` is the one answer every approving path reads: approving one action, approving the whole
plan, and Act mode. An action a chain covers never runs on Helm's own say-so, whatever its act cap:
Helm's approval does not count towards a chain. The chain applies while the plan carries
`capability.helm_approval_chains` (Enterprise); a saved chain stays stored and applies again after an
upgrade. Everything here except `chain_for`, `people_by_role` and `save` is pure.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.services.helm import money
from app.services.helm import vocabulary as v

logger = logging.getLogger("app.services.helm.approvals")

#: The key in `helm_settings.preferences`.
PREFERENCE_KEY = "approval_chain"
#: Only actions that send money to a payee wait for a chain.
CHAIN_ACTIONS = (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT)
#: Who may approve a payment at all (brief §5.4), in the order a sentence names them.
CHAIN_ROLES = ("OWNER", "ADMIN", "BILLING")
CHAIN_APPROVALS = (2, 3)
MAX_TIERS = 6
#: The largest amount a tier may start above (a trillion units, in micros).
MAX_ABOVE_MICROS = 10**12 * money.MICROS

ROLE_WORDS = {"OWNER": "an owner", "ADMIN": "an admin", "BILLING": "the billing role"}
ROLE_PLURALS = {"OWNER": "owners", "ADMIN": "admins", "BILLING": "the billing role"}
COUNT_WORDS = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
ORDINAL_WORDS = {1: "first", 2: "second", 3: "third"}


class ChainInvalid(Exception):
    """A chain a person typed that cannot be kept; `problems` says why, in words."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Tier:
    above_micros: int
    currency: str
    approvals: int
    #: Whose approvals count; empty: everyone who may approve a payment.
    roles: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {"above_micros": self.above_micros, "currency": self.currency, "approvals": self.approvals,
                "roles": list(self.roles) or None}


@dataclass(frozen=True)
class Requirement:
    """What one action needs before it may run: how many different people, and from which roles."""

    approvals: int = 1
    roles: tuple[str, ...] = ()
    tier: Optional[Tier] = None

    def allows(self, role: Any) -> bool:
        return not self.roles or str(getattr(role, "value", role)).upper() in self.roles

    def counted(self, approvals: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """The approvals that count: a person's (never Helm's own), from a role this requirement takes."""
        return [a for a in approvals if a.get("user_id") and self.allows(a.get("role") or "")]


def roles_words(roles: Iterable[str]) -> str:
    """("OWNER", "ADMIN") -> "owners or admins"; empty -> the people who approve payments."""
    names = [ROLE_PLURALS[r] for r in CHAIN_ROLES if r in set(roles)]
    if not names:
        return "owners, admins or the billing role"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def describe(tier: Tier) -> str:
    """"Above ₹10 lakh: two approvals from different people (owners or admins)." """
    return (f"Above {money.say(tier.above_micros, tier.currency)}: {COUNT_WORDS[tier.approvals]} approvals from "
            f"different people ({roles_words(tier.roles)}).")


def _tier(raw: Any) -> Tier:
    """One stored or typed tier, strictly: a ValueError names what is wrong."""
    if not isinstance(raw, dict):
        raise ValueError("A tier is an amount, a currency and a number of approvals.")
    currency = str(raw.get("currency") or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("a currency is three letters, such as INR.")
    above = raw.get("above_micros")
    if isinstance(above, bool) or not isinstance(above, int) or above < 0 or above > MAX_ABOVE_MICROS:
        raise ValueError("the amount is zero or more, written as a number.")
    approvals = raw.get("approvals")
    if isinstance(approvals, bool) or approvals not in CHAIN_APPROVALS:
        raise ValueError("a chain asks for two or three approvals.")
    roles = raw.get("roles") or []
    if not isinstance(roles, (list, tuple)):
        raise ValueError("the roles are a list, such as owners and admins.")
    wanted = {str(r).strip().upper() for r in roles}
    if wanted - set(CHAIN_ROLES):
        raise ValueError("approvals come from owners, admins or the billing role.")
    return Tier(above_micros=above, currency=currency, approvals=int(approvals),
                roles=tuple(r for r in CHAIN_ROLES if r in wanted))


def _ordered(tiers: Iterable[Tier]) -> tuple[Tier, ...]:
    return tuple(sorted(tiers, key=lambda t: (t.currency, t.above_micros)))


def parse(raw: Any) -> tuple[Tier, ...]:
    """The stored chain, read tolerantly: a tier that does not read is left out and logged. Only
    `validate` writes the chain, so this is a guard against a hand-edited row, not a second format."""
    if not isinstance(raw, dict):
        return ()
    tiers = []
    for item in raw.get("tiers") or []:
        try:
            tiers.append(_tier(item))
        except ValueError:
            logger.warning("helm.approval_chain_tier_unreadable")
    return _ordered(tiers)


def validate(raw_tiers: Any, *, people: Optional[dict[str, int]] = None) -> tuple[Tier, ...]:
    """A chain a person typed: the cleaned tiers (by currency, then amount), or ChainInvalid with every
    problem in words. `people` (active members per role), when given, refuses a tier that could never
    be met: three approvals from owners in an organization with one owner would stop those payments
    for good."""
    if not isinstance(raw_tiers, (list, tuple)):
        raise ChainInvalid(["An approval chain is a list of tiers."])
    problems: list[str] = []
    if len(raw_tiers) > MAX_TIERS:
        problems.append(f"An approval chain has at most {COUNT_WORDS.get(MAX_TIERS, MAX_TIERS)} tiers.")
    tiers: list[Tier] = []
    for index, raw in enumerate(raw_tiers, start=1):
        try:
            tiers.append(_tier(raw))
        except ValueError as exc:
            problems.append(f"Tier {index}: {exc}")
    seen: dict[tuple[str, int], Tier] = {}
    for tier in tiers:
        key = (tier.currency, tier.above_micros)
        if key in seen:
            problems.append(f"Two tiers start above {money.say(tier.above_micros, tier.currency)}; keep one.")
        seen[key] = tier
    ordered = _ordered(seen.values())
    for lower, higher in zip(ordered, ordered[1:]):
        if lower.currency == higher.currency and higher.approvals < lower.approvals:
            problems.append(
                f"Above {money.say(higher.above_micros, higher.currency)} needs at least as many approvals as above "
                f"{money.say(lower.above_micros, lower.currency)}.")
    if people is not None:
        problems.extend(f"{short}: those payments could never be approved." for short in _short_of(ordered, people))
    if problems:
        raise ChainInvalid(problems)
    return ordered


def _short_of(tiers: Iterable[Tier], people: dict[str, int]) -> list[str]:
    """"Above ₹1 lakh the chain needs three different people from ..., and your organization has two"
    for each tier the organization has too few people to meet."""
    out = []
    for tier in tiers:
        available = sum(int(people.get(role, 0)) for role in (tier.roles or CHAIN_ROLES))
        if available < tier.approvals:
            out.append(f"Above {money.say(tier.above_micros, tier.currency)} the chain needs "
                       f"{COUNT_WORDS[tier.approvals]} different people from {roles_words(tier.roles)}, and your "
                       f"organization has {COUNT_WORDS.get(available, available)}")
    return out


def warnings(tiers: Iterable[Tier], people: dict[str, int]) -> list[str]:
    """A kept chain the organization can no longer meet (someone left, or changed role): said on the
    settings page, because those payments wait until a person is added or the chain is changed."""
    return [f"{short}: those payments wait until someone is added or the chain is changed."
            for short in _short_of(tiers, people)]


def requirement(tiers: Iterable[Tier], *, kind: str, amount_micros: Optional[int],
                currency: Optional[str]) -> Requirement:
    """What an action needs: the tier with the highest amount below the action's, in its currency."""
    if kind not in CHAIN_ACTIONS or not currency or amount_micros is None:
        return Requirement()
    applying = [t for t in tiers if t.currency == currency and int(amount_micros) > t.above_micros]
    if not applying:
        return Requirement()
    tier = max(applying, key=lambda t: t.above_micros)
    return Requirement(approvals=tier.approvals, roles=tier.roles, tier=tier)


def chain_json(tiers: Iterable[Tier], *, available: bool, stored: Optional[dict[str, Any]] = None,
               people: Optional[dict[str, int]] = None) -> dict[str, Any]:
    """The chain as the settings page reads it. `available`: the plan carries approval chains (a kept
    chain applies only then); `warnings`: tiers the organization has too few people to meet."""
    stored = stored or {}
    tiers = list(tiers)
    return {"available": available, "tiers": [t.as_json() for t in tiers],
            "sentences": [describe(t) for t in tiers], "updated_at": stored.get("updated_at"),
            "warnings": warnings(tiers, people) if people is not None and available else []}


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------


def stored(settings) -> dict[str, Any]:
    raw = (settings.preferences or {}).get(PREFERENCE_KEY)
    return raw if isinstance(raw, dict) else {}


def chain_for(db, book) -> tuple[Tier, ...]:
    """The book's chain as it applies now: empty unless the plan carries approval chains."""
    from app.services.helm import gate, snapshot_store

    if not gate.approval_chains_held(db, book.organization_id):
        return ()
    return parse(stored(snapshot_store.settings_for(db, book)))


def people_by_role(db, organization_id: uuid.UUID) -> dict[str, int]:
    """Active members of the organization per role (who could ever give an approval)."""
    from sqlalchemy import func, select

    from app.models.organization import MembershipStatus, OrganizationMember

    rows = db.execute(select(OrganizationMember.role, func.count())
                      .where(OrganizationMember.organization_id == organization_id,
                             OrganizationMember.status == MembershipStatus.ACTIVE)
                      .group_by(OrganizationMember.role)).all()
    return {str(getattr(role, "value", role)).upper(): int(n) for role, n in rows}


def save(db, settings, tiers: tuple[Tier, ...], *, user_id: uuid.UUID) -> tuple[Tier, ...]:
    """Store the chain under a row lock (the Monday briefing writes the same JSONB column) and return the
    chain it replaced, read under that lock so the audit's "before" is the one overwritten."""
    db.refresh(settings, with_for_update=True)
    before = parse(stored(settings))
    value = {"tiers": [t.as_json() for t in tiers], "updated_at": datetime.now(timezone.utc).isoformat(),
             "updated_by_user_id": str(user_id)}
    settings.preferences = {**(settings.preferences or {}), PREFERENCE_KEY: value}
    db.flush()
    return before


__all__ = ["CHAIN_ACTIONS", "CHAIN_APPROVALS", "CHAIN_ROLES", "ChainInvalid", "MAX_TIERS", "ORDINAL_WORDS",
           "PREFERENCE_KEY", "Requirement", "Tier", "chain_for", "chain_json", "describe", "parse", "people_by_role",
           "requirement", "roles_words", "save", "stored", "validate", "warnings"]
