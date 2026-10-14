#!/usr/bin/env python3
"""The memory access layer: which backend answers, and how its replies are read.

Run: .venv/bin/python -m unittest test_mem0_backend -v
"""

import json
import unittest

import mem0_backend as mb


class TestBackendSelection(unittest.TestCase):

    def test_unset_is_local(self):
        """The brain machine sets nothing and must keep its existing path."""
        self.assertEqual(mb.backend_name(env={}), mb.LOCAL)

    def test_empty_value_is_local(self):
        self.assertEqual(mb.backend_name(env={"MEM0_BACKEND": "  "}), mb.LOCAL)

    def test_local_is_local(self):
        self.assertEqual(mb.backend_name(env={"MEM0_BACKEND": "local"}), mb.LOCAL)

    def test_remote_is_opt_in(self):
        self.assertEqual(mb.backend_name(env={"MEM0_BACKEND": "remote"}), mb.REMOTE)

    def test_case_and_whitespace_tolerated(self):
        self.assertEqual(mb.backend_name(env={"MEM0_BACKEND": " Remote "}), mb.REMOTE)

    def test_typo_is_invalid_not_local(self):
        """`remot` on a worker must not mean "write to whatever Qdrant is
        nearby" — that would lose the session's memory silently."""
        self.assertEqual(mb.backend_name(env={"MEM0_BACKEND": "rmote"}), mb.INVALID)


class TestInvalidBackendRefuses(unittest.TestCase):

    BAD = {"MEM0_BACKEND": "remot"}

    def test_write_returns_none_so_the_cursor_holds(self):
        self.assertIsNone(mb.write("text", "scope", env=self.BAD))

    def test_write_does_not_reach_either_backend(self):
        calls = []
        orig_l, orig_r = mb._write_local, mb._write_remote
        mb._write_local = lambda *a: calls.append("local")
        mb._write_remote = lambda *a: calls.append("remote")
        try:
            mb.write("text", "scope", env=self.BAD)
        finally:
            mb._write_local, mb._write_remote = orig_l, orig_r
        self.assertEqual(calls, [])

    def test_read_raises_so_recall_injects_nothing(self):
        with self.assertRaises(mb.McpError):
            mb.rows(env=self.BAD)

    def test_the_reason_names_the_offending_value(self):
        msg = mb._invalid_backend_message(env=self.BAD)
        self.assertIn("remot", msg)
        self.assertIn("MEM0_BACKEND", msg)


class TestQdrantEndpoint(unittest.TestCase):
    """Only the local backend knows this address — see the worker test in
    test_recall_inject that asserts the hook itself does not."""

    def test_defaults_to_the_local_store(self):
        self.assertIn("localhost:6333", mb._qdrant_scroll_url(env={}))

    def test_honours_the_same_env_vars_as_config_py(self):
        url = mb._qdrant_scroll_url(env={"MEM0_QDRANT_HOST": "brain.internal",
                                         "MEM0_QDRANT_PORT": "7333"})
        self.assertIn("brain.internal:7333", url)


class TestParseAdded(unittest.TestCase):
    """`add_memory` replies in prose; the cursor logic depends on reading it
    correctly, since [] and None mean very different things to the caller."""

    def test_reads_add_lines(self):
        got = mb._parse_added("Stored in scope 'x' as 'y':\n[ADD] first\n[ADD] second")
        self.assertEqual([g["memory"] for g in got], ["first", "second"])

    def test_nothing_extracted_is_an_empty_list_not_a_failure(self):
        self.assertEqual(
            mb._parse_added("No new facts were extracted (nothing durable to store)."),
            [])

    def test_ignores_non_add_events(self):
        self.assertEqual(mb._parse_added("[UPDATE] changed\n[DELETE] gone"), [])


class TestParseListing(unittest.TestCase):
    """`list_memories` renders one line per row; recall needs the fields back."""

    LINE = "[2026-08-09 14:34] (sample-app/claude-code) prisma migrate diff yol ister"

    def test_extracts_all_fields(self):
        got = mb.parse_listing(self.LINE)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["run_id"], "sample-app")
        self.assertEqual(got[0]["agent_id"], "claude-code")
        self.assertEqual(got[0]["created_at"], "2026-08-09 14:34")
        self.assertIn("prisma migrate", got[0]["memory"])

    def test_dash_means_absent_not_a_scope_named_dash(self):
        got = mb.parse_listing("[2026-08-09 14:34] (-/-) something")
        self.assertIsNone(got[0]["run_id"])
        self.assertIsNone(got[0]["agent_id"])

    def test_memory_text_containing_parens_survives(self):
        got = mb.parse_listing("[2026-08-09 14:34] (p/a) use foo() not bar()")
        self.assertEqual(got[0]["memory"], "use foo() not bar()")

    def test_malformed_lines_are_skipped_not_fatal(self):
        """One odd row must not cost a session its entire recall."""
        text = "garbage\n[broken\n%s\n[2026] no attrs" % self.LINE
        self.assertEqual(len(mb.parse_listing(text)), 1)

    def test_empty_input(self):
        self.assertEqual(mb.parse_listing(""), [])
        self.assertEqual(mb.parse_listing(None), [])

    def test_superseded_memory_sets_status_and_cleans_text(self):
        line = "[2026-08-09 14:34] (sample-app/claude-code) [SUPERSEDED] old architecture decision"
        got = mb.parse_listing(line)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["status"], "superseded")
        self.assertEqual(got[0]["memory"], "old architecture decision")


class TestMcpReplyParsing(unittest.TestCase):

    def test_sse_framing(self):
        raw = 'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":1}}\r\n\r\n'
        self.assertEqual(mb.MiniMcpClient._parse(raw)["result"], {"ok": 1})

    def test_plain_json_body(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": 2}})
        self.assertEqual(mb.MiniMcpClient._parse(raw)["result"], {"ok": 2})

    def test_unparseable_reply_raises(self):
        with self.assertRaises(mb.McpError):
            mb.MiniMcpClient._parse("<html>gateway error</html>")


class TestWriteRouting(unittest.TestCase):

    def test_local_backend_uses_the_local_path(self):
        seen = {}
        orig = mb._write_local
        mb._write_local = lambda t, s, a: seen.setdefault("args", (t, s, a)) or []
        try:
            mb.write("text", "scope", "agent", env={})
        finally:
            mb._write_local = orig
        self.assertEqual(seen["args"], ("text", "scope", "agent"))

    def test_remote_backend_uses_the_remote_path(self):
        seen = {}
        orig = mb._write_remote
        mb._write_remote = lambda t, s, a, e: seen.setdefault("args", (t, s, a)) or []
        try:
            mb.write("text", "scope", "agent", env={"MEM0_BACKEND": "remote"})
        finally:
            mb._write_remote = orig
        self.assertEqual(seen["args"], ("text", "scope", "agent"))

    def test_remote_failure_returns_none_so_the_cursor_holds(self):
        """None means "never consumed" — the caller must not advance past a
        window the brain never received."""
        self.assertIsNone(
            mb._write_remote("t", "s", "a",
                             env={"MEM0_BACKEND": "remote"}))   # no MEM0_BRAIN_URL

    def test_remote_without_a_url_does_not_raise_into_the_hook(self):
        self.assertIsNone(mb.write("t", "s", env={"MEM0_BACKEND": "remote"}))


if __name__ == "__main__":
    unittest.main()
