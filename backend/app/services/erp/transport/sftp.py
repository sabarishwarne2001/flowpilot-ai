"""ARCH47-S1:sftp — SFTP delivery (paramiko), with the same egress rules as HTTP.

  * The host is resolved and every address checked (app.core.ssrf_client:
    no private, loopback, link-local, CGNAT or metadata address); the TCP
    connection is opened to the validated address and handed to paramiko.
  * The server's host key must match the pinned SHA-256 fingerprint saved with
    the target (sftp.host_key_sha256, "SHA256:<base64>" as ssh-keygen -l prints
    it). There is no trust-on-first-use: an unknown or changed key refuses the
    connection (PERMANENT: a person must confirm the new key).
  * Upload is atomic: the file is written as ".<name>.part" in the target
    directory and then RENAMED to its final name. Importers must ignore dot-files
    and ".part" files (the target's documentation says so). The rename is the
    only commit point:
      - a failure before it           TRANSIENT (nothing visible to the ERP)
      - a failure during it           UNCERTAIN: the ledger PROBES (the final file
                                      with our size and sha256, or its ack) and
                                      only then decides
  * The final name is deterministic per posting, so a probe knows what to look for.

Acknowledgements are files the ERP (or its EDI partner) writes: a 997 in the
ack directory (X12), "<name>.ack" (ACCEPTED / REJECTED: reason, or Tally's import
RESPONSE XML), or -- DELIVERY mode -- the delivered file itself.
"""

from __future__ import annotations

import base64
import hashlib
import io
import posixpath
import re
import socket
import stat
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from app.services.erp import vocabulary as v

MAX_ACK_FILES = 200
MAX_ACK_BYTES = 1024 * 1024


class SftpError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


#: The gates allow the loopback mock server (never in production: see `_resolve`).
allow_private_for_tests = False


def fingerprint(key: Any) -> str:
    """ssh-keygen style SHA256 fingerprint of a paramiko key."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def settings(config: Mapping[str, Any]) -> dict:
    s = dict(config.get("sftp") or {})
    host = str(s.get("host") or "").strip()
    if not host or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
        raise SftpError(v.OUTCOME_PERMANENT, "the target needs sftp.host, a host name")
    port = int(s.get("port") or 22)
    if not 1 <= port <= 65535:
        raise SftpError(v.OUTCOME_PERMANENT, "sftp.port is 1 to 65535")
    pin = str(s.get("host_key_sha256") or "").strip()
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", pin):
        raise SftpError(v.OUTCOME_PERMANENT, "sftp.host_key_sha256 must be the server's pinned key fingerprint "
                                             "(SHA256:<43 base64 characters>, as ssh-keygen -l prints it)")
    directory = str(s.get("directory") or "/").strip() or "/"
    ack_dir = str(s.get("ack_directory") or directory).strip() or directory
    for name, value in (("directory", directory), ("ack_directory", ack_dir)):
        if not value.startswith("/") or ".." in value.split("/") or "\x00" in value:
            raise SftpError(v.OUTCOME_PERMANENT, f"sftp.{name} is an absolute path without '..'")
    return {"host": host, "port": port, "pin": pin, "directory": directory.rstrip("/") or "/",
            "ack_directory": ack_dir.rstrip("/") or "/", "timeout": float(s.get("timeout_seconds") or 30)}


def _resolve(host: str, port: int) -> list[str]:
    from app.core import ssrf_client as S

    if allow_private_for_tests:
        from app.core.config import settings as app_settings

        if getattr(app_settings, "ENVIRONMENT", "development") == "production":
            raise SftpError(v.OUTCOME_PERMANENT, "private addresses are never allowed in production")
        return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]
    try:
        return S.resolve_and_validate(host, port, timeout=5.0)
    except S.ForbiddenAddressError as exc:
        raise SftpError(v.OUTCOME_PERMANENT, f"refused by the egress guard: {exc}") from exc
    except (S.DNSResolutionError, S.TimeoutExceededError) as exc:
        raise SftpError(v.OUTCOME_TRANSIENT, str(exc)) from exc


def load_private_key(text: str, passphrase: Optional[str]) -> Any:
    import paramiko

    last: Optional[Exception] = None
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase or None)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise SftpError(v.OUTCOME_PERMANENT, f"the private key could not be read ({type(last).__name__})")


@dataclass
class Delivery:
    kind: str
    remote_path: Optional[str] = None
    size: Optional[int] = None
    message: str = ""
    log: list[dict] = field(default_factory=list)


class Connection:
    def __init__(self, *, config: Mapping[str, Any], auth_mode: str, credential: Optional[dict]) -> None:
        self.cfg = settings(config)
        self.auth_mode = auth_mode
        self.credential = dict(credential or {})
        self.transport: Any = None
        self.sftp: Any = None

    def __enter__(self) -> "Connection":
        import paramiko

        cfg = self.cfg
        last: Optional[Exception] = None
        sock = None
        for ip in _resolve(cfg["host"], cfg["port"]):
            try:
                sock = socket.create_connection((ip, cfg["port"]), timeout=cfg["timeout"])
                break
            except OSError as exc:
                last = exc
        if sock is None:
            raise SftpError(v.OUTCOME_TRANSIENT, f"could not connect: {last}")
        try:
            transport = paramiko.Transport(sock)
            transport.banner_timeout = cfg["timeout"]
            transport.start_client(timeout=cfg["timeout"])
            key = transport.get_remote_server_key()
            seen = fingerprint(key)
            if seen != cfg["pin"]:
                transport.close()
                raise SftpError(v.OUTCOME_PERMANENT, f"the server's host key {seen} is not the pinned "
                                                     f"{cfg['pin']}: refused (confirm the new key before posting)")
            username = str(self.credential.get("username") or "")
            if self.auth_mode == v.AUTH_SSH_PASSWORD:
                transport.auth_password(username, str(self.credential.get("password") or ""))
            elif self.auth_mode == v.AUTH_SSH_KEY:
                transport.auth_publickey(username, load_private_key(str(self.credential.get("private_key") or ""),
                                                                    self.credential.get("passphrase")))
            else:
                raise SftpError(v.OUTCOME_PERMANENT, f"{self.auth_mode} is not an SSH authentication")
            if not transport.is_authenticated():
                raise SftpError(v.OUTCOME_PERMANENT, "the server did not accept the credential")
            self.transport = transport
            self.sftp = paramiko.SFTPClient.from_transport(transport)
            self.sftp.get_channel().settimeout(cfg["timeout"])
        except SftpError:
            sock.close()
            raise
        except paramiko.AuthenticationException as exc:
            sock.close()
            raise SftpError(v.OUTCOME_PERMANENT, f"authentication failed: {exc}") from exc
        except (paramiko.SSHException, OSError, EOFError) as exc:
            sock.close()
            raise SftpError(v.OUTCOME_TRANSIENT, f"SSH negotiation failed: {exc}") from exc
        return self

    def __exit__(self, *exc: Any) -> None:
        for thing in (self.sftp, self.transport):
            try:
                if thing is not None:
                    thing.close()
            except Exception:  # noqa: BLE001
                pass

    def _stat(self, path: str) -> Optional[Any]:
        try:
            return self.sftp.stat(path)
        except IOError:
            return None

    def remote_sha(self, path: str, limit: int = v.MAX_RENDERED_BYTES) -> Optional[str]:
        st = self._stat(path)
        if st is None or st.st_size > limit:
            return None
        with self.sftp.open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def upload(self, filename: str, data: bytes, sha256: str) -> Delivery:
        cfg = self.cfg
        if "/" in filename or filename.startswith("."):
            return Delivery(v.OUTCOME_PERMANENT, message="unsafe file name")
        final = posixpath.join(cfg["directory"], filename)
        temp = posixpath.join(cfg["directory"], f".{filename}.part")
        existing = self._stat(final)
        if existing is not None:
            same = self.remote_sha(final) == sha256
            return Delivery(v.OUTCOME_FOUND if same else v.OUTCOME_PERMANENT, final, existing.st_size,
                            "already on the server" if same else "a DIFFERENT file already has this name")
        try:
            self.sftp.putfo(io.BytesIO(data), temp, file_size=len(data), confirm=True)
        except Exception as exc:  # noqa: BLE001 - the ERP never sees a .part file
            return Delivery(v.OUTCOME_TRANSIENT, message=f"upload failed before commit: {exc}")
        try:
            try:
                self.sftp.posix_rename(temp, final)
            except IOError:
                self.sftp.rename(temp, final)
        except Exception as exc:  # noqa: BLE001 - the rename may or may not have happened
            return Delivery(v.OUTCOME_UNCERTAIN, final, message=f"the rename to the final name failed: {exc}")
        st = self._stat(final)
        if st is None or st.st_size != len(data):
            return Delivery(v.OUTCOME_UNCERTAIN, final, message="the file is not on the server with its size after "
                                                               "the rename (an importer may already have taken it)")
        return Delivery(v.OUTCOME_OK, final, st.st_size, f"uploaded to {final}")

    def probe(self, final: str, sha256: str) -> str:
        """FOUND (our bytes are there), ABSENT (nothing by that name), or MISMATCH (another file)."""
        st = self._stat(final)
        if st is None:
            return v.OUTCOME_ABSENT
        return v.OUTCOME_FOUND if self.remote_sha(final) == sha256 else v.OUTCOME_MISMATCH

    def ack_files(self, *, name: Optional[str] = None) -> list[tuple[str, bytes]]:
        """(name, content) of candidate acknowledgement files: "<name>.ack" when a name is given, else the ack
        directory's newest regular files (997s), bounded."""
        cfg = self.cfg
        if name is not None:
            path = posixpath.join(cfg["ack_directory"], f"{name}.ack")
            st = self._stat(path)
            if st is None or st.st_size > MAX_ACK_BYTES:
                return []
            with self.sftp.open(path, "rb") as fh:
                return [(f"{name}.ack", fh.read())]
        try:
            entries = self.sftp.listdir_attr(cfg["ack_directory"])
        except IOError:
            return []
        files = [e for e in entries if stat.S_ISREG(e.st_mode or 0) and not e.filename.startswith(".")
                 and (e.st_size or 0) <= MAX_ACK_BYTES]
        files.sort(key=lambda e: e.st_mtime or 0, reverse=True)
        out = []
        for e in files[:MAX_ACK_FILES]:
            with self.sftp.open(posixpath.join(cfg["ack_directory"], e.filename), "rb") as fh:
                out.append((e.filename, fh.read()))
        return out


def parse_ack_file(content: bytes) -> tuple[str, str, Optional[str]]:
    """(outcome, message, external id) from a "<name>.ack" file: a Tally RESPONSE, a JSON {status, id, message},
    or text beginning ACCEPTED / REJECTED."""
    text = content.decode("utf-8", "replace").strip()
    if text.startswith("<"):
        from app.services.erp.formats import tally

        try:
            return tally.correlate(tally.parse_response(content))
        except tally.TallyError as exc:
            return v.OUTCOME_MISMATCH, f"an acknowledgement that could not be read: {exc}", None
    if text.startswith("{"):
        import json

        try:
            doc = json.loads(text)
        except ValueError:
            return v.OUTCOME_MISMATCH, "an acknowledgement that is not valid JSON", None
        status = str(doc.get("status", "")).upper()
        ident = doc.get("id") or doc.get("external_id")
        if status in ("ACCEPTED", "OK", "POSTED", "DONE"):
            return v.OUTCOME_ACCEPTED, str(doc.get("message") or "accepted"), None if ident is None else str(ident)
        if status in ("REJECTED", "ERROR", "FAILED"):
            return v.OUTCOME_REJECTED, str(doc.get("message") or "rejected"), None
        return v.OUTCOME_MISMATCH, f"an acknowledgement with status {status!r}", None
    head, _, rest = text.partition(":")
    word = head.strip().upper()
    if word.startswith("ACCEPTED"):
        return v.OUTCOME_ACCEPTED, "accepted", (rest.strip() or None)
    if word.startswith("REJECTED"):
        return v.OUTCOME_REJECTED, rest.strip() or "rejected", None
    return v.OUTCOME_MISMATCH, f"an acknowledgement that says {text[:80]!r}", None


__all__ = ["Connection", "Delivery", "SftpError", "fingerprint", "load_private_key", "parse_ack_file", "settings"]
