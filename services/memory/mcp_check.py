#!/usr/bin/env python
"""Drive the MCP server through a real client and assert the memory semantics.

The point is transport parity: the same checks run over stdio and over HTTP, so
"the HTTP server works" means the same thing as "the local one works" instead of
being a separate, weaker claim. A transport change that quietly altered scoping
or layered recall would show up here as a different report.

    ./bin/mem0-python mcp_check.py --transport stdio
    ./bin/mem0-python mcp_check.py --transport http --url http://127.0.0.1:8812/mcp

HTTP mode reads the bearer token from $MEM0_MCP_TOKEN or the token file, exactly
like the server does — nothing is passed on the command line, where it would
land in shell history and `ps`.

Writes go to a disposable scope and are deleted again on the way out, so running
this never pollutes the shared brain.
"""

import argparse
import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

VERIFY_SCOPE = "__mcp_verify__"
PERSONAL = "personal"

# Distinctive enough that a semantic search can only match it by actually having
# stored it, and anchored so the shape matches what the store really holds.
FACT = "mcp_check kanarya: dogrulama scope'u __mcp_verify__ icin port 8812 kullanilir"
QUERY = "hangi port dogrulama scope'u icin kullanilir?"


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print("  %-34s %s%s" % (name, "PASS" if ok else "FAIL",
                                ("  " + detail) if detail else ""))

    @property
    def failed(self):
        return [r for r in self.rows if not r[1]]


@asynccontextmanager
async def stdio_session():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=os.path.join(HERE, "bin", "mem0-python"),
        args=[os.path.join(HERE, "mcp_server.py")],
        # Inherit nothing transport-related: this must exercise the DEFAULT path.
        env={k: v for k, v in os.environ.items()
             if not k.startswith("MEM0_MCP_")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            yield s


@asynccontextmanager
async def http_session(url):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from mcp_transport import load_token

    headers = {"Authorization": "Bearer %s" % load_token()}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            yield s


def text_of(result):
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


async def run_checks(session, rep):
    await session.initialize()
    rep.check("initialize", True)

    tools = {t.name for t in (await session.list_tools()).tools}
    rep.check("list_tools", {"add_memory", "search_memory", "list_memories"} <= tools,
              ",".join(sorted(tools)))

    # Read-only first: proves the store is reachable before anything is written.
    listed = text_of(await session.call_tool("list_memories", {"scope": PERSONAL}))
    rep.check("list_memories(personal)", "No memories stored" not in listed,
              "%d satir" % len(listed.splitlines()))

    added = text_of(await session.call_tool(
        "add_memory",
        {"text": FACT, "scope": VERIFY_SCOPE, "agent": "mcp-check", "verbatim": True},
    ))
    rep.check("add_memory(disposable scope)", "Stored in scope" in added)

    found = text_of(await session.call_tool(
        "search_memory", {"query": QUERY, "scope": VERIFY_SCOPE}))
    rep.check("search finds what was written", "8812" in found)

    # Layered recall: a project scope also sees the shared tier...
    rep.check("layered recall reaches personal",
              "(%s/" % PERSONAL in found or await _personal_visible(session),
              "")

    # ...but never another project's bucket.
    other = text_of(await session.call_tool(
        "search_memory", {"query": QUERY, "scope": "some-unrelated-project"}))
    rep.check("scope isolation (no leak)", VERIFY_SCOPE not in other)

    strict = text_of(await session.call_tool(
        "search_memory",
        {"query": QUERY, "scope": VERIFY_SCOPE, "include_shared": False}))
    rep.check("include_shared=False stays project-only",
              all("(%s/" % PERSONAL not in ln for ln in strict.splitlines()))

    scoped = text_of(await session.call_tool(
        "list_memories", {"scope": VERIFY_SCOPE}))
    rep.check("list_memories(scope) filters", "8812" in scoped
              and "(%s/" % PERSONAL not in scoped)


async def _personal_visible(session):
    """Layered recall is score-ranked, so a project query need not surface a
    personal row. Confirm the shared tier is reachable rather than demanding it
    outrank the exact match."""
    got = text_of(await session.call_tool(
        "search_memory", {"query": "qdrant ollama mem0", "scope": VERIFY_SCOPE}))
    return "(%s/" % PERSONAL in got


def cleanup():
    """Delete everything this run wrote. Runs even when checks failed."""
    from config import get_memory

    try:
        m = get_memory()
        res = m.get_all(filters={"user_id": os.environ.get("MEM0_OWNER", "default"),
                                 "run_id": VERIFY_SCOPE}, top_k=1000)
        rows = res.get("results", [])
        for r in rows:
            m.delete(r["id"])
        return len(rows)
    except Exception as ex:
        print("  cleanup FAILED (%s) — remove scope %r by hand" % (ex, VERIFY_SCOPE))
        return -1


async def main_async(args):
    rep = Report()
    print("== mcp_check (%s) ==" % args.transport)
    opener = (stdio_session() if args.transport == "stdio"
              else http_session(args.url))
    async with opener as session:
        await run_checks(session, rep)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    ap.add_argument("--url", default="http://127.0.0.1:8812/mcp")
    args = ap.parse_args()

    # A verify id makes concurrent/rerun rows distinguishable in the log.
    print("run:", uuid.uuid4().hex[:8])
    try:
        rep = asyncio.run(main_async(args))
    finally:
        removed = cleanup()
        print("cleanup: %s satir silindi" % removed)

    if rep.failed:
        print("FAIL: %d check" % len(rep.failed))
        return 1
    print("OK: %d check" % len(rep.rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
