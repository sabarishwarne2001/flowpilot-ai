"""Helm's emails: a price dispute to a supplier, a statement reconciliation to a supplier, a payment
reminder to a customer, a request for a supplier's renewed compliance document, and the Monday
briefing to owners.

All on `BASE_EMAIL_HTML_LAYOUT` (session 4, F-380), every value escaped, amounts written the way an
explanation says them (lakh and crore for INR). Each returns (subject, html_body, text_body).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from app.services.helm import money
from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import esc, esc_attr, single_line

#: Escalating tone for receivables reminders (level 1 friendly, 2 firm, 3 final).
REMINDER_TONES = {
    1: ("A friendly reminder", "This is a friendly reminder that the invoice below is now past its due date. "
        "If you have already paid, thank you, and please ignore this message."),
    2: ("Second reminder", "The invoice below is still unpaid and is now well past its due date. Please arrange "
        "payment, or tell us if there is a problem with the invoice."),
    3: ("Final reminder", "The invoice below remains unpaid despite earlier reminders. Please pay it now or contact "
        "us today so that we can resolve it."),
}


@dataclass(frozen=True)
class DisputeLine:
    """One invoice line billed above the agreed price, as the dispute email states it."""

    description: str
    po_number: Optional[str]
    agreed_micros: int
    billed_micros: int

    @classmethod
    def of(cls, raw: Any) -> "DisputeLine":
        if isinstance(raw, DisputeLine):
            found: Mapping[str, Any] = raw.__dict__
        else:
            found = raw if isinstance(raw, Mapping) else {"description": str(raw)}
        po = found.get("po_number")
        return cls(description=str(found.get("description") or "Line"), po_number=str(po) if po else None,
                   agreed_micros=_micros(found.get("agreed_micros")), billed_micros=_micros(found.get("billed_micros")))


def _micros(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _wrap(subject: str, brand_name: str, content_html: str, footer: str) -> str:
    return BASE_EMAIL_HTML_LAYOUT.format(title=esc(subject), brand_name=esc(brand_name), content_html=content_html,
                                         footer_text=esc(footer))


def render_dispute(*, organization_name: str, supplier_name: str, invoice_number: str, currency: str,
                   lines: Sequence[DisputeLine], overcharge_micros: int) -> tuple[str, str, str]:
    subject = single_line(f"Invoice {invoice_number or ''}: billed above the agreed price".replace("  ", " "))
    rows_text = []
    rows_html = []
    for line in (DisputeLine.of(raw) for raw in lines):
        agreed = money.say(line.agreed_micros, currency)
        billed = money.say(line.billed_micros, currency)
        po = f" (PO {line.po_number})" if line.po_number else ""
        rows_text.append(f"- {single_line(line.description)}{single_line(po)}: agreed {agreed} a unit, billed {billed}")
        rows_html.append(f"<li>{esc(line.description)}{esc(po)}: agreed {esc(agreed)} a unit, billed {esc(billed)}</li>")
    total = money.say(overcharge_micros, currency)
    text = (f"Dear {supplier_name},\n\nInvoice {invoice_number} bills these lines above the price agreed on the "
            f"purchase order:\n" + "\n".join(rows_text) + f"\n\nIn all, {total} more than agreed. Please send a "
            "corrected invoice or a credit note for the difference. We have put the invoice on hold until then.\n\n"
            f"Regards,\n{organization_name}\n")
    content = (f"<p>Dear {esc(supplier_name)},</p><p>Invoice <strong>{esc(invoice_number)}</strong> bills these lines "
               f"above the price agreed on the purchase order:</p><ul>{''.join(rows_html)}</ul>"
               f"<p>In all, <strong>{esc(total)}</strong> more than agreed. Please send a corrected invoice or a "
               "credit note for the difference. We have put the invoice on hold until then.</p>"
               f"<p>Regards,<br>{esc(organization_name)}</p>")
    return subject, _wrap(subject, organization_name, content,
                          f"Sent by {organization_name} about invoice {invoice_number}."), text


@dataclass(frozen=True)
class ReconciliationItem:
    """One difference between a supplier's statement and our records, as the email states it to them
    (`reconcile.Difference.as_json`: a code, both sides' amounts and the evidence)."""

    code: str
    their_micros: Optional[int]
    our_micros: Optional[int]
    on: Optional[str]
    number: Optional[str]
    reference: Optional[str]
    description: Optional[str]

    @classmethod
    def of(cls, raw: Any) -> "ReconciliationItem":
        found: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        evidence = [e for e in (found.get("evidence") or []) if isinstance(e, Mapping)]
        theirs = next((e for e in evidence if e.get("side") == "theirs"), {})
        ours = next((e for e in evidence if e.get("side") == "ours"), {})

        def text(value: Any) -> Optional[str]:
            return str(value).strip() or None if value not in (None, "") else None

        return cls(code=str(found.get("code") or ""), their_micros=_optional_micros(found.get("their_micros")),
                   our_micros=_optional_micros(found.get("our_micros")),
                   on=_readable_day(ours.get("paid_on") or ours.get("invoice_date") or theirs.get("on")),
                   number=text(ours.get("number") or theirs.get("reference")),
                   reference=text(ours.get("paid_reference") or theirs.get("reference")),
                   description=text(theirs.get("description")))

    def sentence(self, currency: str) -> Optional[str]:
        """The difference in the supplier's terms, or None for one the email does not state."""
        theirs, ours = money.say(self.their_micros, currency), money.say(self.our_micros, currency)
        on = f" of {self.on}" if self.on else ""
        number = self.number or "an invoice"
        if self.code == "NOT_IN_OUR_BOOKS":
            return (f"Your statement lists {self.reference or 'an invoice'}{on} for {theirs}, which we have not "
                    "received. Please send us a copy.")
        if self.code == "AMOUNT_DIFFERS":
            return f"Invoice {number}: your statement shows {theirs}; the invoice we received is for {ours}."
        if self.code == "NOT_ON_THEIR_STATEMENT":
            return (f"Our records include your invoice {number}{on} for {ours}, which is not on your statement. "
                    "Please confirm it.")
        if self.code == "PAYMENT_NOT_APPLIED":
            ref = f" (reference {self.reference})" if self.reference and self.reference != self.number else ""
            return (f"We paid {ours} against invoice {number} on {self.on or 'the date in our records'}{ref}; it is "
                    "not on your statement. Please apply it.")
        if self.code == "PAYMENT_AMOUNT_DIFFERS":
            return f"Our payment against invoice {number} was {ours}; your statement applies {theirs}."
        if self.code == "CREDIT_NOT_APPLIED":
            return f"Your credit of {ours} against invoice {number} is not on your statement."
        if self.code == "CREDIT_NOT_IN_OUR_BOOKS":
            ref = f" ({self.reference})" if self.reference else ""
            return (f"Your statement shows {theirs} received or credited{on}{ref}. Please tell us what it relates "
                    "to.")
        return None


def _readable_day(value: Any) -> Optional[str]:
    from datetime import date

    if not value:
        return None
    try:
        return money.day(date.fromisoformat(str(value)[:10]))
    except ValueError:
        return str(value)


def _optional_micros(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def render_reconciliation(*, organization_name: str, supplier_name: str, statement_date: str, currency: str,
                          their_balance_micros: Optional[int], our_balance_micros: Optional[int],
                          differences: Sequence[Any]) -> tuple[str, str, str]:
    """The reconciliation email for one supplier statement: each difference in their terms, then the
    balances, and a request to correct the statement or say where our records differ."""
    subject = single_line(f"Statement reconciliation: our account at {statement_date}")
    items = [ReconciliationItem.of(raw) for raw in differences]
    sentences = [s for s in (item.sentence(currency) for item in items) if s]
    balances = None
    if their_balance_micros is not None and our_balance_micros is not None \
            and their_balance_micros != our_balance_micros:
        balances = (f"Your statement shows a balance of {money.say(their_balance_micros, currency)} at "
                    f"{statement_date}; our records show {money.say(our_balance_micros, currency)}.")
    opening = (f"Thank you for your statement of account at {statement_date}. We have compared it with our "
               "records and found these differences:")
    closing = ("Please check these items and send us a corrected statement, or tell us where our records differ "
               "from yours.")
    text_lines = [f"- {single_line(s)}" for s in sentences]
    text = (f"Dear {supplier_name},\n\n{opening}\n" + "\n".join(text_lines)
            + (f"\n\n{balances}" if balances else "") + f"\n\n{closing}\n\nRegards,\n{organization_name}\n")
    content = (f"<p>Dear {esc(supplier_name)},</p><p>{esc(opening)}</p>"
               f"<ul>{''.join(f'<li>{esc(s)}</li>' for s in sentences)}</ul>"
               + (f"<p><strong>{esc(balances)}</strong></p>" if balances else "")
               + f"<p>{esc(closing)}</p><p>Regards,<br>{esc(organization_name)}</p>")
    return subject, _wrap(subject, organization_name, content,
                          f"Sent by {organization_name} about its account with {supplier_name}."), text


def render_reminder(*, organization_name: str, customer_name: str, invoice_number: str, currency: str,
                    amount_micros: int, due_date: str, days_overdue: int, level: int) -> tuple[str, str, str]:
    tone_title, tone = REMINDER_TONES.get(max(1, min(3, level)), REMINDER_TONES[1])
    amount = money.say(amount_micros, currency)
    subject = single_line(f"{tone_title}: invoice {invoice_number} ({amount}) is overdue")
    text = (f"Dear {customer_name},\n\n{tone}\n\nInvoice: {invoice_number}\nAmount: {amount}\nDue: {due_date} "
            f"({days_overdue} days ago)\n\nRegards,\n{organization_name}\n")
    content = (f"<p>Dear {esc(customer_name)},</p><p>{esc(tone)}</p>"
               "<table role=\"presentation\" cellpadding=\"4\" cellspacing=\"0\" border=\"0\">"
               f"<tr><td>Invoice</td><td><strong>{esc(invoice_number)}</strong></td></tr>"
               f"<tr><td>Amount</td><td><strong>{esc(amount)}</strong></td></tr>"
               f"<tr><td>Due</td><td>{esc(due_date)} ({days_overdue} days ago)</td></tr></table>"
               f"<p>Regards,<br>{esc(organization_name)}</p>")
    return subject, _wrap(subject, organization_name, content,
                          f"Sent by {organization_name} about invoice {invoice_number}."), text


def render_document_request(*, organization_name: str, supplier_name: str, document_title: str, valid_until: str,
                            upload_url: str) -> tuple[str, str, str]:
    subject = single_line(f"Please send your renewed {document_title}")
    text = (f"Dear {supplier_name},\n\nOur copy of your {document_title} is valid until {valid_until}. Please upload "
            f"the renewed document with this secure link (it works once):\n{upload_url}\n\nRegards,\n"
            f"{organization_name}\n")
    content = (f"<p>Dear {esc(supplier_name)},</p><p>Our copy of your <strong>{esc(document_title)}</strong> is valid "
               f"until <strong>{esc(valid_until)}</strong>. Please upload the renewed document with this secure link "
               "(it works once):</p><div class=\"button-container\"><a href=\""
               f"{esc_attr(upload_url)}\" class=\"button\">Upload the document</a></div>"
               f"<p>Regards,<br>{esc(organization_name)}</p>")
    return subject, _wrap(subject, organization_name, content,
                          f"Sent by {organization_name} to keep its supplier records current."), text


def render_briefing(*, brand_name: str, organization_name: str, book_name: str, week_of: str,
                    plan_lines: Sequence[str], risk_lines: Sequence[str], value_line: str, waiting: int,
                    cockpit_url: str, preferences_url: str) -> tuple[str, str, str]:
    subject = single_line(f"Helm briefing for {book_name}, week of {week_of}")
    plan_text = "\n".join(f"- {line}" for line in plan_lines) or "- Nothing to pay or chase this week."
    risk_text = "\n".join(f"- {line}" for line in risk_lines) or "- No risks Helm can see."
    text = (f"Good morning,\n\nThis week's plan for {book_name}:\n{plan_text}\n\nRisks:\n{risk_text}\n\n"
            f"Value held: {value_line}\nActions waiting for approval: {waiting}\n\nOpen the cockpit: {cockpit_url}\n\n"
            f"Turn this briefing off: {preferences_url}\n")
    plan_html = "".join(f"<li>{esc(line)}</li>" for line in plan_lines) or "<li>Nothing to pay or chase this week.</li>"
    risk_html = "".join(f"<li>{esc(line)}</li>" for line in risk_lines) or "<li>No risks Helm can see.</li>"
    content = (f"<p>Good morning,</p><p>This week's plan for <strong>{esc(book_name)}</strong>:</p><ul>{plan_html}</ul>"
               f"<p><strong>Risks</strong></p><ul>{risk_html}</ul>"
               f"<p><strong>Value held:</strong> {esc(value_line)}<br><strong>Waiting for approval:</strong> "
               f"{waiting}</p><div class=\"button-container\"><a href=\"{esc_attr(cockpit_url)}\" class=\"button\">"
               "Open the cockpit</a></div>"
               f"<p><a href=\"{esc_attr(preferences_url)}\">Turn this briefing off</a></p>")
    footer = (f"You receive the Monday briefing as an owner or admin of {organization_name}. Turn it off in your "
              "notification preferences.")
    return subject, _wrap(subject, brand_name, content, footer), text


__all__ = ["DisputeLine", "REMINDER_TONES", "ReconciliationItem", "render_briefing", "render_dispute",
           "render_document_request", "render_reconciliation", "render_reminder"]
