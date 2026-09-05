#!/usr/bin/env python
"""ARCH-28 §6 — SOC 2 Type I evidence pack generator.

    python scripts/generate_soc2_evidence.py
    python scripts/generate_soc2_evidence.py --static-only
    python scripts/generate_soc2_evidence.py --out evidence/ --format both
    python scripts/generate_soc2_evidence.py --fail-on-indeterminate

The CLI around `app/services/compliance/evidence_pack.py`. The service does the
observing; this decides where the output goes and what the exit code means.

EXIT CODES
==========

    0   every control SATISFIED
    1   at least one EXCEPTION — a control is designed but not in place
    2   at least one INDETERMINATE and --fail-on-indeterminate was passed

The default treats INDETERMINATE as a non-zero-worthy warning rather than a
failure, because the most common cause is running without a database, and
that is a legitimate way to produce a partial pack during development. In CI,
and on the run that actually goes to the auditor, pass
`--fail-on-indeterminate`: an evidence pack with unobserved controls is a
document that certifies whatever the generator happened to reach.

The pack NEVER contains key material. Fernet is evidenced by key count,
fingerprint and a round-trip proof. This file leaves the building.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _session() -> Optional[Any]:
    try:
        from sqlalchemy import text

        from app.db.session import SessionLocal

        session = SessionLocal()
        session.execute(text("SELECT 1"))
        return session
    except Exception as exc:  # noqa: BLE001
        print(f"  (database unreachable: {type(exc).__name__}: {exc})", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the FlowPilot AI SOC 2 Type I evidence pack"
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="do not open a database session; database controls become INDETERMINATE",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="directory to write into (default: print JSON to stdout)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown", "both"),
        default="json",
    )
    parser.add_argument(
        "--fail-on-indeterminate",
        action="store_true",
        help="exit 2 when any control could not be observed; use for the "
        "run that goes to the auditor",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    from app.services.compliance.evidence_pack import (
        EXCEPTION,
        INDETERMINATE,
        SATISFIED,
        compile_pack,
        render_markdown,
    )

    session = None if args.static_only else _session()
    try:
        pack = compile_pack(session)
    finally:
        if session is not None:
            session.close()

    summary = pack["summary"]

    if not args.quiet:
        print("=" * 78)
        print("FlowPilot AI — SOC 2 Type I evidence pack")
        print("=" * 78)
        width = max(len(f["control"]) for f in pack["findings"]) + 2
        for finding in pack["findings"]:
            print(
                f"  [{finding['status']:13}] {finding['control']:<{width}}"
                f"{finding['criterion']:<8}{finding['detail'][:70]}"
            )
        print("=" * 78)
        print(
            f"{summary[SATISFIED]} satisfied / {summary[EXCEPTION]} exception "
            f"/ {summary[INDETERMINATE]} indeterminate"
        )
        print(f"digest: {pack['content_digest']}")

    if args.out:
        out = pathlib.Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        written: list[pathlib.Path] = []

        if args.format in ("json", "both"):
            target = out / f"soc2-type1-evidence-{stamp}.json"
            target.write_text(json.dumps(pack, indent=2, default=str), encoding="utf-8")
            written.append(target)

        if args.format in ("markdown", "both"):
            target = out / f"soc2-type1-evidence-{stamp}.md"
            target.write_text(render_markdown(pack), encoding="utf-8")
            written.append(target)

        for target in written:
            print(f"Written: {target}")
    elif args.format == "markdown":
        print(render_markdown(pack))
    elif not args.out and args.quiet:
        print(json.dumps(pack, indent=2, default=str))

    if summary[EXCEPTION]:
        print(
            f"\n{summary[EXCEPTION]} control(s) reported EXCEPTION. These are "
            "controls the platform claims and does not currently have. Fix "
            "before submission.",
            file=sys.stderr,
        )
        return 1

    if summary[INDETERMINATE]:
        message = (
            f"\n{summary[INDETERMINATE]} control(s) could not be observed from "
            "this run. They are NOT passes. Re-run against the production-shaped "
            "environment before the report is signed."
        )
        if args.fail_on_indeterminate:
            print(message, file=sys.stderr)
            return 2
        print(message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())