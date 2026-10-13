"""An alert to whoever runs the deployment (campaign session 4, §C.4).

Not a customer email: it goes to `OPERATOR_ALERT_EMAILS` through the platform relay. The subject
and body are written by `operator_alert_service` and can carry text that came from elsewhere (a
job's last error, an organization's name), so everything is escaped and the subject is one line.

Pure function: no Session, no model import, no clock. See common.py.
"""

from __future__ import annotations

from datetime import datetime

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import esc, esc_attr, single_line


def _utc(moment: datetime) -> str:
    return moment.strftime("%d %b %Y, %H:%M UTC")


def render_operator_alert(
    *,
    subject: str,
    body: str,
    occurrences: int,
    first_seen_at: datetime,
    last_seen_at: datetime,
    console_url: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """Render `(subject, html, text)` for one alert."""
    line = single_line(f"[{brand_name} alert] {subject}")
    when = (
        f"First seen {_utc(first_seen_at)}; latest {_utc(last_seen_at)}; count {occurrences}."
    )
    footer = (
        f"You get this because your address is in OPERATOR_ALERT_EMAILS on this {brand_name} "
        "deployment. The same alert repeats at most once an hour while it keeps happening."
    )
    text_body = "\n\n".join([body, when, f"Background jobs: {console_url}", footer]) + "\n"
    content_html = f"""<p><strong>{esc(subject)}</strong></p>
            <p>{esc(body)}</p>
            <p style="font-size:13px;color:#4b5563">{esc(when)}</p>
            <div class="button-container">
                <a class="button" href="{esc_attr(console_url)}">Open background jobs</a>
            </div>
            <p style="font-size:12px;color:#6b7280">{esc(footer)}</p>"""
    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=esc(line),
        brand_name=esc(brand_name),
        content_html=content_html,
        footer_text=esc(f"{brand_name} operator alert."),
    )
    return line, html_body, text_body
