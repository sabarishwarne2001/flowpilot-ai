"""Helm's emails: a price dispute to a supplier, a payment reminder to a customer, a request for a
supplier's renewed compliance document, and the Monday briefing to owners.

All on `BASE_EMAIL_HTML_LAYOUT` (session 4, F-380), every value escaped, amounts written the way an
explanation says them (lakh and crore for INR). Each returns (subject, html_body, text_body).
"""

from __future__ import annotations

from typing import Any, Sequence

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


def _wrap(subject: str, brand_name: str, content_html: str, footer: str) -> str:
    return BASE_EMAIL_HTML_LAYOUT.format(title=esc(subject), brand_name=esc(brand_name), content_html=content_html,
                                         footer_text=esc(footer))


def render_dispute(*, organization_name: str, supplier_name: str, invoice_number: str, currency: str,
                   lines: Sequence[dict[str, Any]], overcharge_micros: int) -> tuple[str, str, str]:
    subject = single_line(f"Invoice {invoice_number or ''}: billed above the agreed price".replace("  ", " "))
    rows_text = []
    rows_html = []
    for line in lines:
        agreed = money.say(int(line.get("agreed_micros") or 0), currency)
        billed = money.say(int(line.get("billed_micros") or 0), currency)
        po = f" (PO {line.get('po_number')})" if line.get("po_number") else ""
        rows_text.append(f"- {line.get('description') or 'Line'}{po}: agreed {agreed} a unit, billed {billed}")
        rows_html.append(f"<li>{esc(str(line.get('description') or 'Line'))}{esc(po)}: agreed {esc(agreed)} a unit, "
                         f"billed {esc(billed)}</li>")
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
               "Open the cockpit</a></div>")
    footer = (f"You receive the Monday briefing as an owner or admin of {organization_name}. Turn it off in your "
              "notification preferences.")
    return subject, _wrap(subject, brand_name, content, footer), text


__all__ = ["REMINDER_TONES", "render_briefing", "render_dispute", "render_document_request", "render_reminder"]
