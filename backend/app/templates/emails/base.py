from __future__ import annotations

import re
from html import escape as html_escape

# Campaign session 4 (§A.4): the layout is built for the mail clients customers use, not only
# for browsers. Gmail drops <style> in some of its apps, Outlook for Windows draws with Word (no
# max-width, no padding on links), and dark-mode clients recolour mail that does not say it
# supports dark. So: a viewport and a declared colour scheme with dark colours, a 600px table
# column that Outlook keeps (inside a conditional comment only Outlook reads), styles inline on
# every element as well as in <style>, and buttons drawn as a coloured table cell (see
# _bulletproof_buttons). Servabo's blue is the app's (#1555e0, white text 6.2:1).
_BRAND_BLUE = "#1555e0"
_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

_LAYOUT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light dark">
    <meta name="supported-color-schemes" content="light dark">
    <title>{title}</title>
    <style>
        body {{ margin: 0; padding: 0; background-color: #f9f9f9; }}
        .container {{ max-width: 600px; background: #ffffff; border: 1px solid #e1e1e1; border-radius: 8px; }}
        .content p {{ margin: 0 0 16px 0; }}
        @media only screen and (max-width: 620px) {{
            .inner {{ padding: 24px !important; }}
        }}
        @media (prefers-color-scheme: dark) {{
            body, .email-bg {{ background-color: #111418 !important; }}
            .container {{ background-color: #1b1f24 !important; border-color: #30363d !important; }}
            .inner, .content, .content p, .content td, .content li {{ color: #e6e6e6 !important; }}
            .footer, .footer p {{ color: #a8b0b8 !important; border-color: #30363d !important; }}
            .logo {{ color: #8ab4f8 !important; }}
            .content a:not(.button) {{ color: #8ab4f8 !important; }}
        }}
    </style>
</head>
<body style="margin:0;padding:0;background-color:#f9f9f9;">
<table role="presentation" class="email-bg" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f9f9f9;">
<tr><td align="center" style="padding:40px 12px;">
<!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" align="center"><tr><td><![endif]-->
<table role="presentation" class="container" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background-color:#ffffff;border:1px solid #e1e1e1;border-radius:8px;">
<tr><td class="inner" style="padding:40px;font-family:FONT;font-size:15px;line-height:1.6;color:#333333;">
    <div class="header" style="text-align:center;margin-bottom:30px;">
        {brand_header}
    </div>
    <div class="content" style="margin-bottom:30px;">
        {content_html}
    </div>
    <div class="footer" style="font-size:12px;color:#666666;text-align:center;border-top:1px solid #eeeeee;padding-top:20px;margin-top:30px;">
        <p style="margin:0;">{footer_text}</p>{legal_links}
    </div>
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr>
</table>
</body>
</html>
""".replace("FONT", _FONT).replace("BLUE", _BRAND_BLUE)

_BUTTON = re.compile(
    r'<div class="button-container">\s*(<a\b(?P<attrs>[^>]*\bclass="button"[^>]*)>(?P<label>.*?)</a>)\s*</div>',
    re.DOTALL,
)
_BUTTON_STYLE = (
    f"display:inline-block;padding:12px 24px;font-family:{_FONT};font-size:15px;font-weight:600;"
    "line-height:1.2;color:#ffffff;text-decoration:none;border-radius:4px;"
)


def _bulletproof_buttons(html: str) -> str:
    """Draw each `<div class="button-container"><a class="button">` as a coloured table cell."""

    def one(match: re.Match[str]) -> str:
        attrs = re.sub(r'\sstyle="[^"]*"', "", match.group("attrs"))
        return (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" '
            'style="margin:35px auto;"><tr>'
            f'<td align="center" bgcolor="{_BRAND_BLUE}" style="border-radius:4px;background-color:{_BRAND_BLUE};">'
            f'<a{attrs} style="{_BUTTON_STYLE}">{match.group("label")}</a>'
            "</td></tr></table>"
        )

    return _BUTTON.sub(one, html)


_WORDMARK = '<span class="logo" style="font-size:24px;font-weight:bold;color:{color};vertical-align:middle;">{name}</span>'


def _brand_header(brand_name: str) -> str:
    """The email's header: the Servabo mark and name, or a white-label brand's name alone.

    The mark is drawn in HTML (a blue rounded tile with a white "S", the app's mark at email
    size), not loaded as an image: mail clients hold remote images until the reader allows them,
    and an email that must carry no web address (a revocation notice, a completed transfer)
    stays that way. Outlook for Windows ignores the rounded corners and shows a square tile.
    An organization sending under its own brand never gets Servabo's mark in its mail.
    """
    from app.core.config import settings

    name_html = _WORDMARK.format(color=_BRAND_BLUE, name=brand_name)
    if brand_name != html_escape(str(settings.PROJECT_NAME)):
        return name_html
    tile = (
        f'<span class="mark" aria-hidden="true" style="display:inline-block;width:30px;height:30px;'
        f"border-radius:8px;background-color:{_BRAND_BLUE};color:#ffffff;font-family:{_FONT};"
        'font-size:19px;font-weight:700;line-height:30px;text-align:center;vertical-align:middle;'
        'margin-right:10px;">S</span>'
    )
    return tile + name_html


def _legal_links(brand_name: str, content_html: str) -> str:
    """The footer's links to Servabo's terms, privacy policy and refund policy (website session, B7).

    Only in Servabo's own mail (never under an organization's brand), and only in an email that
    already carries a web link: a revocation notice or a completed transfer stays free of
    addresses, as the security emails promise.
    """
    from app.core.config import settings

    if brand_name != html_escape(str(settings.PROJECT_NAME)) or 'href="http' not in content_html:
        return ""
    site = settings.WEBSITE_URL
    links = " &middot; ".join(
        f'<a href="{site}/legal/{path}" style="color:#666666;text-decoration:underline;">{label}</a>'
        for path, label in (("terms", "Terms"), ("privacy", "Privacy"), ("refunds", "Refunds"))
    )
    return f'\n        <p class="legal" style="margin:8px 0 0 0;">{links}</p>'


class _EmailLayout(str):
    """The layout string; `format` also draws its buttons for every mail client."""

    def format(self, *args: object, **kwargs: object) -> str:  # type: ignore[override]
        if "brand_header" not in kwargs:
            kwargs["brand_header"] = _brand_header(str(kwargs.get("brand_name", "")))
        if "legal_links" not in kwargs:
            kwargs["legal_links"] = _legal_links(str(kwargs.get("brand_name", "")), str(kwargs.get("content_html", "")))
        return _bulletproof_buttons(super().format(*args, **kwargs))


# Standard base HTML responsive layout for all outbound transactional emails
BASE_EMAIL_HTML_LAYOUT = _EmailLayout(_LAYOUT)
