#!/usr/bin/env python3
"""ARCH50-S1:licence-tool — FlowPilot's Ed25519 keys and sovereign-edition licences, offline.

    python scripts/licence_tool.py keygen --out flowpilot-licence.key        # a new private key (0600); prints the public key
    python scripts/licence_tool.py trust --public-key <b64> --purpose licence --production --label "FlowPilot licence key 2026"
    python scripts/licence_tool.py issue --key flowpilot-licence.key --licensee "Acme Bank" --expires 2027-09-30 \\
            --max-organizations 5 --max-seats 500 --grace-days 14 --feature egress_lockdown --feature local_llm \\
            [--deployment-id acme-prod] --out acme.licence.json
    python scripts/licence_tool.py verify acme.licence.json [--environment production]
    python scripts/licence_tool.py show

KEEP THE PRIVATE KEY OFF EVERY DEPLOYMENT. Anyone holding it can license anything. `trust` pins a PUBLIC key into
app/core/licence_keys.py (the only place a verifier reads keys from); run it in the source tree before a release is
built, so the release's signed SHA256SUMS covers the pinned key. `--production` marks a key whose licences a
production deployment accepts; without it the key is a development key (refused when ENVIRONMENT=production).
Nothing here makes a network call.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

KEYS_FILE = BACKEND / "app" / "core" / "licence_keys.py"


def _licence_module():
    """app/services/sovereign/licence.py loaded by path: the tool must not need the application's configuration
    (importing the app.services package would)."""
    import importlib.util

    name = "flowpilot_licence_offline"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, BACKEND / "app" / "services" / "sovereign" / "licence.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_private(path: Path, private_b64: str) -> None:
    path = Path(path)
    if path.exists():
        raise SystemExit(f"{path} exists; refusing to overwrite a private key")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(private_b64 + "\n")


def _read_private(path: str) -> str:
    return Path(path).read_text(encoding="ascii").strip()


def cmd_keygen(args: argparse.Namespace) -> int:
    from app.core import signing

    private, public = signing.generate_keypair()
    _write_private(Path(args.out), private)
    print(json.dumps({"key_id": signing.key_id(public), "public_key": public, "private_key_file": str(args.out)},
                     indent=2))
    return 0


def rewrite_trusted(text: str, *, purpose: str, key_id: str, public_key: str, production: bool, label: str) -> str:
    """Add one entry to TRUSTED_LICENCE_KEYS or TRUSTED_RELEASE_KEYS, keeping everything else byte for byte."""
    block = "LICENCE" if purpose == "licence" else "RELEASE"
    begin, end = f"# BEGIN TRUSTED {block} KEYS\n", f"# END TRUSTED {block} KEYS\n"
    if begin not in text or end not in text:
        raise SystemExit(f"the {block} block markers are missing from {KEYS_FILE.name}")
    head, rest = text.split(begin, 1)
    body, tail = rest.split(end, 1)
    name = f"TRUSTED_{block}_KEYS"
    entries = re.findall(r'^    "(ed25519:[0-9a-f]{16})": \{.*\},$', body, re.M)
    lines = [line for line in body.splitlines() if line.startswith('    "ed25519:')]
    if key_id in entries:
        return text
    lines.append(f'    "{key_id}": {{"public_key": "{public_key}", "production": {bool(production)}, '
                 f'"label": {json.dumps(label)}}},')
    new_body = f"{name}: dict[str, TrustedKey] = {{\n" + "\n".join(lines) + "\n}\n"
    return head + begin + new_body + end + tail


def cmd_trust(args: argparse.Namespace) -> int:
    from app.core import signing

    key_id = signing.key_id(args.public_key)
    text = KEYS_FILE.read_text(encoding="utf-8")
    updated = rewrite_trusted(text, purpose=args.purpose, key_id=key_id, public_key=args.public_key,
                              production=args.production, label=args.label or key_id)
    if updated != text:
        KEYS_FILE.write_text(updated, encoding="utf-8", newline="\n")
        print(f"pinned {key_id} ({args.purpose}, {'production' if args.production else 'development'})")
    else:
        print(f"{key_id} is already pinned")
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    licence = _licence_module()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    not_before = datetime.fromisoformat(args.not_before) if args.not_before else now
    expires = datetime.fromisoformat(args.expires)
    if expires.tzinfo is None:
        expires = expires.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    if not_before.tzinfo is None:
        not_before = not_before.replace(tzinfo=timezone.utc)
    payload = {"licence_id": args.licence_id or f"LIC-{uuid.uuid4().hex[:12].upper()}", "licensee": args.licensee,
               "edition": "sovereign", "issued_at": now.isoformat().replace("+00:00", "Z"),
               "not_before": not_before.isoformat().replace("+00:00", "Z"),
               "expires_at": expires.isoformat().replace("+00:00", "Z"), "grace_days": int(args.grace_days),
               "max_organizations": int(args.max_organizations), "max_seats": int(args.max_seats),
               "features": sorted(set(args.feature or []))}
    if args.deployment_id:
        payload["deployment_id"] = args.deployment_id
    doc = licence.issue(_read_private(args.key), payload)
    text = json.dumps(doc.as_dict(), indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({payload['licence_id']}, signed by {doc.key_id})")
    else:
        sys.stdout.write(text)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    licence = _licence_module()

    doc = licence.parse(Path(args.file).read_bytes())
    status = licence.verify(doc, environment=args.environment or os.environ.get("ENVIRONMENT") or "development")
    print(json.dumps(status.as_dict(), indent=2, default=str))
    return 0 if status.status in licence.USABLE else 1


def cmd_show(_: argparse.Namespace) -> int:
    from app.core import licence_keys

    print(json.dumps({"licence": licence_keys.TRUSTED_LICENCE_KEYS, "release": licence_keys.TRUSTED_RELEASE_KEYS},
                     indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("keygen")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_keygen)
    p = sub.add_parser("trust")
    p.add_argument("--public-key", required=True)
    p.add_argument("--purpose", choices=("licence", "release"), required=True)
    p.add_argument("--production", action="store_true")
    p.add_argument("--label", default="")
    p.set_defaults(fn=cmd_trust)
    p = sub.add_parser("issue")
    p.add_argument("--key", required=True)
    p.add_argument("--licensee", required=True)
    p.add_argument("--expires", required=True, help="YYYY-MM-DD or an ISO-8601 instant")
    p.add_argument("--not-before")
    p.add_argument("--licence-id")
    p.add_argument("--max-organizations", type=int, required=True)
    p.add_argument("--max-seats", type=int, required=True)
    p.add_argument("--grace-days", type=int, default=14)
    p.add_argument("--feature", action="append")
    p.add_argument("--deployment-id")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_issue)
    p = sub.add_parser("verify")
    p.add_argument("file")
    p.add_argument("--environment")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("show")
    p.set_defaults(fn=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
