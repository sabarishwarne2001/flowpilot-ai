"""A DNS server for the browser journeys and the tests: it answers TXT queries from a JSON file.

TEST ONLY. Domain verification asks real resolvers (DNS_RESOLVERS) for a TXT record the customer
publishes at their DNS provider. A test cannot publish at a real provider, and cannot bind port 53
for a local server, so this server listens on another port (DNS_RESOLVERS accepts `host:port`) and
answers from a file the test writes, over the real DNS protocol: FlowPilot's lookup code, from the
socket up, is the code under test.

    python -m scripts.e2e_dns serve --port 5354 --records /tmp/flowpilot-e2e-logs/dns-records.json
    python -m scripts.e2e_dns publish --records FILE --name acme.example --value "flowpilot-site-verification=..."

The records file is `{"name": ["value", ...]}`, names lower-case without the trailing dot. A name
that is not in the file is answered NXDOMAIN.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import socketserver
import sys
import threading
from typing import Callable

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset

Records = Callable[[], dict[str, list[str]]]


def _from_file(path: pathlib.Path) -> Records:
    def read() -> dict[str, list[str]]:
        try:
            return {k.lower().rstrip("."): list(v) for k, v in json.loads(path.read_text()).items()}
        except (FileNotFoundError, ValueError):
            return {}

    return read


def answer(wire: bytes, records: dict[str, list[str]]) -> bytes:
    query = dns.message.from_wire(wire)
    response = dns.message.make_response(query)
    response.flags |= dns.flags.AA
    for question in query.question:
        name = question.name.to_text().rstrip(".").lower()
        values = records.get(name)
        if values is None:
            response.set_rcode(dns.rcode.NXDOMAIN)
        elif question.rdtype == dns.rdatatype.TXT:
            response.answer.append(dns.rrset.from_text_list(
                question.name, 60, "IN", "TXT", [json.dumps(value) for value in values]))
    return response.to_wire()


def server(host: str, port: int, records: Records) -> socketserver.ThreadingUDPServer:
    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            data, sock = self.request
            sock.sendto(answer(data, records()), self.client_address)

    return socketserver.ThreadingUDPServer((host, port), Handler)


def start_in_thread(records: Records, host: str = "127.0.0.1") -> tuple[socketserver.ThreadingUDPServer, int]:
    running = server(host, 0, records)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    return running, running.server_address[1]


def publish(path: pathlib.Path, name: str, value: str) -> None:
    current = _from_file(path)()
    current.setdefault(name.lower().rstrip("."), [])
    if value not in current[name.lower().rstrip(".")]:
        current[name.lower().rstrip(".")].append(value)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2))
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("ENVIRONMENT", "").strip().lower() == "production":
        print("refusing: this is a test stand-in and ENVIRONMENT is production", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["serve", "publish"])
    parser.add_argument("--records", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5354)
    parser.add_argument("--name")
    parser.add_argument("--value")
    args = parser.parse_args(argv)
    path = pathlib.Path(args.records)
    if args.command == "publish":
        if not (args.name and args.value):
            parser.error("publish needs --name and --value")
        publish(path, args.name, args.value)
        print(json.dumps({"published": args.name, "value": args.value}))
        return 0
    print(f"e2e DNS answering on {args.host}:{args.port} from {path}", flush=True)
    server(args.host, args.port, _from_file(path)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
