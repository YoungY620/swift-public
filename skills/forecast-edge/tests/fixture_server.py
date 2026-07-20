#!/usr/bin/env python3
"""Local HTTP fixture server used by the Linux shell contract test."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_PUSD = "0x4444444444444444444444444444444444444444"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def send_fixture(self, name: str, content_type: str = "application/json") -> None:
        body = (FIXTURES / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value: object) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/@fixture-user":
            self.send_fixture("profile.html", "text/html")
        elif path == "/public-profile":
            self.send_json({"proxyWallet": "0x2222222222222222222222222222222222222222", "name": "fixture-user"})
        elif path == "/positions":
            self.send_fixture("positions.json")
        elif path == "/v1/info/assets":
            self.send_json(
                [
                    {
                        "asset": "pUSD",
                        "address": FIXTURE_PUSD,
                        "decimals": 6,
                        "collateral_ratio": "1.00",
                        "withdrawal_fee": "0.000000",
                    }
                ]
            )
        elif path == "/markets":
            self.send_json([json.loads((FIXTURES / "market.json").read_text())])
        elif path in {"/markets/540817", "/markets/slug/sample-event"}:
            self.send_fixture("market.json")
        elif path == "/book":
            self.send_fixture("book.json")
        elif path == "/fee-rate":
            self.send_json({"base_fee": 400})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/rpc":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        contract = str(request.get("params", [{}])[0].get("to", "")).lower()
        if contract != FIXTURE_PUSD:
            self.send_json({"jsonrpc": "2.0", "id": 1, "error": {"message": "wrong collateral"}})
            return
        self.send_json({"jsonrpc": "2.0", "id": 1, "result": hex(123_456_789)})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Path(args.port_file).write_text(str(server.server_address[1]), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
