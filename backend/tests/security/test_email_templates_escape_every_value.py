"""Every email template escapes every value it is given (campaign session 3, security review).

Names that land in emails come from people: an organization's name, an inviter's display name, a
workspace, an address. An organization admin can put `<a href=…>` or `<script>` into any of them.
Each template is rendered here with hostile text in every text argument; the HTML must not carry
the markup through, and subjects must stay on one line (a newline in a subject is a header
injection). New templates are picked up automatically.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import typing
from datetime import datetime, timedelta, timezone

import pytest

import app.templates.emails as emails_pkg

HOSTILE = '<script>alert(1)</script><a href="https://evil.example/x" onclick="steal()">x</a>'
HEADER_BREAK = "Acme\r\nBcc: victim@example.com"


def _renderers():
    found = []
    for module_info in pkgutil.iter_modules(emails_pkg.__path__):
        module = importlib.import_module(f"{emails_pkg.__name__}.{module_info.name}")
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("render_") and func.__module__ == module.__name__:
                found.append(func)
    return found


#: Arguments that are a fixed vocabulary, not text a person typed.
VOCABULARY = {"kind": "ENDING"}


def _row(cls, text: str):
    """A dataclass row (a workspace grant, an expired invitation) with hostile text in each field."""
    import dataclasses

    return cls(**{f.name: text for f in dataclasses.fields(cls)})


def _value_for(name: str, annotation, text: str):
    import dataclasses

    if name in VOCABULARY:
        return VOCABULARY[name]
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Literal:
        return args[0]
    if args and dataclasses.is_dataclass(args[0]):
        return [_row(args[0], text), _row(args[0], "Second")]
    if "Sequence[str]" in str(annotation) or "list[str]" in str(annotation):
        return [text, "Second Organization"]
    args = typing.get_args(annotation)
    if annotation in (str, "str") or (origin is typing.Union and str in args) or "str" in str(annotation):
        if "url" in name or "link" in name or name.endswith("_href"):
            return f"https://app.servabo.app/x?{text}"
        if "email" in name:
            return f"person+{text[:20]}@example.com"
        return text
    if annotation in (int, "int") or "int" in str(annotation):
        return 3
    if "datetime" in str(annotation):
        return datetime.now(timezone.utc) + timedelta(days=2)
    if "bool" in str(annotation):
        return True
    return None


def _call(func, text: str):
    signature = inspect.signature(func)
    hints = typing.get_type_hints(func)
    kwargs = {}
    for name, parameter in signature.parameters.items():
        annotation = hints.get(name, parameter.annotation)
        value = _value_for(name, annotation, text)
        if value is None:
            if parameter.default is not inspect.Parameter.empty:
                continue
            pytest.skip(f"{func.__name__}: cannot build {name} ({annotation})")
        kwargs[name] = value
    return func(**kwargs)


RENDERERS = [f for f in _renderers() if f.__name__ not in {"render_table", "render_grant_table", "render_grant_lines_text"}]


def test_every_email_template_is_found() -> None:
    assert len(RENDERERS) >= 15, [f.__name__ for f in RENDERERS]


@pytest.mark.parametrize("render", RENDERERS, ids=lambda f: f.__name__)
def test_markup_in_any_value_is_escaped(render) -> None:
    rendered = _call(render, HOSTILE)
    subject, html_body = rendered[0], rendered[1]
    assert "<script>" not in html_body
    assert 'onclick="steal()"' not in html_body
    assert '<a href="https://evil.example' not in html_body
    assert "\n" not in subject and "\r" not in subject


@pytest.mark.parametrize("render", RENDERERS, ids=lambda f: f.__name__)
def test_a_line_break_cannot_add_a_mail_header(render) -> None:
    subject = _call(render, HEADER_BREAK)[0]
    assert "\r" not in subject and "\n" not in subject
