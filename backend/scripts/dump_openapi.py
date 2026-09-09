"""
Dumps the OpenAPI document to a file without serving the application.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app


def main() -> int:
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")

    document = app.openapi()

    schemas = document.get("components", {}).get("schemas", {})
    paths = document.get("paths", {})

    if not schemas:
        print(
            "REFUSING TO WRITE: the OpenAPI document has no component schemas. "
            "The route table is empty or the app failed to wire its routers.",
            file=sys.stderr,
        )
        return 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2), encoding="utf-8")

    print(f"Wrote {destination} — {len(schemas)} schemas, {len(paths)} paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
