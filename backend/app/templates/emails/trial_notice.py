"""The two free-trial emails (owner decision N-052): two days before the end, and when it ends.

Sent to each owner ACCOUNT once (the trial belongs to the account), naming the
organizations still on the trial, by `app.workers.handlers.trial` (`trial.reminders`).

Both carry one link, to the plan page of the first organization, where an owner
subscribes. Neither says the data is at risk: nothing is deleted when a trial ends.

Pure function: no Session, no model import, no clock. See common.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from app.templates.emails.base import BASE_EMAIL_HTML_LAYOUT
from app.templates.emails.common import esc, esc_attr, format_timestamp, single_line


def _organization_list(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def render_trial_notice(
    *,
    kind: str,
    organization_names: Sequence[str],
    ends_at: datetime,
    plans_link: str,
    brand_name: str,
) -> tuple[str, str, str]:
    """Render `(subject, html, text)`. `kind` is "ENDING" (two days before) or "ENDED"."""
    if kind not in ("ENDING", "ENDED"):
        raise ValueError(f"unknown trial notice {kind!r}")
    organizations = _organization_list([single_line(name) for name in organization_names])
    when = format_timestamp(ends_at)

    if kind == "ENDING":
        subject = single_line(f"Your {brand_name} trial ends on {when}")
        lead = (
            f"Your free trial of {brand_name} for {organizations} ends on {when}. "
            "Choose a plan before then to keep uploading documents, asking the assistant "
            "and running automations without a pause."
        )
        after = (
            "If you do not subscribe, nothing is deleted: everyone can still sign in, read, "
            "search and export, and subscribing later turns everything back on at once."
        )
        button = "Choose a plan"
    else:
        subject = single_line(f"Your {brand_name} trial has ended")
        lead = (
            f"Your free trial of {brand_name} for {organizations} ended on {when}. "
            "New uploads, the assistant and automations are paused until you choose a plan."
        )
        after = (
            "Nothing has been deleted. Everyone can still sign in, read, search and export, "
            "and subscribing turns everything back on at once."
        )
        button = "Subscribe"

    text_body = f"Hello,\n\n{lead}\n\n{after}\n\n{button}: {plans_link}\n"
    content_html = f"""<p>Hello,</p>
            <p>{esc(lead)}</p>
            <p>{esc(after)}</p>
            <div class="button-container">
                <a class="button" href="{esc_attr(plans_link)}">{esc(button)}</a>
            </div>"""
    footer_text = (
        f"{brand_name} sent this because you own an organization on the free trial."
    )
    html_body = BASE_EMAIL_HTML_LAYOUT.format(
        title=esc(subject),
        brand_name=esc(brand_name),
        content_html=content_html,
        footer_text=esc(footer_text),
    )
    return subject, html_body, text_body


__all__ = ["render_trial_notice"]
