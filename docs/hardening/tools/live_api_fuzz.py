"""Live API fuzzer (the 2026-10-09 bug hunt; found F-208, F-209, F-211, F-213).

Hits every operation in the app's OpenAPI document as a real, signed-in user and
prints every 5xx, timeout and dropped connection. Run it against a live stack
(API + worker + Postgres + Redis) while tailing the API log, then trace each hit.

    # once: dump the route table next to this script
    cd backend && python -c "import json; from app.main import app; json.dump(app.openapi(), open('../docs/hardening/tools/openapi.json','w'))"
    # read-only pass, then writes (writes change data: use a throwaway tenant or database)
    python docs/hardening/tools/live_api_fuzz.py get c-owner@e2e.example.com a-viewer@e2e.example.com
    python docs/hardening/tools/live_api_fuzz.py write f-owner@e2e.example.com

Each user's own organization and first workspace are used for {organization_id}
and {workspace_id}. Other ids are tried as real (harvested from list responses),
random UUIDs and garbage. Write bodies are generated from the schema: valid,
edge values (20 000 characters, NUL, emoji, 2^63, year 9999) and wrong types,
plus malformed JSON and an array. Password: E2E_PASSWORD or the e2e default.
"""
import json, os, random, re, sys, time, uuid
import httpx

BASE = os.environ.get("FUZZ_API_ORIGIN", "http://127.0.0.1:8000")
PW = os.environ.get("E2E_PASSWORD", "E2e-FlowPilot-Pass-2026!")
HERE = os.path.dirname(__file__)
SPEC = json.load(open(os.path.join(HERE, "openapi.json")))
IDS: dict = {}  # email -> (organization_id, workspace_id), resolved at sign-in


def resolve_ids(email, token):
    rows = httpx.get(f"{BASE}/api/v1/me/workspaces", headers={"Authorization": f"Bearer {token}"}, timeout=60).json()
    IDS[email] = (rows[0]["organization_id"], rows[0]["id"])
SKIP = re.compile(
    r"/auth/(logout|refresh|password|change-password|sessions/revoke|devices)|/me/delete|"
    r"/account/delete|/sessions/(revoke|all)|/mfa/(disable|enable)|/email-change|"
    r"/ownership-transfer|/billing/webhook|/webhooks/(stripe|dodo)|/scim/|/saml/|/oidc/|/sso/|"
    r"/leave$|/archive$|/delete-request|/erasure"
)
OUT = []


def login(email):
    r = httpx.post(f"{BASE}/api/v1/auth/login", data={"username": email, "password": PW}, timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


def resolve(s, depth=0):
    if depth > 8 or not isinstance(s, dict):
        return s or {}
    if "$ref" in s:
        name = s["$ref"].split("/")[-1]
        return resolve(SPEC["components"]["schemas"][name], depth + 1)
    for k in ("anyOf", "oneOf"):
        if k in s:
            opts = [o for o in s[k] if o.get("type") != "null"]
            if opts:
                return resolve(opts[0], depth + 1)
    if "allOf" in s:
        merged = {}
        for o in s["allOf"]:
            merged.update(resolve(o, depth + 1))
        return merged
    return s


def example(s, mode, depth=0):
    """mode: valid | edge | wrongtype"""
    s = resolve(s, depth)
    if depth > 6:
        return None
    t = s.get("type")
    if "enum" in s:
        return s["enum"][0]
    if "default" in s and mode == "valid" and s["default"] is not None:
        return s["default"]
    if t == "object" or "properties" in s:
        props = s.get("properties", {})
        req = set(s.get("required", []))
        keys = list(props) if mode != "valid" else [k for k in props if k in req] or list(props)[:4]
        return {k: example(props[k], mode, depth + 1) for k in keys}
    if t == "array":
        return [example(s.get("items", {}), mode, depth + 1)]
    if t == "integer":
        if mode == "edge":
            return random.choice([-1, 0, 2**63, 2**31, -(2**40)])
        if mode == "wrongtype":
            return "x"
        return max(1, s.get("minimum", 1) or 1)
    if t == "number":
        if mode == "edge":
            return random.choice([-1e308, 1e308, 0, -0.5])
        return 1.5
    if t == "boolean":
        return True if mode != "wrongtype" else "maybe"
    if t == "string":
        fmt = s.get("format")
        if mode == "wrongtype":
            return 12345
        if fmt == "uuid":
            return str(uuid.uuid4()) if mode == "valid" else random.choice(["not-a-uuid", str(uuid.uuid4())])
        if fmt == "date-time":
            return "2026-10-09T00:00:00Z" if mode == "valid" else random.choice(["9999-12-31T23:59:59Z", "0001-01-01T00:00:00Z", "2026-13-45T99:99:99"])
        if fmt == "date":
            return "2026-10-09" if mode == "valid" else random.choice(["9999-12-31", "0001-01-01"])
        if fmt == "email":
            return "fuzz@e2e.example.com"
        if mode == "edge":
            return random.choice(["", " ", "\u0000x", "😀" * 300, "A" * 20000, "'; select pg_sleep(0);--", "../../etc/passwd", "<script>x</script>", "%s%n", "\ud800"[:0] + "ｆｕｚｚ"])
        if s.get("pattern"):
            return "fuzz"
        return "fuzz-" + uuid.uuid4().hex[:6]
    return "fuzz"


def edge_query_values():
    return ["-1", "0", "999999999", "abc", "", "%00", "9" * 30, "2026-13-45", "null", "[]"]


def fill_path(path, tenant, real_ids, variant):
    org, ws = IDS[tenant]  # tenant: the user's email

    def sub(m):
        name = m.group(1)
        if name == "workspace_id":
            return ws
        if name == "organization_id":
            return org
        if variant == "real":
            prefix = path[: m.start()].rstrip("/")
            cands = real_ids.get(prefix)
            if cands:
                return random.choice(cands)
        if variant == "garbage":
            return random.choice(["not-a-uuid", "0", "%20", "..", "x" * 300])
        return str(uuid.uuid4())

    return re.sub(r"\{(\w+)\}", sub, path)


def harvest(real_ids, url_template, body):
    ids = []
    def walk(o, d=0):
        if d > 3:
            return
        if isinstance(o, dict):
            v = o.get("id") or o.get("key")
            if isinstance(v, (str, int)):
                ids.append(str(v))
            for x in o.values():
                if isinstance(x, (list, dict)) and d < 2:
                    walk(x, d + 1)
        elif isinstance(o, list):
            for x in o[:20]:
                walk(x, d + 1)
    walk(body)
    if ids:
        real_ids.setdefault(url_template, [])
        real_ids[url_template] = list(dict.fromkeys(real_ids[url_template] + ids))[:30]


def call(client, method, url, **kw):
    t = time.time()
    try:
        r = client.request(method, url, **kw)
    except httpx.ReadTimeout:
        OUT.append(("TIMEOUT", method, url, kw.get("json"), ""))
        print(f"!! TIMEOUT {method} {url}", flush=True)
        return None
    except httpx.TransportError as exc:
        body = kw.get("json")
        OUT.append(("RESET", method, url, str(body)[:300] if body is not None else None, repr(exc)))
        print(f"!! RESET {method} {url} {repr(exc)[:100]} body={str(body)[:200]}", flush=True)
        return None
    dt = time.time() - t
    if r.status_code >= 500:
        OUT.append((r.status_code, method, url, kw.get("json") if not isinstance(kw.get("json"), str) else None, r.text[:300]))
        print(f"!! {r.status_code} {method} {url} {r.text[:200]}", flush=True)
    elif dt > 15:
        OUT.append(("SLOW%.0fs" % dt, method, url, None, ""))
    return r


def ops():
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            yield path, method.upper(), op


def run(phase, users):
    random.seed(7)
    for email in users:
        tenant = email
        token = login(email)
        resolve_ids(email, token)
        client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        real_ids = {}
        n = 0
        # pass 1: GETs without params harvest ids (templates resolved per tenant)
        for path, method, op in ops():
            if method != "GET" or SKIP.search(path):
                continue
            n += 1
            if n % 50 == 0:  # tokens live 10 minutes
                client.headers["Authorization"] = f"Bearer {login(email)}"
            params_spec = [p for p in op.get("parameters", []) if p["in"] == "query"]
            url = fill_path(path, tenant, real_ids, "real")
            r = call(client, "GET", url)
            if r is not None and r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                try:
                    harvest(real_ids, path, r.json())
                except Exception:
                    pass
            # edge query params
            for p in params_spec:
                for v in edge_query_values()[:5]:
                    call(client, "GET", url, params={p["name"]: v})
            for variant in ("random", "garbage"):
                if re.search(r"\{(?!workspace_id|organization_id)\w+\}", path):
                    call(client, "GET", fill_path(path, tenant, real_ids, variant))
        # second GET pass with harvested real ids for detail routes
        for path, method, op in ops():
            if method != "GET" or SKIP.search(path) or not re.search(r"\{(?!workspace_id|organization_id)\w+\}", path):
                continue
            n += 1
            if n % 50 == 0:
                client.headers["Authorization"] = f"Bearer {login(email)}"
            for _ in range(2):
                call(client, "GET", fill_path(path, tenant, real_ids, "real"))
        if phase in ("write", "all"):
            for path, method, op in ops():
                if method == "GET" or SKIP.search(path):
                    continue
                n += 1
                if n % 30 == 0:
                    client.headers["Authorization"] = f"Bearer {login(email)}"
                body_schema = (op.get("requestBody", {}).get("content", {}).get("application/json", {}) or {}).get("schema")
                variants = ["real", "random", "garbage"] if method != "DELETE" else ["random", "garbage"]
                for variant in variants:
                    url = fill_path(path, tenant, real_ids, variant)
                    if body_schema is None:
                        if method != "DELETE":
                            call(client, method, url)
                        else:
                            call(client, method, url)
                        continue
                    for mode in ("valid", "edge", "wrongtype"):
                        try:
                            body = example(body_schema, mode)
                        except RecursionError:
                            body = {}
                        call(client, method, url, json=body)
                    call(client, method, url, content=b"{not json", headers={"Content-Type": "application/json"})
                    call(client, method, url, json=[])
        print(f"== {email}: done, {len(OUT)} problems so far", flush=True)
    print("TOTAL problems:", len(OUT))
    for o in OUT:
        print(o[0], o[1], o[2], str(o[4])[:150])


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2:] or ["c-owner@e2e.example.com"])
