"""Every outbound connection in `app/` passes the egress gate (campaign session 3, F-291).

`app/core/egress.py` is the one place that decides whether the platform may open a connection:
the SSRF rules (no loopback, metadata service, private ranges; connect to the validated address),
the sovereign edition's deny mode and an Enterprise tenant's egress lockdown. That only holds if
nothing opens a socket around it. The rule was enforced by `verify_arch50.py`, which was archived
with the other historical scripts at the final release, so since then nothing in CI stopped a new
`httpx.get(...)` or `smtplib.SMTP(...)` on a tenant-chosen host.

This walks every module under `app/` and finds each call that opens a network client: sockets,
`http.client`, `httpx`, `requests`, `aiohttp`, `smtplib`, `paramiko`, `boto3`, `urllib`, and the
provider SDKs. Outside the gate itself (`egress.py`, `ssrf_client.py`) each must be visibly handed
the gate: a transport or `http_client` argument, `egress.attach_boto(...)` around it, or an
`egress.` decision in the same function (`guard`, `open_connection`). A subclass of one of
these classes (the pinned SMTP clients) must take its socket from the gate in one of its methods.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
GATE_MODULES = {"core/egress.py", "core/ssrf_client.py"}

#: (module, attribute) pairs that open a connection, or build something that will.
NETWORK = {
    ("socket", "create_connection"), ("socket", "socket"),
    ("http.client", "HTTPConnection"), ("http.client", "HTTPSConnection"),
    ("httpx", "Client"), ("httpx", "AsyncClient"), ("httpx", "get"), ("httpx", "post"), ("httpx", "put"),
    ("httpx", "patch"), ("httpx", "delete"), ("httpx", "request"), ("httpx", "stream"),
    ("requests", "Session"), ("requests", "get"), ("requests", "post"), ("requests", "put"),
    ("requests", "patch"), ("requests", "delete"), ("requests", "request"),
    ("aiohttp", "ClientSession"), ("websockets", "connect"), ("ftplib", "FTP"),
    ("smtplib", "SMTP"), ("smtplib", "SMTP_SSL"),
    ("paramiko", "Transport"), ("paramiko", "SSHClient"),
    ("boto3", "client"), ("boto3", "resource"),
    ("urllib.request", "urlopen"),
    ("openai", "OpenAI"), ("openai", "AsyncOpenAI"), ("openai", "AzureOpenAI"),
    ("groq", "Groq"), ("groq", "AsyncGroq"), ("anthropic", "Anthropic"), ("anthropic", "AsyncAnthropic"),
    ("genai", "Client"), ("google.genai", "Client"), ("stripe", "StripeClient"),
}
GATED_KEYWORDS = {"transport", "http_client", "http_options"}


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Name):
        return node.id
    return None


def _network_call(call: ast.Call, imported: dict[str, tuple[str, str]], modules: dict[str, str]) -> str | None:
    name = _dotted(call.func)
    if not name:
        return None
    if "." in name:
        head, attr = name.rsplit(".", 1)
        module = modules.get(head, head)
        return f"{module}.{attr}" if (module, attr) in NETWORK else None
    if name in imported and imported[name] in NETWORK:
        return ".".join(imported[name])
    return None


def _violations() -> list[str]:
    found: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        if relative in GATE_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), relative)
        imported: dict[str, tuple[str, str]] = {}
        modules: dict[str, str] = {}
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imported[alias.asname or alias.name] = (node.module, alias.name)
                    modules[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    modules[alias.asname or alias.name] = alias.name
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [_dotted(base) or "" for base in node.bases]
                network_base = any(
                    (modules.get(b.rsplit(".", 1)[0], b.rsplit(".", 1)[0]), b.rsplit(".", 1)[1]) in NETWORK
                    for b in bases if "." in b
                ) or any(imported.get(b) in NETWORK for b in bases)
                if network_base and not any(
                    isinstance(inner, ast.Call) and (_dotted(inner.func) or "").startswith("egress.")
                    for inner in ast.walk(node)
                ):
                    found.append(f"app/{relative}:{node.lineno} class {node.name} opens connections without the egress gate")
                continue
            if not isinstance(node, ast.Call):
                continue
            target = _network_call(node, imported, modules)
            if target is None:
                continue
            if any(keyword.arg in GATED_KEYWORDS for keyword in node.keywords):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Call) and (_dotted(parent.func) or "").endswith("attach_boto"):
                continue
            function = parent
            while function is not None and not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = parents.get(function)
            decided = function is not None and any(
                isinstance(inner, ast.Call) and (_dotted(inner.func) or "").startswith("egress.")
                for inner in ast.walk(function)
            )
            if not decided:
                found.append(f"app/{relative}:{node.lineno} {target}(...) is opened without the egress gate")
    return found


def test_no_outbound_client_is_opened_around_the_gate() -> None:
    assert _violations() == []


def test_the_scan_catches_an_ungated_client(tmp_path, monkeypatch) -> None:
    """The scan is not vacuous: a plain httpx.get on a tenant URL is reported."""
    module = tmp_path / "app" / "services"
    module.mkdir(parents=True)
    (module / "leak.py").write_text(
        "import httpx\n\n\ndef fetch(url):\n    return httpx.get(url)\n", encoding="utf-8"
    )
    (module / "fine.py").write_text(
        "from app.core import egress\nimport smtplib\n\n\ndef send(host):\n"
        "    egress.guard(egress.SMTP_TENANT, host, 25)\n    return smtplib.SMTP(host)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(__import__(__name__, fromlist=["APP"]), "APP", tmp_path / "app")
    (module / "subclass.py").write_text(
        "import smtplib\n\n\nclass Direct(smtplib.SMTP):\n    pass\n", encoding="utf-8"
    )
    assert _violations() == [
        "app/services/leak.py:5 httpx.get(...) is opened without the egress gate",
        "app/services/subclass.py:4 class Direct opens connections without the egress gate",
    ]
