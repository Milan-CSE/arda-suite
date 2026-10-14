#!/usr/bin/env python
"""Adversarial checks against a running HTTP MCP server.

mcp_check.py answers "does it work"; this answers "does it refuse". They are
separate because a server that passes the first and fails the second is the
dangerous outcome — it looks finished.

Raw urllib on purpose: an MCP client library negotiates and retries, which is
exactly the behaviour that can turn a rejection into something that looks like a
connection problem. Here the status code is read directly.

    ./bin/mem0-python mcp_auth_check.py [--url http://127.0.0.1:8812/mcp]
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18",
               "capabilities": {},
               "clientInfo": {"name": "mcp_auth_check", "version": "0"}},
}

ACCEPT = "application/json, text/event-stream"


def post(url, body, token=None, content_type="application/json", raw=None,
         host=None):
    """(status, body_text). Never returns the token or echoes it anywhere.

    `host` overrides the Host header without changing where the socket goes —
    that is how a proxied worker's request is reproduced against a loopback
    server, and the only way to exercise the DNS rebinding allowlist locally.
    """
    data = raw if raw is not None else json.dumps(body).encode()
    headers = {"Content-Type": content_type, "Accept": ACCEPT}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    if host:
        headers["Host"] = host
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, e)


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok)))
        print("  %-42s %s  %s" % (name, "PASS" if ok else "FAIL", detail))

    @property
    def failed(self):
        return [r for r in self.rows if not r[1]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8812/mcp")
    ap.add_argument("--allowed-host", default=os.environ.get("MEM0_MCP_ALLOWED_HOSTS", "").split(",")[0].strip(),
                    help="a host:port the RUNNING server was told to accept; "
                         "checked to answer 200. Skipped when empty.")
    args = ap.parse_args()

    from mcp_transport import load_token
    token = load_token()

    rep = Report()
    print("== mcp_auth_check ==")

    st, _ = post(args.url, INITIALIZE)
    rep.check("A. no token -> rejected", st in (401, 403), "HTTP %s" % st)

    st, _ = post(args.url, INITIALIZE, token="b" * 64)
    rep.check("B. wrong token -> rejected", st in (401, 403), "HTTP %s" % st)

    st, _ = post(args.url, INITIALIZE, token=token[:-1])
    rep.check("B2. truncated token -> rejected", st in (401, 403), "HTTP %s" % st)

    st, body = post(args.url, INITIALIZE, token=token)
    rep.check("C. correct token -> initialize", st == 200 and "serverInfo" in body,
              "HTTP %s" % st)

    # D–F. The Host header allowlist (DNS rebinding protection). A worker behind
    # Tailscale Serve or any proxy sends the name it dialled, which is checked
    # AFTER the token — so an unlisted name looks like an auth success followed
    # by an inexplicable 421. These pin all three outcomes.
    st, _ = post(args.url, INITIALIZE, token=token, host="evil.example.com:8812")
    rep.check("D. unlisted Host -> 421", st == 421, "HTTP %s" % st)

    st, body = post(args.url, INITIALIZE, token=token, host="127.0.0.1:8812")
    rep.check("E. loopback Host still works", st == 200 and "serverInfo" in body,
              "HTTP %s" % st)

    if args.allowed_host:
        st, body = post(args.url, INITIALIZE, token=token, host=args.allowed_host)
        rep.check("F. allowlisted Host -> 200",
                  st == 200 and "serverInfo" in body,
                  "%s -> HTTP %s" % (args.allowed_host, st))
    else:
        print("  %-42s SKIP  (no --allowed-host / MEM0_MCP_ALLOWED_HOSTS)"
              % "F. allowlisted Host -> 200")

    # G. Malformed input must be answered, not fatal. The server has to still be
    # serving afterwards — that second part is the real assertion.
    st, _ = post(args.url, None, token=token, raw=b"{ this is not json")
    ok_malformed = st not in (-1,)
    st2, _ = post(args.url, INITIALIZE, token=token)
    rep.check("G. malformed body -> no crash", ok_malformed and st2 == 200,
              "malformed HTTP %s, then initialize HTTP %s" % (st, st2))

    st, _ = post(args.url, {"jsonrpc": "2.0", "id": 2, "method": "no.such.method"},
                 token=token)
    st2, _ = post(args.url, INITIALIZE, token=token)
    rep.check("G2. unknown method -> no crash", st2 == 200,
              "unknown HTTP %s, then initialize HTTP %s" % (st, st2))

    st, _ = post(args.url, INITIALIZE, token=token, content_type="text/plain")
    rep.check("G3. wrong content-type -> refused not fatal",
              st != -1 and post(args.url, INITIALIZE, token=token)[0] == 200,
              "HTTP %s" % st)

    if rep.failed:
        print("FAIL: %d check" % len(rep.failed))
        return 1
    print("OK: %d check" % len(rep.rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
