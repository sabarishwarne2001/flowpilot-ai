"""Live cross-tenant (IDOR) sweep over every operation in the API (campaign session 3).

The question for each of the ~620 operations: can a user of tenant A, or tenant A's API key,
read or change something of tenant B by putting B's ids in the request? Expected answer: never a
2xx, never one of B's values in a response.

How it works, against a live stack (API + worker + Postgres + Redis, the e2e seed):
1. The VICTIM signs in and every GET route is walked twice with the victim's organization,
   workspace and harvested ids; every id-looking value in the responses is collected by the name
   it was found under (`id` of a list item belongs to the child route's parameter; `*_id` keys are
   kept under their own name) and every UUID the victim can see is remembered for leak checks.
2. The ATTACKER (another tenant) then calls every operation in three variants:
     foreign-container : the victim's organization/workspace in the path (+ victim child ids);
     own-container     : the attacker's own organization/workspace with the victim's child ids;
     body/query ids    : victim ids in `*_id` query parameters and body fields.
   Write operations get a schema-valid body, so the authorization check, not validation, decides.
3. Every 2xx is printed as SUSPECT with the number of victim values in the body; a 2xx write is
   always SUSPECT. Each suspect is then traced by hand (some 2xx are fine: an empty list filtered
   to the caller's own tenant contains no victim value).

    cd backend && python -c "import json; from app.main import app; json.dump(app.openapi(), open('../docs/hardening/tools/openapi.json','w'))"
    python docs/hardening/tools/live_idor_sweep.py --victim c-owner@e2e.example.com --attacker b-owner@e2e.example.com
    python docs/hardening/tools/live_idor_sweep.py --victim c-owner@e2e.example.com --attacker b-owner@e2e.example.com --writes
    python docs/hardening/tools/live_idor_sweep.py ... --api-key      # the attacker's API key, every scope

Writes can change the victim's data if a defect exists: run on the e2e database, never on real data.
Password: E2E_PASSWORD or the e2e default.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from live_api_fuzz import SKIP, example  # noqa: E402  (schema-valid bodies, the same skip list)

BASE = os.environ.get("FUZZ_API_ORIGIN", "http://127.0.0.1:8000")
PW = os.environ.get("E2E_PASSWORD", "E2e-FlowPilot-Pass-2026!")
HERE = os.path.dirname(__file__)
SPEC = json.load(open(os.path.join(HERE, "openapi.json")))
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PARAM_RE = re.compile(r"\{(\w+)\}")
CONTAINER = {"organization_id", "workspace_id"}
# Routes that are public by design (token links, discovery, health) or need a non-user credential.
PUBLIC = re.compile(
    r"^/api/v1/(health|public/document-requests|public/calendar|calendar-feeds|branding/(logo|favicon)"
    r"|internal/tls|auth/|sso/|saml/|oidc/|invitations/(preview|accept)|billing/webhook)|^/scim/|^/health"
)


class Session:
    def __init__(self, email: str, api_key: bool = False) -> None:
        self.email = email
        self.calls = 0
        self.api_key = None
        self.client = httpx.Client(base_url=BASE, timeout=60)
        self._login()
        rows = self.get("/api/v1/me/workspaces").json()
        self.org, self.ws = rows[0]["organization_id"], rows[0]["id"]
        if api_key:
            created = self.client.post(
                f"/api/v1/organizations/{self.org}/api-keys",
                json={"name": f"idor-sweep-{uuid.uuid4().hex[:6]}", "scopes": _all_scopes()},
            )
            created.raise_for_status()
            self.api_key = created.json().get("token") or created.json().get("api_key")
            self.client.headers["Authorization"] = f"Bearer {self.api_key}"

    def _login(self) -> None:
        r = httpx.post(f"{BASE}/api/v1/auth/login", data={"username": self.email, "password": PW}, timeout=60)
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    def request(self, method: str, url: str, **kw):
        self.calls += 1
        if self.api_key is None and self.calls % 40 == 0:  # access tokens live ten minutes
            self._login()
        try:
            return self.client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            print(f"!! {method} {url}: {exc!r}"[:200], flush=True)
            return None

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)


def _all_scopes() -> list[str]:
    enum = SPEC["components"]["schemas"].get("ApiKeyScope", {}).get("enum")
    return enum or ["work_items:read", "work_items:write"]


def ops():
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                yield path, method.upper(), op


def child_param(list_path: str) -> str | None:
    """The parameter of the detail route right under a list route: /x/items -> item_id."""
    for path in SPEC["paths"]:
        if path.startswith(list_path + "/{") and path.count("/") == list_path.count("/") + 1:
            return PARAM_RE.search(path[len(list_path):]).group(1)
    return None


def harvest(ids: dict[str, list[str]], seen: set[str], path: str, body) -> None:
    child = child_param(path)

    def keep(name: str, value: str) -> None:
        bucket = ids.setdefault(name, [])
        if value not in bucket and len(bucket) < 12:
            bucket.append(value)

    def walk(node, depth: int, top_list: bool) -> None:
        if depth > 5:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and UUID_RE.fullmatch(value):
                    seen.add(value)
                    if key == "id" and top_list and child:
                        keep(child, value)
                    elif key.endswith("_id"):
                        keep(key, value)
                elif isinstance(value, (dict, list)):
                    walk(value, depth + 1, key in ("items", "results", "data") or top_list and depth == 0)
        elif isinstance(node, list):
            for item in node[:25]:
                walk(item, depth + 1, top_list or depth == 0)

    walk(body, 0, isinstance(body, list))


def _path_schemas() -> dict[str, dict[str, dict]]:
    """path -> parameter name -> schema, so typed parameters get a value of their type."""
    out: dict[str, dict[str, dict]] = {}
    for path, item in SPEC["paths"].items():
        for op in item.values():
            for parameter in op.get("parameters", []) if isinstance(op, dict) else []:
                if parameter.get("in") == "path":
                    out.setdefault(path, {})[parameter["name"]] = parameter.get("schema", {})
    return out


PATH_SCHEMAS = _path_schemas()


#: Free-text path parameters the routes validate against a vocabulary.
KNOWN_VALUES = {"kind": "EXTRACTION", "object_type": "DOCUMENT", "decision_type": "EXTRACTION_AUTO_APPROVE"}


def _typed(path: str, name: str) -> str | None:
    if name in KNOWN_VALUES:
        return KNOWN_VALUES[name]
    schema = PATH_SCHEMAS.get(path, {}).get(name, {})
    if "$ref" in schema:
        schema = SPEC["components"]["schemas"].get(schema["$ref"].split("/")[-1], {})
    if schema.get("enum"):
        return str(schema["enum"][0])
    if schema.get("type") == "integer":
        return "1"
    if schema.get("type") == "string" and schema.get("format") != "uuid" and not name.endswith("_id"):
        return None  # free text (a key, a slug, a kind): no typed default
    return None


def fill(path: str, org: str, ws: str, ids: dict[str, list[str]]) -> str | None:
    missing = False

    def sub(match):
        nonlocal missing
        name = match.group(1)
        if name == "organization_id":
            return org
        if name == "workspace_id":
            return ws
        typed = _typed(path, name)
        if typed is not None:
            return typed
        values = ids.get(name)
        if not values:
            missing = True
            return str(uuid.uuid4())
        return values[0]

    filled = PARAM_RE.sub(sub, path)
    return None if missing else filled


def victim_body(op, victim_ids: dict[str, list[str]]):
    schema = (op.get("requestBody", {}).get("content", {}).get("application/json", {}) or {}).get("schema")
    if schema is None:
        return None
    try:
        body = example(schema, "valid")
    except RecursionError:
        return {}
    if isinstance(body, dict):
        for key in list(body):
            if key.endswith("_id") and victim_ids.get(key):
                body[key] = victim_ids[key][0]
            elif key.endswith("_ids") and victim_ids.get(key[:-1]):
                body[key] = victim_ids[key[:-1]][:2]
    return body


def victim_query(op, victim_ids: dict[str, list[str]], victim: Session) -> dict[str, str]:
    params: dict[str, str] = {}
    for parameter in op.get("parameters", []):
        if parameter.get("in") != "query":
            continue
        name = parameter["name"]
        if name == "workspace_id":
            params[name] = victim.ws
        elif name == "organization_id":
            params[name] = victim.org
        elif name.endswith("_id") and victim_ids.get(name):
            params[name] = victim_ids[name][0]
    return params


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--victim", required=True)
    parser.add_argument("--attacker", required=True)
    parser.add_argument("--writes", action="store_true")
    parser.add_argument("--api-key", action="store_true", help="attack with the attacker's API key")
    parser.add_argument("--report", default="/tmp/idor-sweep.json")
    parser.add_argument("--victim-workspace", help="the victim workspace (default: the victim's first); "
                        "use one the attacker, a member of the same organization, has no grant in")
    args = parser.parse_args()

    victim = Session(args.victim)
    if args.victim_workspace:
        victim.ws = args.victim_workspace
    ids: dict[str, list[str]] = {}
    seen: set[str] = set()
    for _ in range(2):
        for path, method, op in ops():
            if method != "GET" or SKIP.search(path) or PUBLIC.search(path):
                continue
            url = fill(path, victim.org, victim.ws, ids)
            if url is None:
                continue
            r = victim.get(url)
            if r is not None and r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                try:
                    harvest(ids, seen, path, r.json())
                except ValueError:
                    pass
    seen -= {victim.org, victim.ws}
    print(f"victim {args.victim}: {sum(len(v) for v in ids.values())} ids under {len(ids)} names, "
          f"{len(seen)} distinct values", flush=True)

    attacker = Session(args.attacker, api_key=args.api_key)
    findings, statuses, attempted, others = [], {}, 0, []
    for path, method, op in ops():
        if PUBLIC.search(path) or SKIP.search(path):
            continue
        if method != "GET" and not args.writes:
            continue
        names = set(PARAM_RE.findall(path))
        variants = [("foreign-container", victim.org, victim.ws)]
        if names - CONTAINER:
            variants.append(("own-container", attacker.org, attacker.ws))
        elif not names:
            variants = [("body/query", attacker.org, attacker.ws)]
        for label, org, ws in variants:
            url = fill(path, org, ws, ids)
            if url is None:
                url = PARAM_RE.sub(
                    lambda m: org if m.group(1) == "organization_id" else ws if m.group(1) == "workspace_id"
                    else (_typed(path, m.group(1)) or str(uuid.uuid4())),
                    path,
                )
            body = victim_body(op, ids) if method != "GET" else None
            params = victim_query(op, ids, victim)
            r = attacker.request(method, url, params=params or None, json=body)
            attempted += 1
            if r is None:
                continue
            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
            if r.status_code != 404:
                others.append({"status": r.status_code, "method": method, "path": path, "variant": label,
                               "body": r.text[:160]})
            if r.status_code >= 500:
                findings.append({"kind": "5xx", "status": r.status_code, "method": method, "url": url, "variant": label, "body": r.text[:200]})
                print(f"!! {r.status_code} {method} {url}", flush=True)
            if 200 <= r.status_code < 300:
                text = r.text
                leaked = sorted(v for v in seen if v in text and v not in url and v not in json.dumps(params) + json.dumps(body))
                if leaked or method != "GET":
                    findings.append({"kind": "SUSPECT", "status": r.status_code, "method": method, "url": url,
                                     "variant": label, "leaked": len(leaked), "sample": leaked[:3], "body": text[:300]})
                    print(f"SUSPECT {r.status_code} {method} {url} [{label}] leaked={len(leaked)}", flush=True)
    print(f"attempted {attempted} requests; statuses {dict(sorted(statuses.items()))}; findings {len(findings)}")
    json.dump({"findings": findings, "statuses": statuses, "attempted": attempted, "not_404": others}, open(args.report, "w"), indent=1)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
