"""ARCH47-S1:xmlsafe — the ONE module of the ERP code that imports lxml.

ARCH-16 confines XML parsing (`scripts/verify_arch16.py` S1): an XML parser
left at its defaults can be made to read local files or the network through an
external entity (XXE) or to exhaust memory through nested entities ("billion
laughs"). ERP postings read XML that comes from outside — Tally's import
RESPONSE, `.ack` files, a response file a person pastes — so every parse goes
through here:

  parse(data)             untrusted input. A document that declares a DOCTYPE
                          or an ENTITY is refused before lxml sees it; the parser
                          itself resolves no entities, loads no DTD, touches no
                          network and refuses huge trees. Raises XMLRefused.
  parse_vendored(...)     a schema file under app/services/erp/schemas/ (the
                          OASIS / W3C / ECMA-376 files, some of which carry an
                          internal DTD subset); refused for any other path.
                          Entities are still not resolved and nothing is fetched:
                          imports resolve only inside the vendored directory
                          (xsd._resolver).
  compile_schema(doc)     an XMLSchema from a vendored document.

Building documents (Element, SubElement, tostring) is not parsing and is
re-exported for the writers (ubl.py, tally.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from lxml import etree

SCHEMAS = (Path(__file__).resolve().parent.parent / "schemas").resolve()
#: no DOCTYPE / ENTITY declaration anywhere (case-insensitive, whitespace-tolerant, UTF-16 decoded first)
_DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
MAX_BYTES = 20 * 1024 * 1024

Element = etree.Element
SubElement = etree.SubElement
tostring = etree.tostring
Resolver = etree.Resolver
XMLSyntaxError = etree.XMLSyntaxError


class XMLRefused(ValueError):
    """Untrusted XML that is not well-formed, too large, or declares a DOCTYPE / ENTITY."""


def _hardened(*, allow_dtd_subset: bool = False) -> Any:
    # allow_dtd_subset only records the caller's intent: the parser settings are identical (a vendored
    # schema's internal subset is read, never loaded as a DTD, and its entities are never resolved).
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
                           huge_tree=False, recover=False)


def _as_bytes(data: Any) -> bytes:
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    raise XMLRefused(f"XML must be bytes or text, not {type(data).__name__}")


def _declares(raw: bytes) -> bool:
    if _DECLARATION.search(raw):
        return True
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00<" in raw[:64] or b"<\x00" in raw[:64]:
        for codec in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                if _DECLARATION.search(raw.decode(codec, errors="ignore").encode("utf-8")):
                    return True
            except LookupError:  # pragma: no cover
                continue
    return False


def parse(data: Any) -> Any:
    """The root element of UNTRUSTED XML, or XMLRefused."""
    raw = _as_bytes(data)
    if len(raw) > MAX_BYTES:
        raise XMLRefused(f"XML larger than {MAX_BYTES} bytes")
    if _declares(raw):
        raise XMLRefused("XML declaring a DOCTYPE or an ENTITY is refused (external and nested entities)")
    try:
        root = etree.fromstring(raw, _hardened())
    except etree.XMLSyntaxError as exc:
        raise XMLRefused(f"not well-formed XML: {exc}") from exc
    if root is None:
        raise XMLRefused("empty XML document")
    return root


def parse_vendored(data: bytes, *, path: Path, resolver: Optional[Any] = None) -> Any:
    """A vendored schema document (trusted: it ships in the repository); any other path is refused."""
    resolved = Path(path).resolve()
    if SCHEMAS not in resolved.parents:
        raise XMLRefused(f"not a vendored schema: {path}")
    parser = _hardened(allow_dtd_subset=True)
    if resolver is not None:
        parser.resolvers.add(resolver)
    return etree.fromstring(data, parser, base_url=resolved.as_uri())


def compile_schema(doc: Any) -> Any:
    return etree.XMLSchema(etree.ElementTree(doc))


__all__ = ["Element", "MAX_BYTES", "Resolver", "SCHEMAS", "SubElement", "XMLRefused", "XMLSyntaxError",
           "compile_schema", "parse", "parse_vendored", "tostring"]
