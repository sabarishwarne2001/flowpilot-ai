"""ARCH-50 — the release manifest: a CycloneDX SBOM and Ed25519-signed SHA-256 checksums.

ARCH50-S1:release

    release/SHA256SUMS          "<sha256>  <path>" for every file of the release, sorted by path
    release/SHA256SUMS.sig      {"format": "flowpilot-release-sig/1", "key_id": ..., "signature": ...}
                                (Ed25519 over the exact bytes of SHA256SUMS)
    release/sbom.cdx.json       CycloneDX 1.5 JSON: every pinned Python requirement and every npm package in
                                the lockfile, with purls and the licences the metadata declares
    release/release.json        version, commit, when, counts, the SBOM's own digest

Built by `scripts/release_manifest.py build`, checked by `... verify` and by the operator console. JSON only:
no XML library is imported (verify_arch16 S1 confines XML to two modules). What is signed is the checksum
list, and the SBOM and this module are themselves in it, so swapping a trusted key or an SBOM entry breaks
the signature.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from app.core import licence_keys, signing

SUMS = "SHA256SUMS"
SIG = "SHA256SUMS.sig"
SBOM = "sbom.cdx.json"
RELEASE = "release.json"
SIG_FORMAT = "flowpilot-release-sig/1"
EXCLUDED_PARTS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", ".pytest_cache", ".mypy_cache",
                  "release", ".flowpilot_backups", "uploads"}
EXCLUDED_PREFIXES = ("backend/evidence/", "backend/.arch")

STATUS_VERIFIED = "VERIFIED"
STATUS_MISSING = "MISSING"
STATUS_UNSIGNED = "UNSIGNED"
STATUS_UNTRUSTED_KEY = "UNTRUSTED_KEY"
STATUS_BAD_SIGNATURE = "BAD_SIGNATURE"
STATUS_DEVELOPMENT_KEY = "DEVELOPMENT_KEY_IN_PRODUCTION"
STATUS_MODIFIED = "MODIFIED"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def release_files(root: Path) -> list[str]:
    """Tracked files when git is present (what the release IS), else a walk with the same exclusions."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=str(root), capture_output=True, timeout=60)
        if out.returncode == 0 and out.stdout:
            names = [n for n in out.stdout.decode("utf-8").split("\0") if n]
            return sorted(n for n in names if _included(n) and (root / n).is_file())
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if _included(rel):
                found.append(rel)
    return sorted(found)


def _included(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in EXCLUDED_PARTS for p in parts):
        return False
    return not any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_checksums(root: Path, files: list[str]) -> str:
    return "".join(f"{file_digest(root / rel)}  {rel}\n" for rel in sorted(files))


def parse_checksums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(f"not a checksum line: {line[:80]!r}")
        out[match.group(2)] = match.group(1)
    return out


# --------------------------------------------------------------------------- SBOM


def _python_requirements(root: Path) -> list[tuple[str, str]]:
    path = root / "backend" / "requirements.txt"
    if not path.exists():
        return []
    pins = []
    for line in _read_text(path).splitlines():
        line = line.split("#", 1)[0].strip()
        match = re.fullmatch(r"([A-Za-z0-9_.\-\[\]]+)==([A-Za-z0-9_.+\-!]+)", line)
        if match:
            pins.append((re.sub(r"\[.*\]", "", match.group(1)), match.group(2)))
    return sorted(set(pins), key=lambda p: p[0].lower())


def _python_licence(name: str) -> Optional[str]:
    try:
        from importlib import metadata

        meta = metadata.metadata(name)
    except Exception:  # noqa: BLE001 -- not installed here (torch, paddle in the sandbox)
        return None
    expr = meta.get("License-Expression")
    if expr:
        return str(expr)[:120]
    raw = meta.get("License")
    if raw and len(raw) < 80 and "\n" not in raw:
        return str(raw)
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License :: OSI Approved :: "):
            return classifier.rsplit("::", 1)[-1].strip()
    return None


def _npm_packages(root: Path) -> list[dict[str, Any]]:
    lock = root / "frontend" / "package-lock.json"
    if not lock.exists():
        return []
    data = json.loads(_read_text(lock))
    out = []
    for key, info in sorted((data.get("packages") or {}).items()):
        if not key.startswith("node_modules/") or not isinstance(info, dict) or not info.get("version"):
            continue
        name = key.split("node_modules/")[-1]
        out.append({"name": name, "version": str(info["version"]), "license": info.get("license"),
                    "dev": bool(info.get("dev")), "integrity": info.get("integrity")})
    return out


def build_sbom(root: Path, *, version: str, commit: Optional[str], at: Optional[datetime] = None) -> dict[str, Any]:
    moment = (at or datetime.now(timezone.utc)).replace(microsecond=0)
    components: list[dict[str, Any]] = []
    for name, ver in _python_requirements(root):
        comp: dict[str, Any] = {"type": "library", "name": name, "version": ver,
                                "purl": f"pkg:pypi/{name.lower()}@{ver}", "bom-ref": f"pypi:{name.lower()}@{ver}",
                                "scope": "required"}
        lic = _python_licence(name)
        if lic:
            comp["licenses"] = [{"expression": lic}] if " " in lic or "(" in lic else [{"license": {"name": lic}}]
        components.append(comp)
    for pkg in _npm_packages(root):
        name = pkg["name"]
        purl_name = name.replace("@", "%40", 1) if name.startswith("@") else name
        comp = {"type": "library", "name": name, "version": pkg["version"],
                "purl": f"pkg:npm/{purl_name}@{pkg['version']}", "bom-ref": f"npm:{name}@{pkg['version']}",
                "scope": "optional" if pkg["dev"] else "required"}
        if pkg.get("license"):
            comp["licenses"] = [{"license": {"name": str(pkg["license"])[:120]}}]
        if pkg.get("integrity") and str(pkg["integrity"]).startswith("sha512-"):
            import base64

            try:
                comp["hashes"] = [{"alg": "SHA-512",
                                   "content": base64.b64decode(str(pkg["integrity"])[7:]).hex()}]
            except Exception:  # noqa: BLE001
                pass
        components.append(comp)
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'flowpilot-ai/{version}/{commit}')}",
        "version": 1,
        "metadata": {"timestamp": moment.isoformat().replace("+00:00", "Z"),
                     "tools": [{"vendor": "FlowPilot AI", "name": "release_manifest", "version": "arch50"}],
                     "component": {"type": "application", "name": "flowpilot-ai", "version": version,
                                   "bom-ref": "flowpilot-ai", **({"properties": [{"name": "git:commit",
                                                                                   "value": commit}]}
                                                                 if commit else {})}},
        "components": components,
    }


# --------------------------------------------------------------------------- signature


def sign_checksums(sums_text: str, private_key_b64: str) -> dict[str, str]:
    public = signing.public_key_of(private_key_b64)
    return {"format": SIG_FORMAT, "key_id": signing.key_id(public),
            "signature": signing.sign(private_key_b64, sums_text.encode("utf-8"))}


def verify_release(release_dir: Path, *, root: Optional[Path] = None,
                   trusted: Optional[Mapping[str, Mapping[str, Any]]] = None,
                   environment: Optional[str] = None, check_files: bool = True) -> dict[str, Any]:
    root = root or repository_root()
    sums_path, sig_path = release_dir / SUMS, release_dir / SIG
    out: dict[str, Any] = {"status": STATUS_MISSING, "release_dir": str(release_dir), "files": 0, "modified": [],
                           "missing": [], "key_id": None, "release": None}
    if not sums_path.exists():
        return out
    sums_text = sums_path.read_bytes().decode("utf-8")
    listed = parse_checksums(sums_text)
    out["files"] = len(listed)
    if (release_dir / RELEASE).exists():
        try:
            out["release"] = json.loads((release_dir / RELEASE).read_text(encoding="utf-8"))
        except ValueError:
            out["release"] = None
    if not sig_path.exists():
        out["status"] = STATUS_UNSIGNED
        return out
    sig = json.loads(sig_path.read_text(encoding="utf-8"))
    out["key_id"] = sig.get("key_id")
    keys = licence_keys.TRUSTED_RELEASE_KEYS if trusted is None else trusted
    key = keys.get(str(sig.get("key_id")))
    if key is None or signing.key_id(str(key["public_key"])) != sig.get("key_id"):
        out["status"] = STATUS_UNTRUSTED_KEY
        return out
    if not signing.verify(str(key["public_key"]), sums_text.encode("utf-8"), str(sig.get("signature"))):
        out["status"] = STATUS_BAD_SIGNATURE
        return out
    env = str(environment if environment is not None else os.environ.get("ENVIRONMENT", "development")).lower()
    if env == "production" and not bool(key.get("production")):
        out["status"] = STATUS_DEVELOPMENT_KEY
        return out
    if check_files:
        for rel, digest in listed.items():
            path = (release_dir / rel.split("/", 1)[1]) if rel.startswith("release/") else root / rel
            if not path.exists():
                out["missing"].append(rel)
            elif file_digest(path) != digest:
                out["modified"].append(rel)
    out["status"] = STATUS_MODIFIED if (out["modified"] or out["missing"]) else STATUS_VERIFIED
    out["modified"] = out["modified"][:50]
    out["missing"] = out["missing"][:50]
    return out


__all__ = ["RELEASE", "SBOM", "SIG", "SUMS", "build_checksums", "build_sbom", "file_digest", "parse_checksums",
           "release_files", "repository_root", "sign_checksums", "verify_release"]
