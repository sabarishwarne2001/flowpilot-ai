"""ARCH45-S1:report — the discrepancy matrix as a PDF report, CSV and JSON.

The PDF is written with pikepdf (already a dependency: packets and redaction
use it) and the standard Helvetica fonts, laid out with Helvetica's published
AFM advance widths (below). No new dependency, nothing embedded, nothing
fetched. reportlab happens to be pinned in requirements.txt but nothing in the
application imports it and one test treats it as optional, so the report does
not depend on it.

Contents: the documents (in the order the user chose), the summary by severity
and layer, the agreement between every pair of documents, the rules and each
document's verdict, then every discrepancy -- material ones first -- with each
document's value, the page it is on, a word diff against the first document
for changed clauses, and the reviewer's decision. The footer carries the run
fingerprint, so a printed report can be matched to the run that produced it.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Optional, Sequence

from app.services.corroboration import normalize as n
from app.services.corroboration import vocabulary as v

# Helvetica and Helvetica-Bold advance widths (1/1000 em), WinAnsiEncoding codes 32..255.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556,
         556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
         500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278,
         469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556,
         333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584, 350, 556, 350, 222, 556, 333, 1000,
         556, 556, 333, 1000, 667, 333, 1000, 350, 611, 350, 350, 222, 222, 333, 333, 350, 556, 1000, 333,
         1000, 500, 333, 944, 350, 500, 667, 278, 333, 556, 556, 556, 556, 260, 556, 333, 737, 370, 556, 584,
         333, 737, 333, 400, 584, 333, 333, 333, 556, 537, 278, 333, 333, 365, 556, 834, 834, 834, 611, 667,
         667, 667, 667, 667, 667, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778,
         778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556, 556, 889, 500, 556,
         556, 556, 556, 278, 278, 278, 278, 556, 556, 556, 556, 556, 556, 556, 584, 611, 556, 556, 556, 556,
         500, 556, 500]
_HELV_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556,
              556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722,
              278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333,
              278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611,
              611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584, 350, 556, 350,
              278, 556, 500, 1000, 556, 556, 333, 1000, 667, 333, 1000, 350, 611, 350, 350, 278, 278, 500,
              500, 350, 556, 1000, 333, 1000, 556, 333, 944, 350, 500, 667, 278, 333, 556, 556, 556, 556,
              280, 556, 333, 737, 370, 556, 584, 333, 737, 333, 400, 584, 333, 333, 333, 611, 556, 278, 333,
              333, 365, 556, 834, 834, 834, 611, 722, 722, 722, 722, 722, 722, 1000, 722, 667, 667, 667, 667,
              278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667,
              611, 556, 556, 556, 556, 556, 556, 889, 556, 556, 556, 556, 556, 278, 278, 278, 278, 611, 611,
              611, 611, 611, 611, 611, 584, 611, 611, 611, 611, 611, 556, 611, 556]
_SUBST = {"₹": "Rs.", "→": "->", "←": "<-", "≤": "<=", "≥": ">=", "≠": "!=", "×": "x", "✓": "v", "✗": "x",
          " ": " ", "​": ""}

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 42.0
BODY_W = PAGE_W - 2 * MARGIN
SEVERITY_GRAY = {v.SEVERITY_HIGH: "0.80 0.25 0.25", v.SEVERITY_MEDIUM: "0.85 0.55 0.10", v.SEVERITY_LOW: "0.45 0.45 0.45"}


def encode(text: str) -> bytes:
    s = "".join(_SUBST.get(ch, ch) for ch in (text or ""))
    return s.encode("cp1252", errors="replace")


def width(text: str, size: float, bold: bool = False) -> float:
    table = _HELV_BOLD if bold else _HELV
    total = 0
    for b in encode(text):
        total += table[b - 32] if 32 <= b <= 255 else 556
    return total * size / 1000.0


def wrap(text: str, max_width: float, size: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        words, line = paragraph.split(" "), ""
        for w in words:
            trial = f"{line} {w}" if line else w
            if width(trial, size, bold) <= max_width:
                line = trial
                continue
            if line:
                lines.append(line)
            while width(w, size, bold) > max_width and len(w) > 1:  # a very long token is broken
                cut = len(w)
                while cut > 1 and width(w[:cut], size, bold) > max_width:
                    cut -= 1
                lines.append(w[:cut])
                w = w[cut:]
            line = w
        lines.append(line)
    return lines


def _esc(raw: bytes) -> bytes:
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class PdfWriter:
    def __init__(self) -> None:
        self.pages: list[list[bytes]] = []
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def need(self, height: float) -> None:
        if self.y - height < MARGIN + 24:
            self.new_page()

    def text(self, x: float, y: float, s: str, *, size: float = 9, bold: bool = False, rgb: Optional[str] = None) -> None:
        colour = f"{rgb} rg ".encode() if rgb else b""
        self.pages[-1].append(b"BT " + colour + f"/{'F2' if bold else 'F1'} {size:g} Tf {x:.2f} {y:.2f} Td (".encode()
                              + _esc(encode(s)) + b") Tj ET" + (b" 0 g" if rgb else b""))

    def rule(self, gray: float = 0.8) -> None:
        self.need(8)
        self.pages[-1].append(f"{gray:g} G 0.5 w {MARGIN:.2f} {self.y:.2f} m {PAGE_W - MARGIN:.2f} {self.y:.2f} l S 0 G".encode())
        self.y -= 8

    def para(self, s: str, *, size: float = 9, bold: bool = False, indent: float = 0, gap: float = 3,
             rgb: Optional[str] = None) -> None:
        leading = size * 1.3
        for line in wrap(s, BODY_W - indent, size, bold):
            self.need(leading)
            self.y -= leading
            self.text(MARGIN + indent, self.y, line, size=size, bold=bold, rgb=rgb)
        self.y -= gap

    def heading(self, s: str) -> None:
        self.need(40)
        self.y -= 8
        self.para(s, size=12, bold=True, gap=4)

    def columns(self, cells: Sequence[tuple[str, float, float, bool]], *, size: float = 8.5) -> None:
        """One table row: (text, x offset, width, bold) per cell, each wrapped."""
        leading = size * 1.3
        wrapped = [wrap(t, w - 4, size, b) for t, _, w, b in cells]
        height = max(len(x) for x in wrapped) * leading + 2
        self.need(height)
        for (t, x, w, b), lines in zip(cells, wrapped):
            y = self.y
            for line in lines:
                y -= leading
                self.text(MARGIN + x, y, line, size=size, bold=b)
        self.y -= height

    def chip(self, label: str, severity: str) -> float:
        """A coloured severity label at the current line; returns its width."""
        w = width(label, 8, True) + 8
        self.need(14)
        rgb = SEVERITY_GRAY.get(severity, "0.4 0.4 0.4")
        self.pages[-1].append(f"{rgb} rg {MARGIN:.2f} {self.y - 12:.2f} {w:.2f} 11 re f 0 g".encode())
        self.text(MARGIN + 4, self.y - 9.5, label, size=8, bold=True, rgb="1 1 1")
        return w

    def finish(self, footer: str) -> bytes:
        import pikepdf

        pdf = pikepdf.new()
        fonts = pikepdf.Dictionary(
            F1=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
                                  Encoding=pikepdf.Name.WinAnsiEncoding),
            F2=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                  BaseFont=pikepdf.Name("/Helvetica-Bold"), Encoding=pikepdf.Name.WinAnsiEncoding))
        total = len(self.pages)
        for i, ops in enumerate(self.pages, start=1):
            page = pdf.add_blank_page(page_size=(PAGE_W, PAGE_H))
            tail = f"{footer}  -  page {i} of {total}"
            foot = b"BT 0.45 g /F1 7 Tf " + f"{MARGIN:.2f} 24 Td (".encode() + _esc(encode(tail)) + b") Tj ET 0 g"
            page.Resources = pikepdf.Dictionary(Font=fonts)
            page.Contents = pdf.make_stream(b"\n".join(ops + [foot]))
        pdf.docinfo["/Title"] = "Corroboration report"
        pdf.docinfo["/Producer"] = "FlowPilot AI"
        buffer = io.BytesIO()
        pdf.save(buffer, deterministic_id=True)
        return buffer.getvalue()


def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "-"


def _diff_text(a: str, b: str) -> str:
    out = []
    for op, x, y in n.word_diff(a, b):
        if op == "equal":
            out.append(x)
        elif op == "delete":
            out.append(f"[-{x}-]")
        elif op == "insert":
            out.append(f"{{+{y}+}}")
        else:
            out.append(f"[-{x}-] {{+{y}+}}")
    return " ".join(p for p in out if p)


def to_pdf(bundle: dict) -> bytes:
    """`bundle` is the API's run detail (service → api._detail) as a dict."""
    run, docs = bundle["run"], sorted(bundle["documents"], key=lambda d: d["position"])
    names = {d["work_item_id"]: d["label"] for d in docs}
    w = PdfWriter()
    w.para("Corroboration report", size=17, bold=True, gap=2)
    w.para(" vs ".join(d["label"] for d in docs), size=10, gap=6)
    created = run.get("completed_at") or run.get("created_at") or ""
    w.para(f"Comparison {run['id']}  -  {run['status']}  -  completed {str(created)[:19].replace('T', ' ')} UTC  -  "
           f"engine {run['engine_version']}, encoder {(run.get('stats') or {}).get('encoder_used') or run['encoder']}",
           size=8, gap=2, rgb="0.35 0.35 0.35")
    if (run.get("stats") or {}).get("encoder_note"):
        w.para(str(run["stats"]["encoder_note"]), size=8, gap=2, rgb="0.35 0.35 0.35")
    if bundle.get("stale"):
        w.para("STALE: at least one document changed after this comparison ran. Re-run it for a current answer.",
               size=9, bold=True, rgb="0.75 0.2 0.2", gap=4)
    w.rule()
    w.heading("Documents")
    for d in docs:
        w.columns([(f"{d['position'] + 1}.", 0, 20, True), (d["label"], 20, 300, False),
                   (f"{d.get('page_count') or '-'} page(s)", 320, 70, False),
                   (f"content {d['content_hash'][:12]}", 390, BODY_W - 390, False)])
    w.heading("Summary")
    s = run
    by_sev = (s.get("stats") or {}).get("by_severity") or {}
    by_layer = (s.get("stats") or {}).get("by_layer") or {}
    threshold = (s.get("options") or {}).get("materiality_threshold", v.DEFAULT_MATERIALITY_THRESHOLD)
    w.para(f"{s['discrepancy_count']} difference(s), {s['material_count']} material (materiality >= {threshold}); "
           f"{s['open_material_count']} material still open. High {by_sev.get('HIGH', 0)}, medium "
           f"{by_sev.get('MEDIUM', 0)}, low {by_sev.get('LOW', 0)}.")
    w.para("By layer: " + ", ".join(f"{k.lower()} {val}" for k, val in by_layer.items()) + ".")
    layers = s.get("layers") or {}
    for key in v.LAYERS:
        info = layers.get(key) or {}
        if info.get("status") == "SKIPPED":
            w.para(f"{key.lower().capitalize()} layer skipped: {info.get('reason', '')}", size=8, rgb="0.4 0.4 0.4")
    w.heading("Agreement between documents")
    position = {d["work_item_id"]: d["position"] for d in docs}
    for p in bundle.get("pairs") or []:
        left, right = sorted((p["left_work_item_id"], p["right_work_item_id"]), key=lambda x: position.get(x, 9))
        a, b = names.get(left, "?"), names.get(right, "?")
        w.columns([(f"{a}  <->  {b}", 0, 250, True), (f"agreement {_pct(p['agreement'])}", 250, 90, False),
                   (f"clauses identical {p['clauses_identical']}/{max(p['clauses_left'], p['clauses_right'])}, "
                    f"fields {p['fields_agreeing']}/{p['fields_compared']}, lines {p['lines_agreeing']}/"
                    f"{max(p['lines_left'], p['lines_right'])}, material {p['material_count']}", 340, BODY_W - 340,
                    False)])
    rules = s.get("rules") or []
    if rules:
        w.heading("Rules")
        evaluated = {e["key"]: e["verdicts"] for e in (layers.get("RULE") or {}).get("evaluated") or []}
        for r in rules:
            verdicts = evaluated.get(r["key"], {})
            w.para(f"{r['sentence']}  ({r.get('understood_as', '')}; {r.get('source', '').lower()})", bold=True, gap=1)
            w.para("; ".join(f"{names.get(k, k)}: {val}" for k, val in verdicts.items()) or "not evaluated",
                   indent=12, size=8)
    w.heading("Differences")
    baseline = docs[0]["work_item_id"] if docs else None
    ordered = sorted(bundle.get("discrepancies") or [], key=lambda d: (not d["is_material"], d["ordinal"]))
    for d in ordered:
        w.need(40)
        w.y -= 4
        chip = w.chip(f"{d['severity']} {d['materiality']}", d["severity"])
        w.text(MARGIN + chip + 6, w.y - 9.5, f"#{d['ordinal'] + 1}  {d['kind'].replace('_', ' ').lower()}  -  "
                                              f"{d['label'][:90]}", size=9, bold=True)
        w.y -= 14
        state = d["status"] if d["status"] == v.DECISION_OPEN else f"{d['status']} {str(d.get('decided_at') or '')[:10]}"
        w.para(f"{d['summary']}  [{state}{' - material' if d['is_material'] else ''}]", size=8.5, gap=2)
        values = d.get("values") or {}
        for doc in docs:
            val = values.get(doc["work_item_id"]) or {}
            if val.get("participates") is False:
                continue
            where = f" (p. {val['page']})" if val.get("page") else ""
            shown = (val.get("display") or "").strip() or "- absent -"
            w.para(f"{doc['label']}{where}: {shown[:700]}", size=8, indent=12, gap=1)
        if d["kind"] == v.KIND_CLAUSE_MODIFIED and baseline in values and values[baseline].get("present"):
            base_text = values[baseline].get("display") or ""
            for doc in docs[1:]:
                other = values.get(doc["work_item_id"]) or {}
                if other.get("present") and other.get("normalized") != values[baseline].get("normalized"):
                    w.para(f"Changes {docs[0]['label']} -> {doc['label']}: {_diff_text(base_text, other.get('display') or '')[:900]}",
                           size=8, indent=12, gap=1, rgb="0.2 0.2 0.45")
        if d.get("note"):
            w.para(f"Reviewer note: {d['note']}", size=8, indent=12, gap=1)
    return w.finish(f"FlowPilot AI corroboration {run['id']}  -  fingerprint {run['fingerprint'][:16]}")


def to_json(bundle: dict) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def to_csv(bundle: dict) -> bytes:
    from app.services.tables.export import safe_text

    docs = sorted(bundle["documents"], key=lambda d: d["position"])
    buffer = io.StringIO()
    out = csv.writer(buffer)
    out.writerow(["#", "severity", "materiality", "material", "layer", "kind", "item", "status", "summary",
                  *[d["label"] for d in docs]])
    for d in bundle.get("discrepancies") or []:
        values = d.get("values") or {}
        out.writerow([d["ordinal"] + 1, d["severity"], d["materiality"], "yes" if d["is_material"] else "no", d["layer"],
                      d["kind"], safe_text(d["label"]), d["status"], safe_text(d["summary"]),
                      *[safe_text(((values.get(doc["work_item_id"]) or {}).get("display") or "")[:2000]) for doc in docs]])
    return buffer.getvalue().encode("utf-8-sig")


CONTENT_TYPES = {v.FORMAT_PDF: "application/pdf", v.FORMAT_JSON: "application/json",
                 v.FORMAT_CSV: "text/csv; charset=utf-8"}


def filename(docs: Sequence[dict], fmt: str, stamp: Optional[datetime] = None) -> str:
    import re

    first = sorted(docs, key=lambda d: d["position"])[0]["label"] if docs else "comparison"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", re.sub(r"\.[^.]+$", "", first))[:60] or "comparison"
    return f"corroboration_{stem}_{len(docs)}docs.{fmt}"


__all__ = ["CONTENT_TYPES", "PdfWriter", "encode", "filename", "to_csv", "to_json", "to_pdf", "width", "wrap"]
