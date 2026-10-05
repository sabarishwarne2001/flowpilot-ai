"""F-050 regression: concurrent packet thumbnails must not crash the API process.

The split-review screen asks for every page thumbnail of a packet at once. The
route is a sync FastAPI handler, so Starlette runs those requests on parallel
threads, and each one rendered with pypdfium2. PDFium is not thread-safe
(pypdfium2 documents this), so two renders at the same time corrupted the heap
and the whole API process aborted ("free(): unaligned chunk detected in
tcache 2") — every tenant lost the API, not just the reader of that screen.
Found by the Phase 3 browser tests (packet-split review page).

The stress runs in a child process: a crash there fails this test instead of
killing pytest.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _pdf_with_pages(pages: int) -> bytes:
    """A small valid PDF with a text line on each page."""
    bodies: dict[int, str] = {1: "<< /Type /Catalog /Pages 2 0 R >>",
                              3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids = []
    next_id = 4
    for number in range(pages):
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        kids.append(f"{page_id} 0 R")
        stream = f"BT /F1 24 Tf 72 720 Td (Packet page {number + 1}) Tj ET"
        bodies[page_id] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                           f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>")
        bodies[content_id] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    bodies[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {pages} >>"
    out = b"%PDF-1.4\n"
    offsets = {}
    for object_id in range(1, next_id):
        offsets[object_id] = len(out)
        out += f"{object_id} 0 obj\n{bodies[object_id]}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {next_id}\n0000000000 65535 f \n".encode()
    for object_id in range(1, next_id):
        out += f"{offsets[object_id]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {next_id} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


STRESS = textwrap.dedent(
    """
    import sys, threading
    from types import SimpleNamespace

    import app.core.storage as storage
    from app.services.packets import service

    pdf = open(sys.argv[1], "rb").read()

    class _Storage:
        def get(self, _key):
            return pdf

    storage.get_storage_driver = lambda: _Storage()
    item = SimpleNamespace(file_type="application/pdf", stored_filename="packet.pdf")
    errors = []

    def worker():
        try:
            for round_ in range(30):
                png = service.render_thumbnail(item, 1 + round_ % 4)
                assert png[:8] == b"\\x89PNG\\r\\n\\x1a\\n"
        except Exception as exc:  # a Python error is a failure too, not a crash
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("errors:", errors)
    sys.exit(1 if errors else 0)
    """
)


def test_concurrent_thumbnails_do_not_crash_the_process(tmp_path):
    packet = tmp_path / "packet.pdf"
    packet.write_bytes(_pdf_with_pages(4))
    result = subprocess.run(
        [sys.executable, "-c", STRESS, str(packet)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"concurrent render_thumbnail exited {result.returncode} "
        f"(negative = killed by a signal):\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
    )
