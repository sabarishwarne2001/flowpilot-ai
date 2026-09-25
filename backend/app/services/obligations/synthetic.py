"""ARCH46-S1:synthetic — documents with planted obligations and exact truth. Pure; seeded.

Every document is a real PDF (pikepdf, standard Courier, ARCH-45's Writer)
read back through the SAME text-layer reader the OCR pipeline uses for
digital pages, so the extractor sees what a processed upload gives it: line
blocks with 200-DPI boxes, running headers and "Page i of n" footers, clauses
broken across pages.

The truth is computed INDEPENDENTLY of app/services/obligations/temporal.py:
months and years with dateutil.relativedelta (which clamps to the month end,
the same stated rule), business days with a plain loop, RRULE series with
dateutil.rrule. A disagreement between the two is a failed gate, not a
shared bug.

Families (each a single document):

  msa        an auto-renewing services agreement: RENEWAL (series), NOTICE
             (non-renewal, days / months / business days, "end of the term" or
             "renewal date"), a monthly fee (PAYMENT series), a quarterly or
             monthly report (REPORTING series with an offset), a deliverable
             (DELIVERY, fixed). Distractors: a post-termination return
             period, termination for convenience on notice, a survival period,
             late-payment interest, the signing date.
  fixed      a fixed-term agreement: EXPIRY (start + term - 1 day), a delivery
             within N days of the Effective Date (OFFSET), an annual payment
             ("annually on 1 April").
  lease      a lease that expires on a stated date with a renewal-option notice
             in MONTHS (month-end arithmetic: 31 December - 3 months =
             30 September), rent on the first day of each month.
  policy     an insurance schedule with "Period of Insurance: <from> to <to>"
             (a field line; DMY decided by a date like 31/03), and a monthly
             claims report within N business days after each month end.
  invoice    extracted fields: invoice date + "Net N" terms, or a due date.

Held-out sets vary names, dates (leap days and month ends included), number
words, units, date formats, line widths, clause order and which distractors
appear.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrulestr

from app.services.corroboration.synthetic import Writer, _doc

REFERENCE = date(2026, 9, 25)

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])
    head = f"{_ONES[n // 100]} hundred"
    return head if n % 100 == 0 else f"{head} and {words(n % 100)}"


def both(n: int) -> str:
    """'sixty (60)'."""
    return f"{words(n)} ({n})"


def named(d: date) -> str:
    return f"{d.day} {d.strftime('%B')} {d.year}"


def us_named(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def iso(d: date) -> str:
    return d.isoformat()


@dataclass(frozen=True)
class Expected:
    kind: str
    due: date
    rrule: Optional[str] = None
    pending: bool = False
    note: str = ""


@dataclass
class ObDoc:
    name: str
    doc: object          # corroboration.synthetic.SynDoc
    expected: list[Expected]
    fields: dict = field(default_factory=dict)
    parties: list[tuple[str, str]] = field(default_factory=list)   # (role, name)
    reference: date = REFERENCE
    holidays: tuple = ()
    weekend: tuple = (6, 7)


# ---------------------------------------------------------------------------
# the independent truth
# ---------------------------------------------------------------------------


def t_add(d: date, n: int, unit: str) -> date:
    if unit == "DAY":
        return d + timedelta(days=n)
    if unit == "WEEK":
        return d + timedelta(weeks=n)
    if unit == "MONTH":
        return d + relativedelta(months=n)
    if unit == "YEAR":
        return d + relativedelta(years=n)
    cur, step, left = d, (1 if n >= 0 else -1), abs(n)
    while left:
        cur += timedelta(days=step)
        if cur.isoweekday() < 6:
            left -= 1
    return cur


def t_preceding(d: date, holidays: tuple = ()) -> date:
    while d.isoweekday() >= 6 or d in holidays:
        d -= timedelta(days=1)
    return d


def t_following(d: date, holidays: tuple = ()) -> date:
    while d.isoweekday() >= 6 or d in holidays:
        d += timedelta(days=1)
    return d


def t_series_first(rule: str, start: date, on_or_after: date, offset: Optional[tuple[int, str]] = None,
                   plain_months: Optional[int] = None) -> date:
    """The first due date on or after `on_or_after`: base dates from dateutil (or anchored months for a
    plain rule), then the offset."""
    k = 0
    while True:
        if plain_months is not None:
            base = start + relativedelta(months=k * plain_months)
        else:
            base = None
            rr = rrulestr(rule, dtstart=__import__("datetime").datetime(start.year, start.month, start.day))
            for i, moment in enumerate(rr):
                if i == k:
                    base = moment.date()
                    break
            assert base is not None
        due = t_add(base, offset[0], offset[1]) if offset else base
        if due >= on_or_after:
            return due
        k += 1
        if k > 2000:
            raise AssertionError("no occurrence")


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------

CLIENTS = ("Globex Manufacturing Ltd", "Initech Components Pvt Ltd", "Umbrella Pharma Ltd", "Stark Logistics LLP",
           "Wayne Retail Pvt Ltd", "Tyrell Textiles Ltd")
PROVIDERS = ("Acme Technology Services Pvt Ltd", "Hooli Cloud Solutions Ltd", "Vandelay Industries Pvt Ltd",
             "Soylent Data Systems Ltd", "Cyberdyne Analytics LLP", "Massive Dynamic Services Ltd")
DELIVERABLES = ("Migration Plan", "Security Assessment Report", "Data Inventory", "Disaster Recovery Runbook",
                "Training Materials", "Network Design Document")
REPORTS = ("service level report", "compliance certificate", "usage statement", "incident summary report")


def _writer(rng: random.Random, header: str) -> Writer:
    return Writer(header, chars=rng.choice((72, 78, 84)), size=9.5)


def _para(w: Writer, number: int, heading: str, text: str, style: int) -> None:
    if style == 0:
        w.para(f"{number}. {heading}. {text}")
    else:
        w.line(f"{number}. {heading.upper()}", bold=True)
        w.para(text)


def msa(seed: int, *, reference: date = REFERENCE) -> ObDoc:
    rng = random.Random(seed)
    client, provider = rng.choice(CLIENTS), rng.choice(PROVIDERS)
    start = date(rng.choice((2024, 2025, 2026)), rng.randint(1, 12), 1) + timedelta(days=rng.randint(0, 27))
    if rng.random() < 0.15:
        start = rng.choice((date(2024, 2, 29), date(2025, 1, 31), date(2024, 8, 31)))
    term_months = rng.choice((12, 24, 36, 6))
    renew_months = rng.choice((12, 12, 24, term_months))
    notice_n, notice_unit, notice_word = rng.choice(((60, "DAY", "days"), (90, "DAY", "days"), (30, "DAY", "days"),
                                                     (3, "MONTH", "months"), (1, "MONTH", "month"),
                                                     (45, "BUSINESS_DAY", "Business Days")))
    anchor_phrase, shift = rng.choice((("before the end of the then-current term", -1),
                                       ("prior to the expiry of the then-current term", -1),
                                       ("before the renewal date", 0)))
    fee_day = rng.choice((1, 5, 7, 10, 15))
    fee = rng.choice((125000, 250000, 480000, 90000))
    report_per, report_rule = rng.choice((("calendar quarter", "FREQ=YEARLY;BYMONTH=3,6,9,12;BYMONTHDAY=-1"),
                                          ("month", "FREQ=MONTHLY;BYMONTHDAY=-1")))
    report_days = rng.choice((5, 10, 15, 20))
    report = rng.choice(REPORTS)
    deliverable = rng.choice(DELIVERABLES)
    deliver_on = start + timedelta(days=rng.randint(30, 200))
    fmt = rng.choice((named, named, us_named, iso))
    style = rng.randint(0, 1)
    term_words = (f"{both(term_months // 12)} year{'s' if term_months // 12 > 1 else ''}"
                  if term_months % 12 == 0 and rng.random() < 0.5 else f"{both(term_months)} months")
    renew_words = (f"{both(renew_months // 12)} year{'s' if renew_months // 12 > 1 else ''}"
                   if renew_months % 12 == 0 and rng.random() < 0.5 else f"{both(renew_months)} months")
    w = _writer(rng, f"Master Services Agreement - {client} / {provider}")
    w.title("MASTER SERVICES AGREEMENT")
    signed = start - timedelta(days=rng.randint(1, 20))
    w.para(f"This Master Services Agreement is made on {fmt(signed)} between {client} (the Client) and {provider} "
           f"(the Provider).")
    renew_unit = ("year" if renew_words.split(")")[-1].strip().startswith("year") else "month")
    renew_n_words = renew_words.split(")")[0] + ")"
    term_text = rng.choice((
        f"This Agreement commences on {fmt(start)} (the \"Effective Date\") and continues for an initial term of "
        f"{term_words}. It shall automatically renew for successive periods of {renew_words} unless either "
        f"party gives written notice of non-renewal at least {both(notice_n)} {notice_word} {anchor_phrase}.",
        f"This Agreement shall take effect on {fmt(start)} (the \"Effective Date\"). The initial term is {term_words}. "
        f"Thereafter this Agreement will renew automatically for consecutive {renew_n_words} {renew_unit} periods, "
        f"unless a party notifies the other in writing not less than {both(notice_n)} {notice_word} {anchor_phrase}.",
        f"With effect from {fmt(start)} (the \"Effective Date\") this Agreement remains in force for a term of "
        f"{term_words} and shall be renewed for further periods of {renew_words} each, unless terminated by written "
        f"notice given no later than {both(notice_n)} {notice_word} {anchor_phrase}.",
    ))
    fee_text = rng.choice((
        f"The Client shall pay the Provider a monthly fee of INR {fee:,} on or before the "
        f"{fee_day}{_suffix(fee_day)} day of each calendar month.",
        f"A monthly fee of INR {fee:,} is payable by the Client on the {fee_day}{_suffix(fee_day)} of every month.",
        f"The Client shall pay INR {fee:,} per month, due by the {fee_day}{_suffix(fee_day)} day of each month.",
    ))
    report_text = rng.choice((
        f"The Provider shall submit a {report} within {both(report_days)} days after the end of each {report_per}.",
        f"Within {both(report_days)} days following the end of each {report_per}, the Provider shall furnish a {report}.",
    ))
    deliver_text = rng.choice((
        f"The Provider shall deliver the {deliverable} on or before {fmt(deliver_on)}.",
        f"The Provider shall deliver the {deliverable} to the Client no later than {fmt(deliver_on)}.",
        f"The {deliverable} shall be delivered by the Provider by {fmt(deliver_on)}.",
    ))
    clauses = [("Term", term_text), ("Fees", fee_text), ("Reporting", report_text), ("Deliverables", deliver_text)]
    distractors = [
        ("Return of Materials", f"Within {both(rng.choice((15, 30)))} days after termination the Provider shall return "
                                "all materials of the Client."),
        ("Termination for Convenience", f"Either party may terminate this Agreement on {both(rng.choice((30, 90)))} "
                                        "days' written notice to the other party."),
        ("Confidentiality", f"The obligations in this clause survive for {both(3)} years after termination."),
        ("Late Payment", "Overdue amounts shall bear interest at 1.5% per month until paid."),
    ]
    body = clauses + [d for d in distractors if rng.random() < 0.8]
    rng.shuffle(body)
    for i, (h, text) in enumerate(body, start=1):
        _para(w, i, h, text, style)
    w.para(f"Signed for and on behalf of {client} and {provider}.")
    doc = _doc(f"msa-{seed}", f"MSA-{seed}.pdf", w, {})
    # truth
    renewal_start = start + relativedelta(months=term_months)
    renew_rule = ("FREQ=YEARLY" + (f";INTERVAL={renew_months // 12}" if renew_months // 12 > 1 else "")
                  if renew_months % 12 == 0 else f"FREQ=MONTHLY;INTERVAL={renew_months}")
    renewal_due = t_series_first(renew_rule, renewal_start, reference, plain_months=renew_months)
    notice_anchor = renewal_due + timedelta(days=shift)
    notice_due = t_preceding(t_add(notice_anchor, -notice_n, notice_unit))
    expected = [
        Expected("RENEWAL", renewal_due, renew_rule),
        Expected("NOTICE", notice_due, pending=notice_unit == "BUSINESS_DAY"),
        Expected("PAYMENT", t_series_first(f"FREQ=MONTHLY;BYMONTHDAY={fee_day}", start, reference),
                 f"FREQ=MONTHLY;BYMONTHDAY={fee_day}"),
        Expected("REPORTING", t_series_first(report_rule, start, reference, (report_days, "DAY")), report_rule),
        Expected("DELIVERY", deliver_on),
    ]
    return ObDoc(f"msa-{seed}", doc, expected, parties=[("vendor", provider), ("customer", client)], reference=reference)


def _suffix(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def fixed(seed: int, *, reference: date = REFERENCE) -> ObDoc:
    rng = random.Random(seed)
    client, provider = rng.choice(CLIENTS), rng.choice(PROVIDERS)
    start = date(rng.choice((2025, 2026)), rng.randint(1, 12), 1) + timedelta(days=rng.randint(0, 27))
    if rng.random() < 0.2:
        start = rng.choice((date(2026, 1, 31), date(2028, 2, 29), date(2026, 3, 31)))
    term_months = rng.choice((12, 18, 24, 36))
    within = rng.choice((14, 21, 30, 45))
    pay_day, pay_month = rng.choice(((1, 4), (15, 7), (31, 12), (30, 6)))
    deliverable = rng.choice(DELIVERABLES)
    fmt = rng.choice((named, us_named))
    w = _writer(rng, f"Services Agreement - {provider}")
    w.title("FIXED TERM SERVICES AGREEMENT")
    w.para(f"This Agreement is entered into between {client} and {provider}.")
    month_name = date(2024, pay_month, 1).strftime('%B')
    body = [
        ("Duration", rng.choice((
            f"This Agreement shall commence on {fmt(start)} (the \"Effective Date\") and shall remain in force "
            f"for a period of {both(term_months)} months, unless terminated earlier.",
            f"This Agreement is effective from {fmt(start)} (the \"Effective Date\") and continues for a fixed term of "
            f"{both(term_months)} months.",
        ))),
        ("Initial Deliverable", rng.choice((
            f"The Provider shall deliver the {deliverable} within {both(within)} days of the Effective Date.",
            f"The Provider shall deliver the {deliverable} to the Client within {both(within)} days from the "
            "Effective Date.",
        ))),
        ("Annual Licence Fee", rng.choice((
            f"The Client shall pay the annual licence fee of USD {rng.choice((12000, 45000, 8000)):,} annually on "
            f"{pay_day} {month_name}.",
            f"The annual licence fee of USD {rng.choice((12000, 45000, 8000)):,} is payable on {pay_day} {month_name} "
            "of each year.",
        ))),
        ("Governing Law", "This Agreement is governed by the laws of India."),
        ("Return of Materials", "Within thirty (30) days after termination each party shall return the other's "
                                "materials."),
    ]
    rng.shuffle(body)
    for i, (h, text) in enumerate(body, start=1):
        _para(w, i, h, text, rng.randint(0, 1))
    doc = _doc(f"fixed-{seed}", f"Agreement-{seed}.pdf", w, {})
    expiry = start + relativedelta(months=term_months) - timedelta(days=1)
    pay_rule = f"FREQ=YEARLY;BYMONTH={pay_month};BYMONTHDAY={pay_day}"
    expected = [Expected("EXPIRY", expiry), Expected("DELIVERY", start + timedelta(days=within)),
                Expected("PAYMENT", t_series_first(pay_rule, start, reference), pay_rule)]
    return ObDoc(f"fixed-{seed}", doc, expected, parties=[("vendor", provider), ("customer", client)], reference=reference)


def lease(seed: int, *, reference: date = REFERENCE) -> ObDoc:
    rng = random.Random(seed)
    landlord, tenant = rng.choice(PROVIDERS), rng.choice(CLIENTS)
    expires = rng.choice((date(2027, 12, 31), date(2027, 5, 31), date(2028, 3, 31), date(2027, 8, 31),
                          date(2028, 2, 29)))
    months = rng.choice((3, 6, 2))
    rent_day = rng.choice((1, 5, 7))
    fmt = rng.choice((named, us_named))
    w = _writer(rng, f"Lease Deed - {tenant}")
    w.title("LEASE DEED")
    w.para(f"This Lease is made between {landlord} (the Lessor) and {tenant} (the Lessee) for the premises described in "
           "the Schedule.")
    body = [
        ("Lease Term", rng.choice((f"The lease expires on {fmt(expires)}.",
                                   f"The term of this lease ends on {fmt(expires)}.",
                                   f"This lease is valid until {fmt(expires)}."))),
        ("Renewal Option", rng.choice((
            f"The Lessee may renew this lease for a further term by giving not less than {both(months)} "
            f"months' written notice before expiry.",
            f"The Lessee may renew this lease by giving the Lessor at least {both(months)} months' prior written "
            "notice before the expiry date.",
        ))),
        ("Rent", f"The Lessee shall pay the monthly rent of INR {rng.choice((85000, 150000)):,} on or before the "
                 f"{rent_day}{_suffix(rent_day)} day of each month."),
        ("Security Deposit", "The deposit shall be refunded within thirty (30) days after termination."),
    ]
    rng.shuffle(body)
    for i, (h, text) in enumerate(body, start=1):
        _para(w, i, h, text, rng.randint(0, 1))
    doc = _doc(f"lease-{seed}", f"Lease-{seed}.pdf", w, {})
    notice = t_preceding(expires + relativedelta(months=-months))
    rent_rule = f"FREQ=MONTHLY;BYMONTHDAY={rent_day}"
    expected = [Expected("EXPIRY", expires), Expected("NOTICE", notice),
                Expected("PAYMENT", t_series_first(rent_rule, reference, reference), rent_rule)]
    return ObDoc(f"lease-{seed}", doc, expected, parties=[("landlord", landlord), ("tenant", tenant)],
                 reference=reference)


def policy(seed: int, *, reference: date = REFERENCE) -> ObDoc:
    rng = random.Random(seed)
    insured = rng.choice(CLIENTS)
    start = date(2026, rng.choice((1, 3, 4, 7, 10)), 1)
    end = start + relativedelta(years=1) - timedelta(days=1)
    k = rng.choice((5, 7, 10))
    w = _writer(rng, "Policy Schedule - Commercial Property")
    w.title("POLICY SCHEDULE")
    w.line(f"Insured: {insured}")
    w.line(f"Policy No: POL/{seed}/{rng.randint(100, 999)}")
    sep = rng.choice(("/", "."))
    w.line(rng.choice((f"Period of Insurance: {dmy(start)} to {dmy(end)}".replace("/", sep),
                       f"Period of Insurance: From {dmy(start)} To {dmy(end)}".replace("/", sep))))
    w.gap()
    w.para(f"1. Claims Reporting. The Insured shall submit a claims report within {both(k)} Business Days after the end "
           "of each month.")
    w.para("2. Cancellation. The Insurer may cancel this policy by giving fifteen (15) days' notice by registered post.")
    doc = _doc(f"policy-{seed}", f"Policy-{seed}.pdf", w, {})
    rule = "FREQ=MONTHLY;BYMONTHDAY=-1"
    expected = [Expected("EXPIRY", end),
                Expected("REPORTING", t_series_first(rule, start, reference, (k, "BUSINESS_DAY")), rule, pending=True)]
    return ObDoc(f"policy-{seed}", doc, expected, parties=[("insured", insured)], reference=reference)


def invoice(seed: int, *, reference: date = REFERENCE) -> ObDoc:
    rng = random.Random(seed)
    vendor = rng.choice(PROVIDERS)
    inv_date = date(2026, rng.randint(6, 12), rng.randint(1, 28))
    net = rng.choice((15, 30, 45, 60))
    use_due = rng.random() < 0.4
    w = _writer(rng, f"Tax Invoice - {vendor}")
    w.title("TAX INVOICE")
    w.line(f"Invoice No: INV-{seed}")
    w.line(f"Invoice Date: {named(inv_date)}")
    w.gap()
    w.para("Interest at 18% per annum is charged on late payment.")
    total = rng.choice((118000, 59000, 247800))
    fields = {"invoice_number": f"INV-{seed}", "invoice_date": inv_date.isoformat(), "total_amount": str(total),
              "currency": "INR", "vendor_name": vendor}
    if use_due:
        due = inv_date + timedelta(days=net)
        fields["due_date"] = named(due)
    else:
        fields["payment_terms"] = f"Net {net}"
        due = inv_date + timedelta(days=net)
    doc = _doc(f"invoice-{seed}", f"INV-{seed}.pdf", w, fields)
    return ObDoc(f"invoice-{seed}", doc, [Expected("PAYMENT", due)], fields=fields, parties=[("vendor", vendor)],
                 reference=reference)


def nda(seed: int, *, reference: date = REFERENCE) -> ObDoc:
    """No obligation with a date: every sentence is a distractor (precision)."""
    rng = random.Random(seed)
    a, b = rng.choice(CLIENTS), rng.choice(PROVIDERS)
    w = _writer(rng, f"Non-Disclosure Agreement - {a}")
    w.title("MUTUAL NON-DISCLOSURE AGREEMENT")
    w.para(f"This Agreement is dated {named(date(2026, rng.randint(1, 12), rng.randint(1, 28)))} between {a} and {b}.")
    body = [
        ("Confidentiality", f"Each party shall keep the other's information confidential for {both(rng.choice((2, 3, 5)))} "
                            "years after termination."),
        ("Termination", f"Either party may terminate this Agreement on {both(rng.choice((30, 60)))} days' written notice."),
        ("Return of Information", f"Within {both(rng.choice((10, 15)))} days after termination each party shall return "
                                  "or destroy the other's information."),
        ("Governing Law", "This Agreement is governed by the laws of India."),
    ]
    rng.shuffle(body)
    for i, (h, text) in enumerate(body, start=1):
        _para(w, i, h, text, rng.randint(0, 1))
    doc = _doc(f"nda-{seed}", f"NDA-{seed}.pdf", w, {})
    return ObDoc(f"nda-{seed}", doc, [], parties=[("party", b)], reference=reference)


def purchase_order(seed: int, *, reference: date = REFERENCE) -> ObDoc:
    """A delivery date as a field line; with or without a date that decides day-first."""
    rng = random.Random(seed)
    vendor = rng.choice(PROVIDERS)
    decided = rng.random() < 0.5
    order_date = date(2026, rng.randint(1, 12), rng.randint(13, 28) if decided else rng.randint(1, 12))
    delivery = date(2026, rng.randint(1, 12), rng.randint(1, 12))
    if delivery.day == delivery.month:
        delivery = delivery.replace(day=min(12, delivery.day % 12 + 1))
    w = _writer(rng, f"Purchase Order - {vendor}")
    w.title("PURCHASE ORDER")
    w.line(f"PO No: PO-{seed}")
    w.line(f"Order Date: {dmy(order_date)}")
    w.line(f"Delivery Date: {dmy(delivery)}")
    w.gap()
    w.para("Goods shall be supplied in accordance with the specifications attached.")
    doc = _doc(f"po-{seed}", f"PO-{seed}.pdf", w, {})
    ambiguous = not decided  # the delivery date itself is always day <= 12 and day != month
    return ObDoc(f"po-{seed}", doc, [Expected("DELIVERY", delivery, pending=ambiguous)], parties=[("vendor", vendor)],
                 reference=reference)


FAMILIES = {"msa": msa, "fixed": fixed, "lease": lease, "policy": policy, "invoice": invoice, "nda": nda,
            "po": purchase_order}


def golden() -> list[ObDoc]:
    """One of each family at fixed seeds, plus the calendar edge cases."""
    return [msa(4601), msa(4602), fixed(4603), lease(4604), policy(4605), invoice(4606), invoice(4607), nda(4608),
            purchase_order(4609), purchase_order(4610)]


def held_out(seeds) -> list[ObDoc]:
    out = []
    names = list(FAMILIES)
    for seed in seeds:
        out.append(FAMILIES[names[seed % len(names)]](seed))
    return out


__all__ = ["Expected", "FAMILIES", "ObDoc", "REFERENCE", "both", "fixed", "golden", "held_out", "invoice", "lease",
           "msa", "named", "nda", "policy", "purchase_order", "t_add", "t_following", "t_preceding", "t_series_first", "words"]
