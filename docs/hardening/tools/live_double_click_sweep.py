"""Live double-click sweep (the 2026-10-09 bug hunt; found F-212 and F-214).

Sends every POST/PUT/PATCH N times at once with the same valid body, as a real
user, and prints every 5xx. Watch the health check while it runs: a request that
waits on a lock on the event loop freezes the whole API (F-212).

    python docs/hardening/tools/live_double_click_sweep.py c-owner@e2e.example.com 4

Changes data: use a throwaway tenant or database. Needs openapi.json next to
this script (see live_api_fuzz.py).
"""
import asyncio, json, os, re, sys, uuid
import httpx
sys.path.insert(0, os.path.dirname(__file__))
import live_api_fuzz as fuzz  # schema helpers and id resolution

BASE = fuzz.BASE
OUT = []


async def burst(client, method, url, body, n):
    reqs = [client.request(method, url, json=body) for _ in range(n)]
    res = await asyncio.gather(*reqs, return_exceptions=True)
    codes = []
    for r in res:
        if isinstance(r, Exception):
            codes.append("EXC")
            OUT.append(("EXC", method, url, repr(r)[:200]))
            continue
        codes.append(r.status_code)
        if r.status_code >= 500:
            OUT.append((r.status_code, method, url, r.text[:200]))
            print(f"!! {r.status_code} {method} {url} body={json.dumps(body)[:200]}", flush=True)
    return codes


async def main(email, n):
    tenant = email
    token = fuzz.login(email)
    fuzz.resolve_ids(email, token)
    real_ids = {}
    async with httpx.AsyncClient(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=90) as client:
        # harvest ids from list endpoints
        for path, method, op in fuzz.ops():
            if method != "GET" or fuzz.SKIP.search(path) or re.search(r"\{(?!workspace_id|organization_id)\w+\}", path):
                continue
            r = await client.get(fuzz.fill_path(path, tenant, {}, "real"))
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                try:
                    fuzz.harvest(real_ids, path, r.json())
                except Exception:
                    pass
        count = 0
        for path, method, op in fuzz.ops():
            if method not in ("POST", "PUT", "PATCH") or fuzz.SKIP.search(path):
                continue
            count += 1
            if count % 25 == 0:
                client.headers["Authorization"] = f"Bearer {fuzz.login(email)}"
            schema = (op.get("requestBody", {}).get("content", {}).get("application/json", {}) or {}).get("schema")
            url = fuzz.fill_path(path, tenant, real_ids, "real")
            body = None
            if schema is not None:
                try:
                    body = fuzz.example(schema, "valid")
                except RecursionError:
                    body = {}
                # unique-ish strings so the first request can succeed
                def uniq(o):
                    if isinstance(o, dict):
                        return {k: uniq(v) for k, v in o.items()}
                    if isinstance(o, list):
                        return [uniq(v) for v in o]
                    if isinstance(o, str) and o.startswith("fuzz-"):
                        return "race-" + uuid.uuid4().hex[:6]
                    return o
                body = uniq(body)
            await burst(client, method, url, body, n)
    print("TOTAL", len(OUT))
    for o in OUT:
        print(*o)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3))
