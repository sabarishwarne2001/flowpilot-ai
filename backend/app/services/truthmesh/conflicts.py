"""Cross-document conflicts, read through the relation between the documents. Pure: no I/O.

The corroborator asks "do these documents say the same thing?". Across a mesh that is the wrong
question for most pairs: an invoice is SUPPOSED to differ from the purchase order it bills (its own
number, its date, tax on top). TruthMesh asks the question each relation implies:

  child -> parent (BILLS_AGAINST, BILLED_UNDER, ISSUED_UNDER, CLAIMS_UNDER)
      does the child stay within the parent's authority?   UNAUTHORIZED_COMMITMENT
      do all the children together?                         CUMULATIVE_OVERRUN
      are two children the same bill twice?                 DUPLICATE_BILLING
      is the child dated inside the agreement's term?       OUT_OF_TERM
      is it dated after the order it fulfils?               DATE_CONTRADICTION
      is the child's vendor a party to the agreement?       PARTY_MISMATCH
      do currencies and payment terms agree?                CURRENCY_MISMATCH, TERM_CONFLICT
  two versions of one document (VERSION_OF)
      amounts, dates and payee accounts must be equal       AMOUNT_MISMATCH, DATE_CONTRADICTION,
                                                            PAYEE_ACCOUNT_CHANGED; equal = DUPLICATE
  documents from one vendor, linked by a reference
      the payee account must not change                     PAYEE_ACCOUNT_CHANGED
  a spend document no order, contract or policy backs      UNSUPPORTED_COMMITMENT

Each conflict carries the documents, each one's value, the money at risk (exposure) and a
fingerprint that keeps it the same conflict across rebuilds.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.services.truthmesh import vocabulary as v
from app.services.truthmesh.facts import Twin
from app.services.truthmesh.linker import LinkDraft

MICROS = Decimal("1000000")
#: Sorts an undated document after every dated one.
_FAR = date(9999, 12, 31)


@dataclass
class ConflictDraft:
    kind: str
    concept: str
    severity: str
    title: str
    summary: str
    work_item_ids: list[uuid.UUID]
    document_values: list[dict[str, Any]]
    relation: Optional[str] = None
    exposure_micros: Optional[int] = None
    currency: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    key: str = ""
    #: The documents that make the conflict what it is, when not all of `work_item_ids` do (a change
    #: of payee account is the later invoice's, whichever copy of the earlier one shows it).
    identity: Optional[list[uuid.UUID]] = None

    @property
    def fingerprint(self) -> str:
        ids = ",".join(sorted(str(i) for i in (self.identity if self.identity is not None else self.work_item_ids)))
        return hashlib.sha256(f"{self.kind}|{self.concept}|{ids}|{self.key}".encode()).hexdigest()


def money(micros: Optional[int], currency: Optional[str]) -> str:
    if micros is None:
        return "an unknown amount"
    amount = (Decimal(micros) / MICROS).quantize(Decimal("0.01"))
    return f"{currency + ' ' if currency else ''}{amount:,.2f}"


def _name(t: Twin) -> str:
    return f"{t.kind_label.lower()} {t.title}" if t.document_number else t.filename


def _value(t: Twin, display: Any, **extra: Any) -> dict[str, Any]:
    return {"work_item_id": str(t.work_item_id), "title": t.title, "kind": t.kind, "display": display, **extra}


def _tolerance(amount: int) -> int:
    return max(int(v.MONEY_TOLERANCE * MICROS), int(abs(amount) * v.MONEY_RELATIVE_TOLERANCE))


def _over_severity(over: int, base: int) -> str:
    share = over / base if base else 1.0
    if share > 0.25:
        return v.SEVERITY_CRITICAL
    if share > 0.05:
        return v.SEVERITY_HIGH
    return v.SEVERITY_MEDIUM


def _same_currency(a: Twin, b: Twin) -> bool:
    return not (a.currency and b.currency and a.currency != b.currency)


def _comparable(t: Twin) -> Optional[int]:
    """The amount a child draws on its parent: net of tax when the document states both."""
    return t.net_amount_micros if t.net_amount_micros is not None else t.amount_micros


def _usable(link: LinkDraft, status: Mapping[tuple[uuid.UUID, uuid.UUID], str]) -> bool:
    state = status.get(link.pair, "AUTO")
    if state == "REJECTED":
        return False
    return state == "CONFIRMED" or link.strength >= v.CONFLICT_LINK_STRENGTH


def _identifier_backed(link: LinkDraft) -> bool:
    return any(s["kind"] in (v.SIGNAL_IDENTIFIER, v.SIGNAL_TEXT_REFERENCE, v.SIGNAL_SAME_NUMBER,
                             v.SIGNAL_SHARED_REFERENCE) for s in link.signals)


# --------------------------------------------------------------------------- pair rules

def _child_parent(child: Twin, parent: Twin, link: LinkDraft) -> list[ConflictDraft]:
    out: list[ConflictDraft] = []
    rel = link.relation
    ids = [child.work_item_id, parent.work_item_id]
    rel_label = v.RELATION_LABELS.get(rel, "depends on")

    if rel in v.DRAWDOWN_RELATIONS and _same_currency(child, parent):
        drawn, authority = _comparable(child), _comparable(parent)
        if drawn is not None and authority is not None and authority > 0:
            over = drawn - authority
            if over > _tolerance(authority):
                currency = child.currency or parent.currency
                out.append(ConflictDraft(
                    kind=v.K_UNAUTHORIZED_COMMITMENT, concept="amount", severity=_over_severity(over, authority),
                    title=f"{child.title} exceeds {parent.title} by {money(over, currency)}",
                    summary=(f"The {_name(child)} {rel_label} the {_name(parent)} for {money(drawn, currency)}, "
                             f"but the {parent.kind_label.lower()} authorises {money(authority, currency)}. "
                             f"{money(over, currency)} ({over / authority:.1%}) has no authority."),
                    work_item_ids=ids, relation=rel, exposure_micros=over, currency=currency,
                    document_values=[_value(child, money(drawn, currency), role="child", micros=drawn),
                                     _value(parent, money(authority, currency), role="parent", micros=authority)],
                    details={"exposure_key": f"doc:{child.work_item_id}"}))

    if parent.kind in v.AGREEMENT_KINDS and child.document_date and (parent.effective_date or parent.end_date):
        before = parent.effective_date and child.document_date < parent.effective_date
        after = parent.end_date and child.document_date > parent.end_date
        if before or after:
            edge = parent.effective_date if before else parent.end_date
            out.append(ConflictDraft(
                kind=v.K_OUT_OF_TERM, concept="date", severity=v.SEVERITY_HIGH,
                title=f"{child.title} is dated outside {parent.title}'s term",
                summary=(f"The {_name(child)} is dated {child.document_date.isoformat()}, "
                         f"{'before the agreement starts' if before else 'after the agreement ends'} "
                         f"({edge.isoformat() if edge else '—'}). A commitment outside the term has no "
                         f"contractual cover."),
                work_item_ids=ids, relation=rel,
                document_values=[_value(child, child.document_date.isoformat(), role="child"),
                                 _value(parent, f"{_iso(parent.effective_date)} to {_iso(parent.end_date)}",
                                        role="parent")]))

    if (parent.kind in ("PURCHASE_ORDER", "INSURANCE_CLAIM") and child.document_date and parent.document_date
            and child.document_date < parent.document_date and rel != v.REL_ADJUSTS):
        out.append(ConflictDraft(
            kind=v.K_DATE_CONTRADICTION, concept="date", severity=v.SEVERITY_MEDIUM,
            title=f"{child.title} predates the {parent.kind_label.lower()} it {rel_label.split()[0]}",
            summary=(f"The {_name(child)} is dated {child.document_date.isoformat()}, before the "
                     f"{_name(parent)} ({parent.document_date.isoformat()}). Work or billing before the order "
                     f"is a retroactive commitment."),
            work_item_ids=ids, relation=rel,
            document_values=[_value(child, child.document_date.isoformat(), role="child"),
                             _value(parent, parent.document_date.isoformat(), role="parent")]))

    if child.currency and parent.currency and child.currency != parent.currency:
        out.append(ConflictDraft(
            kind=v.K_CURRENCY_MISMATCH, concept="currency", severity=v.SEVERITY_HIGH,
            title=f"{child.title} is in {child.currency}; {parent.title} is in {parent.currency}",
            summary=(f"The {_name(child)} and the {_name(parent)} it {rel_label} state different currencies. "
                     f"Amounts cannot be checked against each other until one is converted or corrected."),
            work_item_ids=ids, relation=rel,
            document_values=[_value(child, child.currency, role="child"), _value(parent, parent.currency, role="parent")]))

    if (parent.kind in v.AGREEMENT_KINDS and len(parent.parties) >= 2 and child.counterparty
            and _identifier_backed(link)):
        from app.services.entities import normalize as en

        key = en.normalize_organization(child.counterparty) or child.counterparty.lower()
        if key not in parent.parties:
            out.append(ConflictDraft(
                kind=v.K_PARTY_MISMATCH, concept="party", severity=v.SEVERITY_HIGH,
                title=f"{child.counterparty} is not a party to {parent.title}",
                summary=(f"The {_name(child)} names {child.counterparty}, but the {_name(parent)} it {rel_label} "
                         f"is between {', '.join(parent.party_names.get(p, p) for p in parent.parties)}."),
                work_item_ids=ids, relation=rel,
                document_values=[_value(child, child.counterparty, role="child"),
                                 _value(parent, ", ".join(parent.party_names.get(p, p) for p in parent.parties),
                                        role="parent")]))

    for term, label in (("payment_days", "payment terms"), ("governing_law", "governing law")):
        mine, theirs = child.terms.get(term), parent.terms.get(term)
        if not mine or not theirs:
            continue
        if str(mine["value"]).strip().lower() == str(theirs["value"]).strip().lower():
            continue
        unit = " days" if term == "payment_days" else ""
        out.append(ConflictDraft(
            kind=v.K_TERM_CONFLICT, concept=term,
            severity=v.SEVERITY_MEDIUM if term == "payment_days" else v.SEVERITY_LOW,
            title=f"{label.capitalize()} differ: {child.title} vs {parent.title}",
            summary=(f"The {_name(child)} states {mine['value']}{unit}; the {_name(parent)} it {rel_label} "
                     f"states {theirs['value']}{unit}. The governing document's terms normally prevail."),
            work_item_ids=ids, relation=rel,
            document_values=[_value(child, f"{mine['value']}{unit}", role="child", quote=mine.get("quote")),
                             _value(parent, f"{theirs['value']}{unit}", role="parent", quote=theirs.get("quote"))]))
    return out


def _versions(a: Twin, b: Twin, link: LinkDraft) -> list[ConflictDraft]:
    out: list[ConflictDraft] = []
    ids = [a.work_item_id, b.work_item_id]
    first, second = sorted((a, b), key=lambda t: (t.document_date or _FAR, str(t.work_item_id)))
    currency = a.currency or b.currency
    differs = False
    if a.amount_micros is not None and b.amount_micros is not None and _same_currency(a, b):
        diff = abs(a.amount_micros - b.amount_micros)
        if diff > _tolerance(max(a.amount_micros, b.amount_micros)):
            differs = True
            out.append(ConflictDraft(
                kind=v.K_AMOUNT_MISMATCH, concept="amount", severity=v.SEVERITY_HIGH,
                title=f"Two versions of {a.title} disagree on the amount",
                summary=(f"{a.filename} states {money(a.amount_micros, currency)} and {b.filename} states "
                         f"{money(b.amount_micros, currency)} for the same {a.kind_label.lower()} number."),
                work_item_ids=ids, relation=link.relation, exposure_micros=diff, currency=currency,
                document_values=[_value(a, money(a.amount_micros, currency), micros=a.amount_micros),
                                 _value(b, money(b.amount_micros, currency), micros=b.amount_micros)],
                details={"exposure_key": f"doc:{second.work_item_id}"}))
    if a.document_date and b.document_date and a.document_date != b.document_date:
        differs = True
        out.append(ConflictDraft(
            kind=v.K_DATE_CONTRADICTION, concept="date", severity=v.SEVERITY_MEDIUM,
            title=f"Two versions of {a.title} carry different dates",
            summary=(f"{a.filename} is dated {a.document_date.isoformat()} and {b.filename} "
                     f"{b.document_date.isoformat()} for the same {a.kind_label.lower()} number."),
            work_item_ids=ids, relation=link.relation,
            document_values=[_value(a, a.document_date.isoformat()), _value(b, b.document_date.isoformat())]))
    if a.payee_account and b.payee_account and a.payee_account != b.payee_account:
        differs = True
        out.append(_payee_conflict(first, second, link, versions=True))
    if not differs and a.kind in v.SPEND_KINDS:
        amount = a.amount_micros if a.amount_micros is not None else b.amount_micros
        out.append(ConflictDraft(
            kind=v.K_DUPLICATE_BILLING, concept="document", severity=v.SEVERITY_MEDIUM,
            title=f"{a.title} is in the workspace twice",
            summary=(f"{a.filename} and {b.filename} are the same {a.kind_label.lower()} ({a.title}, "
                     f"{money(amount, currency)}). Make sure it is paid once."),
            work_item_ids=ids, relation=link.relation, exposure_micros=amount, currency=currency,
            document_values=[_value(a, a.filename), _value(b, b.filename)],
            details={"exposure_key": f"doc:{second.work_item_id}"}))
    return out


def _iso(value: Any) -> str:
    return value.isoformat() if value else "open"


def _payee_conflict(first: Twin, second: Twin, link: LinkDraft, *, versions: bool = False) -> ConflictDraft:
    def masked(account: str) -> str:
        return f"•••• {account[-4:]}"

    currency = second.currency or first.currency
    vendor = (second.counterparty or first.counterparty or "").strip().lower()
    return ConflictDraft(
        kind=v.K_PAYEE_ACCOUNT_CHANGED, concept="payee_account", severity=v.SEVERITY_CRITICAL,
        title=f"{second.title} asks to be paid into a different account than {first.title}",
        summary=(f"{second.counterparty or 'The vendor'} asks to be paid into {masked(second.payee_account or '')} "
                 f"on the {_name(second)}; the {_name(first)} used {masked(first.payee_account or '')}. "
                 f"Confirm the change with the vendor on a known number before paying."),
        work_item_ids=[first.work_item_id, second.work_item_id], relation=link.relation,
        exposure_micros=second.amount_micros, currency=currency,
        document_values=[_value(first, masked(first.payee_account or ""), role="earlier"),
                         _value(second, masked(second.payee_account or ""), role="later")],
        details={"exposure_key": f"doc:{second.work_item_id}", "versions": versions,
                 "dedupe": f"payee:{vendor}:{first.payee_account}:{second.payee_account}"},
        # One change of a vendor's account is one conflict, however many copies of either invoice exist.
        identity=[], key=f"{vendor}|{first.payee_account}>{second.payee_account}")


def _same_vendor(a: Twin, b: Twin) -> bool:
    return bool(a.counterparty and b.counterparty and a.counterparty.strip().lower() == b.counterparty.strip().lower())


# --------------------------------------------------------------------------- group rules

def _drawdowns(twins: Mapping[uuid.UUID, Twin], links: Sequence[LinkDraft],
               status: Mapping[tuple[uuid.UUID, uuid.UUID], str]) -> list[ConflictDraft]:
    out: list[ConflictDraft] = []
    children: dict[uuid.UUID, list[tuple[Twin, LinkDraft]]] = defaultdict(list)
    for link in links:
        if link.directed and link.relation in v.DRAWDOWN_RELATIONS and _usable(link, status):
            child, parent = twins.get(link.source), twins.get(link.target)
            if child is not None and parent is not None and child.kind in v.SPEND_KINDS:
                children[parent.work_item_id].append((child, link))
    for parent_id, members in children.items():
        parent = twins[parent_id]
        authority = _comparable(parent)
        # One child per own number: two copies of one invoice are one bill (VERSION_OF says so).
        distinct: dict[str, Twin] = {}
        for child, _ in sorted(members, key=lambda m: str(m[0].work_item_id)):
            key = child.identifiers[0] if child.identifiers else str(child.work_item_id)
            distinct.setdefault(key, child)
        bills = [c for c in distinct.values() if _comparable(c) is not None and _same_currency(c, parent)]
        currency = parent.currency or next((c.currency for c in bills if c.currency), None)
        if authority and len(bills) >= 2:
            drawn = sum(_comparable(c) or 0 for c in bills)
            over = drawn - authority
            if over > _tolerance(authority):
                ids = [parent.work_item_id] + [c.work_item_id for c in bills]
                # The money at risk is the latest bill's: paying it is what takes the total over.
                latest = max(bills, key=lambda t: (t.document_date or date.min, str(t.work_item_id)))
                out.append(ConflictDraft(
                    kind=v.K_CUMULATIVE_OVERRUN, concept="amount", severity=_over_severity(over, authority),
                    title=f"{len(bills)} documents draw {money(drawn, currency)} on {parent.title} "
                          f"({money(authority, currency)})",
                    summary=(f"Together, {', '.join(c.title for c in bills)} draw {money(drawn, currency)} against "
                             f"the {_name(parent)}, which authorises {money(authority, currency)}: "
                             f"{money(over, currency)} over."),
                    work_item_ids=ids, relation=members[0][1].relation, exposure_micros=over,
                    currency=currency, key="overrun", identity=[parent.work_item_id],
                    document_values=[_value(parent, money(authority, currency), role="parent", micros=authority)]
                    + [_value(c, money(_comparable(c), currency), role="child", micros=_comparable(c)) for c in bills],
                    details={"exposure_key": f"doc:{latest.work_item_id}", "drawn_micros": drawn,
                             "authorised_micros": authority}))
        for i, a in enumerate(bills):
            for b in bills[i + 1:]:
                amount_a, amount_b = _comparable(a), _comparable(b)
                if amount_a is None or amount_b is None or not _same_currency(a, b):
                    continue
                if abs(amount_a - amount_b) <= _tolerance(max(amount_a, amount_b)) and authority and \
                        amount_a >= authority // 2:
                    first, second = sorted((a, b), key=lambda t: (t.document_date or _FAR, str(t.work_item_id)))
                    out.append(ConflictDraft(
                        kind=v.K_DUPLICATE_BILLING, concept="amount", severity=v.SEVERITY_HIGH,
                        title=f"{first.title} and {second.title} bill the same {money(amount_a, currency)} "
                              f"against {parent.title}",
                        summary=(f"Two different {first.kind_label.lower()}s bill {money(amount_a, currency)} each "
                                 f"against the {_name(parent)}, which authorises {money(authority, currency)}. "
                                 f"One of them is likely a duplicate."),
                        work_item_ids=[first.work_item_id, second.work_item_id, parent.work_item_id],
                        relation=members[0][1].relation, exposure_micros=amount_b, currency=currency,
                        # The pair of bill NUMBERS under the order: stable whichever copy is seen.
                        identity=[parent.work_item_id],
                        key="|".join(sorted((first.identifiers or [str(first.work_item_id)])[:1]
                                            + (second.identifiers or [str(second.work_item_id)])[:1])),
                        document_values=[_value(first, money(amount_a, currency), role="child", micros=amount_a),
                                         _value(second, money(amount_b, currency), role="child", micros=amount_b),
                                         _value(parent, money(authority, currency), role="parent", micros=authority)],
                        details={"exposure_key": f"doc:{second.work_item_id}"}))
    return out


def _unsupported(twins: Mapping[uuid.UUID, Twin], links: Sequence[LinkDraft],
                 status: Mapping[tuple[uuid.UUID, uuid.UUID], str],
                 scope: Optional[set[uuid.UUID]]) -> list[ConflictDraft]:
    backed: set[uuid.UUID] = set()
    for link in links:
        if not _usable(link, status) or link.relation == v.REL_VERSION_OF:
            continue
        a, b = twins.get(link.source), twins.get(link.target)
        if a is None or b is None:
            continue
        if b.kind in v.AUTHORITY_KINDS:
            backed.add(a.work_item_id)
        if a.kind in v.AUTHORITY_KINDS:
            backed.add(b.work_item_id)
    out: list[ConflictDraft] = []
    for twin in twins.values():
        if twin.kind not in v.SPEND_KINDS or twin.work_item_id in backed:
            continue
        if scope is not None and twin.work_item_id not in scope:
            continue
        out.append(ConflictDraft(
            kind=v.K_UNSUPPORTED_COMMITMENT, concept="authority",
            severity=v.SEVERITY_MEDIUM if twin.amount_micros else v.SEVERITY_LOW,
            title=f"{twin.title} has no purchase order, contract or policy behind it",
            summary=(f"No document in the workspace authorises the {_name(twin)} "
                     f"({money(twin.amount_micros, twin.currency)}). Link it to its order or agreement, "
                     f"or confirm it was approved another way."),
            work_item_ids=[twin.work_item_id], relation=None, currency=twin.currency,
            document_values=[_value(twin, money(twin.amount_micros, twin.currency), micros=twin.amount_micros)]))
    return out


# --------------------------------------------------------------------------- entry point

def detect(twins: Sequence[Twin], links: Sequence[LinkDraft], *,
           status: Optional[Mapping[tuple[uuid.UUID, uuid.UUID], str]] = None,
           scope: Optional[set[uuid.UUID]] = None) -> list[ConflictDraft]:
    """Every conflict among `twins` across `links`, deduplicated by fingerprint, most severe first.
    `status` holds people's link decisions (REJECTED links are ignored, CONFIRMED always used);
    `scope` limits the per-document rules to those documents."""
    by_id = {t.work_item_id: t for t in twins}
    decided = status or {}
    found: dict[str, ConflictDraft] = {}

    seen: set[str] = set()

    def keep(items: Iterable[ConflictDraft]) -> None:
        for item in items:
            # One change of payee account is one conflict, however many copies of the earlier
            # invoice it is seen against.
            dedupe = item.details.get("dedupe")
            if dedupe:
                if dedupe in seen:
                    continue
                seen.add(dedupe)
            found.setdefault(item.fingerprint, item)

    for link in links:
        if not _usable(link, decided):
            continue
        a, b = by_id.get(link.source), by_id.get(link.target)
        if a is None or b is None:
            continue
        if link.relation == v.REL_VERSION_OF:
            keep(_versions(a, b, link))
        elif link.directed:
            keep(_child_parent(a, b, link))
        if (link.relation != v.REL_VERSION_OF and a.payee_account and b.payee_account
                and a.payee_account != b.payee_account and _same_vendor(a, b) and _identifier_backed(link)
                and a.kind in v.SPEND_KINDS and b.kind in v.SPEND_KINDS):
            first, second = sorted((a, b), key=lambda t: (t.document_date or _FAR, str(t.work_item_id)))
            keep([_payee_conflict(first, second, link)])
        if not link.directed and link.relation != v.REL_VERSION_OF and a.currency and b.currency \
                and a.currency != b.currency and _identifier_backed(link):
            keep([ConflictDraft(
                kind=v.K_CURRENCY_MISMATCH, concept="currency", severity=v.SEVERITY_MEDIUM,
                title=f"{a.title} is in {a.currency}; {b.title} is in {b.currency}",
                summary=f"Two linked documents ({a.filename}, {b.filename}) state different currencies.",
                work_item_ids=[a.work_item_id, b.work_item_id], relation=link.relation,
                document_values=[_value(a, a.currency), _value(b, b.currency)])])
    keep(_drawdowns(by_id, links, decided))
    keep(_unsupported(by_id, links, decided, scope))
    return sorted(found.values(), key=lambda c: (v.SEVERITY_ORDER[c.severity], -(c.exposure_micros or 0), c.title))


def exposure_total(conflicts: Iterable[Any]) -> int:
    """Money at risk: for each document whose payment is at risk, its largest exposure, summed.

    A duplicate bill that also changes the payee account and takes its order over budget is one
    payment that should not leave, not three."""
    best: dict[str, int] = {}
    for conflict in conflicts:
        kind = getattr(conflict, "kind", None)
        amount = getattr(conflict, "exposure_micros", None)
        if kind not in v.EXPOSURE_KINDS or not amount:
            continue
        details = getattr(conflict, "details", None) or {}
        key = details.get("exposure_key") or getattr(conflict, "fingerprint", str(id(conflict)))
        best[key] = max(best.get(key, 0), int(amount))
    return sum(best.values())


__all__ = ["ConflictDraft", "detect", "exposure_total", "money"]
