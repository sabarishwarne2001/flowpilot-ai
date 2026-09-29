#!/usr/bin/env python3
"""ARCH50-S1:release-manifest — the release's SBOM and signed checksums.

    python scripts/release_manifest.py build --version 2.0.0 [--key release.key] [--out ../release]
    python scripts/release_manifest.py verify [--dir ../release] [--environment production]
    python scripts/release_manifest.py sbom                     # print the CycloneDX SBOM only

`build` writes release/sbom.cdx.json (CycloneDX 1.5 JSON: every pinned Python requirement and every package in
frontend/package-lock.json, with purls, declared licences and the lockfile's SHA-512 integrity), release/release.json
(version, commit, time, counts, the SBOM's digest), then release/SHA256SUMS over every tracked file PLUS those two,
and -- with --key -- release/SHA256SUMS.sig (Ed25519 over the exact bytes of SHA256SUMS). `verify` checks the
signature against a key pinned in app/core/licence_keys.py and re-hashes every listed file. JSON only; no XML
library is imported (verify_arch16 S1). Nothing here makes a network call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _release_module():
    """app/services/sovereign/release.py loaded by path: building or verifying a release must not need the
    application's configuration (importing the app.services package would)."""
    import importlib.util

    name = "flowpilot_release_offline"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, BACKEND / "app" / "services" / "sovereign" / "release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def build(version: str, out_dir: Path, key_file: str | None, root: Path = ROOT) -> dict:
    release = _release_module()

    out_dir.mkdir(parents=True, exist_ok=True)
    commit = _commit()
    sbom = release.build_sbom(root, version=version, commit=commit)
    sbom_bytes = (json.dumps(sbom, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (out_dir / release.SBOM).write_bytes(sbom_bytes)
    files = release.release_files(root)
    meta = {"product": "flowpilot-ai", "version": version, "commit": commit,
            "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "files": len(files), "components": len(sbom["components"]),
            "sbom_sha256": hashlib.sha256(sbom_bytes).hexdigest()}
    (out_dir / release.RELEASE).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sums = release.build_checksums(root, files)
    prefix = out_dir.relative_to(root).as_posix() if out_dir.is_relative_to(root) else "release"
    for name in (release.SBOM, release.RELEASE):
        sums += f"{release.file_digest(out_dir / name)}  {prefix}/{name}\n"
    (out_dir / release.SUMS).write_bytes(sums.encode("utf-8"))
    signed = None
    if key_file:
        sig = release.sign_checksums(sums, Path(key_file).read_text(encoding="ascii").strip())
        (out_dir / release.SIG).write_text(json.dumps(sig, indent=2) + "\n", encoding="utf-8")
        signed = sig["key_id"]
    return {**meta, "signed_by": signed, "out": str(out_dir)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--version", required=True)
    b.add_argument("--key")
    b.add_argument("--out", default=str(ROOT / "release"))
    v = sub.add_parser("verify")
    v.add_argument("--dir", default=str(ROOT / "release"))
    v.add_argument("--environment")
    sub.add_parser("sbom")
    args = parser.parse_args(argv)
    release = _release_module()

    if args.command == "build":
        print(json.dumps(build(args.version, Path(args.out).resolve(), args.key), indent=2))
        return 0
    if args.command == "verify":
        result = release.verify_release(Path(args.dir).resolve(), root=ROOT, environment=args.environment)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "VERIFIED" else 1
    print(json.dumps(release.build_sbom(ROOT, version="dev", commit=_commit()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
