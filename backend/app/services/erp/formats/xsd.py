"""ARCH47-S1:xsd — compile the vendored published schemas (schemas/SOURCES.md) once, offline.

The vendored UBL copies import a few namespaces without a schemaLocation (their
redistributor resolves them with a catalog). A resolver supplies the location
IN MEMORY while lxml loads each file; the files on disk are never changed and
nothing is ever fetched: a URL outside the schema directory is refused.
"""

from __future__ import annotations

import functools
import os
import re
import threading
from pathlib import Path
from typing import Optional

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"

_NAMESPACE_FILES = {
    "urn:un:unece:uncefact:data:specification:CoreComponentTypeSchemaModule:2": "CCTS_CCT_SchemaModule.xsd",
    "http://www.w3.org/2000/09/xmldsig#": "xmldsig-core-schema.xsd",
    "http://uri.etsi.org/01903/v1.3.2#": "XAdES01903v132-201601.xsd",
    "http://uri.etsi.org/01903/v1.4.1#": "XAdES01903v141-201601.xsd",
}
_BARE_IMPORT = re.compile(r'<xsd?:import\s+namespace\s*=\s*"([^"]+)"\s*/>')
_LOCK = threading.Lock()


class SchemaError(RuntimeError):
    pass


def _patched(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8")
    common = SCHEMAS / "ubl21" / "common"

    def fix(m: re.Match) -> str:
        name = _NAMESPACE_FILES.get(m.group(1))
        if name is None:
            return m.group(0)
        rel = Path(name) if path.parent == common else Path("..") / "common" / name
        prefix = m.group(0).split(":import")[0]  # "<xsd" or "<xs"
        return f'{prefix}:import namespace="{m.group(1)}" schemaLocation="{rel.as_posix()}"/>'

    if SCHEMAS / "ubl21" in path.parents:
        text = _BARE_IMPORT.sub(fix, text)
    return text.encode("utf-8")


def local_path(url: str, *, windows: Optional[bool] = None) -> str:
    """ARCH47-S1:file-url. The file a schema import names. lxml hands the resolver an absolute `file:` URL built
    from the including file's base URL -- percent-encoded ("sp%20ace", "%23") and, on Windows, with the drive
    after a slash (file:///C:/Users/...). `url2pathname` is the standard library's inverse of
    `Path.as_uri()` for the platform (`nturl2path` on Windows): it decodes the escapes and drops the slash
    before the drive. A URL naming another host (file://server/...) is refused -- a vendored schema is local."""
    import nturl2path
    from urllib.parse import unquote, urlsplit

    windows = (os.name == "nt") if windows is None else windows
    if not url.lower().startswith("file:"):
        return url
    parts = urlsplit(url)
    if parts.netloc not in ("", "localhost"):
        raise SchemaError(f"schema import from another host refused: {url}")
    if windows:
        return nturl2path.url2pathname(parts.path)
    return unquote(parts.path)


def _resolver():
    from app.services.erp.formats import xmlsafe  # ARCH47-S1:xmlsafe (ARCH-16 S1: lxml only in xmlsafe)

    root = SCHEMAS.resolve()

    class _Local(xmlsafe.Resolver):
        def resolve(self, url, pubid, context):  # noqa: ANN001
            try:
                resolved = Path(local_path(url)).resolve()
            except (OSError, ValueError):
                resolved = None
            if resolved is None or root not in resolved.parents or not resolved.is_file():
                raise SchemaError(f"schema import outside the vendored directory refused: {url}")
            return self.resolve_string(_patched(resolved), context, base_url=resolved.as_uri())

    return _Local()


@functools.lru_cache(maxsize=16)
def _compile(relative: str):
    from app.services.erp.formats import xmlsafe

    path = (SCHEMAS / relative).resolve()
    if not path.is_file():
        raise SchemaError(f"vendored schema missing: {relative}")
    doc = xmlsafe.parse_vendored(_patched(path), path=path, resolver=_resolver())
    return xmlsafe.compile_schema(doc)


def schema(relative: str):
    with _LOCK:
        return _compile(relative)


def validate(xml: bytes, relative: str) -> list[str]:
    """Problems (empty when valid) of an XML document against a vendored schema."""
    from app.services.erp.formats import xmlsafe

    try:
        doc = xmlsafe.parse(xml)
    except xmlsafe.XMLRefused as exc:
        return [str(exc)]
    xsd = schema(relative)
    if xsd.validate(doc):
        return []
    return [f"line {e.line}: {e.message}" for e in list(xsd.error_log)[:25]]


UBL_SCHEMAS = {"Invoice": "ubl21/maindoc/UBL-Invoice-2.1.xsd", "Order": "ubl21/maindoc/UBL-Order-2.1.xsd",
               "ReceiptAdvice": "ubl21/maindoc/UBL-ReceiptAdvice-2.1.xsd"}
SML = "ooxml/sml.xsd"
OPC_CONTENT_TYPES = "ooxml/opc/opc-contentTypes.xsd"
OPC_RELATIONSHIPS = "ooxml/opc/opc-relationships.xsd"
TALLY = "tally/tally-voucher-import.xsd"


def validate_xlsx(data: bytes) -> list[str]:
    """Every part of an XLSX package against ECMA-376 (content types, relationships, workbook, sheet, styles)."""
    import io
    import zipfile

    problems: list[str] = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return [f"not a zip package: {exc}"]
    names = set(z.namelist())
    parts = {"[Content_Types].xml": OPC_CONTENT_TYPES, "_rels/.rels": OPC_RELATIONSHIPS,
             "xl/_rels/workbook.xml.rels": OPC_RELATIONSHIPS, "xl/workbook.xml": SML,
             "xl/worksheets/sheet1.xml": SML, "xl/styles.xml": SML}
    for name, xsd in parts.items():
        if name not in names:
            problems.append(f"{name}: missing")
            continue
        problems.extend(f"{name}: {p}" for p in validate(z.read(name), xsd))
    extra = names - set(parts)
    if extra:
        problems.append(f"unexpected parts: {sorted(extra)}")
    if not problems:
        problems.extend(_opc_structure(z, names))
    return problems


def _opc_structure(z, names: set[str]) -> list[str]:
    """ECMA-376 Part 2 (OPC) rules a schema cannot state: every part has a content type (an Override for its
    name or a Default for its extension), and every relationship target is a part of the package."""
    import posixpath

    from app.services.erp.formats import xmlsafe

    ct_ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    problems: list[str] = []
    try:
        ct = xmlsafe.parse(z.read("[Content_Types].xml"))
    except xmlsafe.XMLRefused as exc:
        return [f"[Content_Types].xml: {exc}"]
    overrides = {e.get("PartName", "").lstrip("/") for e in ct.iter(f"{ct_ns}Override")}
    defaults = {e.get("Extension", "").lower() for e in ct.iter(f"{ct_ns}Default")}
    for name in sorted(names - {"[Content_Types].xml"}):
        if name not in overrides and name.rsplit(".", 1)[-1].lower() not in defaults:
            problems.append(f"{name}: no content type ([Content_Types].xml)")
    missing_overrides = sorted(o for o in overrides if o not in names)
    if missing_overrides:
        problems.append(f"[Content_Types].xml names parts that are not in the package: {missing_overrides}")
    for rels, base in (("_rels/.rels", ""), ("xl/_rels/workbook.xml.rels", "xl")):
        try:
            rel_root = xmlsafe.parse(z.read(rels))
        except xmlsafe.XMLRefused as exc:
            problems.append(f"{rels}: {exc}")
            continue
        for e in rel_root.iter(f"{rel_ns}Relationship"):
            if e.get("TargetMode") == "External":
                problems.append(f"{rels}: an external relationship ({e.get('Target')})")
                continue
            target = e.get("Target", "")
            resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
            if resolved not in names:
                problems.append(f"{rels}: relationship {e.get('Id')} targets {target}, which is not in the package")
    return problems


def warm() -> Optional[str]:
    """Compile every runtime schema now (a deployment check); returns an error or None."""
    try:
        for rel in UBL_SCHEMAS.values():
            schema(rel)
        schema(TALLY)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


__all__ = ["SCHEMAS", "SchemaError", "SML", "TALLY", "UBL_SCHEMAS", "schema", "validate", "validate_xlsx", "warm"]
