#!/usr/bin/env python3
"""Unit tests for SessionStart memory injection.

Run: .venv/bin/python -m pytest test_recall_inject.py -q
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import recall_inject as ri  # noqa: E402
import rules  # noqa: E402


def mem(text, scope="personal", created="2026-08-06T12:00:00", agent="claude-code"):
    return {"id": "x", "memory": text, "run_id": scope,
            "agent_id": agent, "created_at": created}


# Anchored, actionable lines — what the store is supposed to hold. Each carries a
# concrete referent (path, flag, identifier) so it survives the junk filter.
L_PROJ = "prisma migrate diff --from-migrations yol argumani ister (./prisma/migrations)"
L_PERS = "hook script'lerinde daima execFileSync + arg dizisi kullan, asla shell string"
L_OTHER = "eslint-config-next@15 ESLint 10 ile calismaz, config-next@16 gerekir"


class TestSelect(unittest.TestCase):

    def test_project_memories_rank_above_personal(self):
        """The project tier is the more specific answer; when the budget only
        fits a few lines, the repo's own traps must not be crowded out by
        cross-project ones."""
        items = [mem(L_PERS, "personal"), mem(L_PROJ, "sample-app")]
        out = ri.select(items, scope="sample-app", max_items=5, max_chars=4000)
        self.assertEqual([m["memory"] for m in out], [L_PROJ, L_PERS])

    def test_other_projects_never_leak(self):
        """Scope isolation is the whole point of the two-tier model: a session
        in `sample-app` must never be handed `sample-assistant`'s facts."""
        items = [mem(L_OTHER, "sample-assistant"), mem(L_PROJ, "sample-app")]
        out = ri.select(items, scope="sample-app", max_items=5, max_chars=4000)
        self.assertEqual([m["memory"] for m in out], [L_PROJ])

    def test_personal_scope_session_gets_only_personal(self):
        items = [mem(L_PERS, "personal"), mem(L_PROJ, "sample-app")]
        out = ri.select(items, scope="personal", max_items=5, max_chars=4000)
        self.assertEqual([m["memory"] for m in out], [L_PERS])

    def test_newest_first_within_a_tier(self):
        old = mem(L_PERS, "personal", "2026-08-01T09:00:00")
        new = mem(L_OTHER, "personal", "2026-08-06T09:00:00")
        out = ri.select([old, new], scope="personal", max_items=5, max_chars=4000)
        self.assertEqual([m["memory"] for m in out], [L_OTHER, L_PERS])

    def test_junk_is_excluded(self):
        """The store is known-polluted by infer=True autosave. Injecting it
        unfiltered would put conversational noise into every session's context,
        which is worse than injecting nothing."""
        junk = mem("Paralel calismayi baslatabilirsin ve hizli bitirmeyi hedefleyelim.")
        out = ri.select([junk, mem(L_PERS)], scope="personal", max_items=5, max_chars=4000)
        self.assertEqual([m["memory"] for m in out], [L_PERS])

    def test_respects_max_items(self):
        items = [mem(L_PERS + " %d" % i, "personal", "2026-08-0%dT09:00:00" % (i + 1))
                 for i in range(5)]
        out = ri.select(items, scope="personal", max_items=2, max_chars=4000)
        self.assertEqual(len(out), 2)

    def test_char_budget_drops_whole_entries(self):
        """A half-sentence is a misleading memory. When the budget runs out the
        entry is dropped entirely rather than truncated."""
        items = [mem(L_PERS, "personal", "2026-08-06T09:00:00"),
                 mem(L_OTHER, "personal", "2026-08-05T09:00:00")]
        out = ri.select(items, scope="personal", max_items=9,
                        max_chars=len(L_PERS) + 10)
        self.assertEqual([m["memory"] for m in out], [L_PERS])

    def test_empty_store_selects_nothing(self):
        self.assertEqual(ri.select([], scope="sample-app", max_items=5, max_chars=4000), [])

    def test_duplicate_text_is_collapsed(self):
        """The store holds near-identical rows (mem0's dedup misses typo
        variants). Repeating a line inside one injection wastes the budget."""
        items = [mem(L_PERS, "personal", "2026-08-06T09:00:00"),
                 mem(L_PERS, "personal", "2026-08-05T09:00:00")]
        out = ri.select(items, scope="personal", max_items=9, max_chars=4000)
        self.assertEqual(len(out), 1)

    def test_superseded_memories_are_excluded(self):
        """A memory marked as superseded must never be injected, even if it has an anchor."""
        active = mem(L_PERS, "personal", "2026-08-06T09:00:00")
        superseded = mem(L_PROJ, "sample-app", "2026-08-07T09:00:00")
        superseded["status"] = "superseded"
        out = ri.select([active, superseded], scope="sample-app", max_items=5, max_chars=4000)
        self.assertEqual([m["memory"] for m in out], [L_PERS])



class TestRender(unittest.TestCase):

    def test_nothing_selected_renders_empty_string(self):
        """A header with no content still costs context and trains the reader to
        ignore the block. No memories means no output at all."""
        self.assertEqual(ri.render([], "sample-app"), "")

    def test_renders_the_memory_text(self):
        out = ri.render([mem(L_PROJ, "sample-app")], "sample-app")
        self.assertIn(L_PROJ, out)

    def test_marks_the_content_as_background_not_instructions(self):
        """Injected memories are prior observations, not orders for this
        session. Without that framing a stale note reads as a directive."""
        out = ri.render([mem(L_PROJ, "sample-app")], "sample-app").lower()
        self.assertTrue("not instructions" in out or "background" in out)

    def test_shows_which_scope_each_line_came_from(self):
        out = ri.render([mem(L_PROJ, "sample-app"), mem(L_PERS, "personal")], "sample-app")
        self.assertIn("sample-app", out)
        self.assertIn("personal", out)


class TestCollect(unittest.TestCase):
    """The hook must never break or hang a session it cannot serve."""

    def test_store_unreachable_yields_no_output(self):
        def boom():
            raise OSError("connection refused")
        block, items = ri.collect("sample-app", fetch=boom)
        self.assertEqual(block, "")
        self.assertEqual(items, [])

    def test_malformed_rows_are_skipped_not_fatal(self):
        rows = [{"nonsense": True}, mem(L_PERS)]
        block, _ = ri.collect("personal", fetch=lambda: rows)
        self.assertIn(L_PERS, block)

    def test_collect_returns_what_it_rendered(self):
        """The caller records the selection so a resume can skip it; the items
        it records must be exactly the ones that reached the context."""
        block, items = ri.collect("personal", fetch=lambda: [mem(L_PERS)])
        self.assertEqual([i["memory"] for i in items], [L_PERS])
        self.assertIn(L_PERS, block)


class TestFingerprint(unittest.TestCase):

    def test_ignores_surrounding_whitespace(self):
        """The store holds re-saved rows whose padding drifted; they are the
        same lesson and must not count as unseen."""
        self.assertEqual(ri.fingerprint("  a lesson "), ri.fingerprint("a lesson"))

    def test_different_text_gets_a_different_id(self):
        self.assertNotEqual(ri.fingerprint(L_PERS), ri.fingerprint(L_PROJ))


class TestSessionState(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = ri.STATE_DIR
        ri.STATE_DIR = self._tmp.name

    def tearDown(self):
        ri.STATE_DIR = self._orig
        self._tmp.cleanup()

    def test_recorded_memories_come_back(self):
        ri.record_injected("sess-1", [mem(L_PERS), mem(L_PROJ)])
        got = ri.read_injected("sess-1")
        self.assertIn(ri.fingerprint(L_PERS), got)
        self.assertIn(ri.fingerprint(L_PROJ), got)

    def test_records_accumulate_across_calls(self):
        ri.record_injected("sess-1", [mem(L_PERS)])
        ri.record_injected("sess-1", [mem(L_PROJ)])
        self.assertEqual(len(ri.read_injected("sess-1")), 2)

    def test_unknown_session_has_no_history(self):
        self.assertEqual(ri.read_injected("never-seen"), set())

    def test_missing_session_id_is_not_fatal(self):
        """Claude Code always sends session_id, but a hook that dies when a
        field is absent is a hook that breaks the session."""
        ri.record_injected("", [mem(L_PERS)])
        self.assertEqual(ri.read_injected(""), set())

    def test_unwritable_state_dir_is_swallowed(self):
        ri.STATE_DIR = os.path.join(self._tmp.name, "nope", "\0bad")
        ri.record_injected("sess-1", [mem(L_PERS)])   # must not raise


class TestSourceHandling(unittest.TestCase):
    """SessionStart fires for several reasons and they differ in whether the
    previous injection is still in the model's context."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = ri.STATE_DIR
        ri.STATE_DIR = self._tmp.name
        ri.record_injected("s", [mem(L_PERS)])

    def tearDown(self):
        ri.STATE_DIR = self._orig
        self._tmp.cleanup()

    def test_resume_stays_silent_because_the_block_is_replayed_with_the_transcript(self):
        self.assertFalse(ri.should_inject("resume", "s"))

    def test_compact_re_injects_because_the_old_block_was_summarised_away(self):
        self.assertTrue(ri.should_inject("compact", "s"))

    def test_startup_injects(self):
        self.assertTrue(ri.should_inject("startup", "s"))

    def test_resume_of_a_session_that_was_never_served_still_injects(self):
        """A session that started while Qdrant was down got nothing; resuming it
        is the next chance to serve it, not a reason to stay quiet."""
        self.assertTrue(ri.should_inject("resume", "never-served"))

    def test_unknown_source_defaults_to_injecting(self):
        """An unrecognised source must fail toward showing the memories, not
        toward silently showing nothing."""
        self.assertTrue(ri.should_inject("brand-new-source", "s"))


class TestBackendIsNotThisModulesBusiness(unittest.TestCase):
    """Where the rows come from moved to mem0_backend; this hook only knows it
    asks for rows. Endpoint/transport tests live in test_mem0_backend.py."""

    def test_fetch_delegates_to_the_backend(self):
        import mem0_backend
        called = {}
        orig = mem0_backend.rows
        mem0_backend.rows = lambda **kw: called.setdefault("kw", kw) or []
        try:
            ri.fetch()
        finally:
            mem0_backend.rows = orig
        self.assertIn("timeout", called["kw"])

    def test_no_qdrant_address_remains_in_this_module(self):
        """A worker must not be able to learn where the vector store is by
        reading its recall hook."""
        with open(os.path.join(os.path.dirname(os.path.abspath(ri.__file__)),
                               "recall_inject.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("6333", src)
        self.assertNotIn("points/scroll", src)


class TestScopedRulesInjection(unittest.TestCase):
    """The five acceptance criteria of the scoped-rules design, exercised through
    main() — the seam that actually runs on SessionStart.

    Spec: docs/superpowers/specs/2026-08-12-scoped-rules-design.md §8
    """

    AWAIT_SERVICES = "Promise donen servis cagrilarinda await kullan."
    AWAIT_ANALYTICS = "Analytics eventlerinde await kullanma, fire-and-forget kullan."

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._rules_dir = os.path.join(self._tmp.name, "rules")
        os.makedirs(self._rules_dir)

        self._orig_rules_dir = rules.RULES_DIR
        self._orig_state = ri.STATE_DIR
        self._orig_scope_for = ri.scope_for
        self._orig_collect = ri.collect

        rules.RULES_DIR = self._rules_dir
        ri.STATE_DIR = self._tmp.name
        # No memories unless a test says otherwise: these tests are about the
        # rules block, and a live fetch would reach for Qdrant.
        ri.collect = lambda scope: ("", [])

    def tearDown(self):
        rules.RULES_DIR = self._orig_rules_dir
        ri.STATE_DIR = self._orig_state
        ri.scope_for = self._orig_scope_for
        ri.collect = self._orig_collect
        self._tmp.cleanup()

    def write_rules(self, scope, entries):
        with open(os.path.join(self._rules_dir, "%s.json" % scope),
                  "w", encoding="utf-8") as f:
            json.dump({"rules": entries}, f)

    def write_raw(self, scope, text):
        with open(os.path.join(self._rules_dir, "%s.json" % scope),
                  "w", encoding="utf-8") as f:
            f.write(text)

    def run_main(self, project, session_id="s1"):
        """main() as SessionStart invokes it: JSON on stdin, block on stdout."""
        ri.scope_for = lambda cwd: project
        stdin, stdout = io.StringIO(json.dumps(
            {"cwd": "/tmp/x", "session_id": session_id, "source": "startup"})), io.StringIO()
        real_in, real_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = stdin, stdout
        try:
            code = ri.main()
        finally:
            sys.stdin, sys.stdout = real_in, real_out
        return code, stdout.getvalue()

    def seed_project_a(self):
        self.write_rules("projectA", [
            {"id": "await-services", "instruction": self.AWAIT_SERVICES,
             "severity": "high", "paths": ["src/services/**", "src/api/**"],
             "exclude_paths": ["src/analytics/**"]},
        ])

    def seed_project_b(self):
        self.write_rules("projectB", [
            {"id": "no-await-analytics", "instruction": self.AWAIT_ANALYTICS,
             "paths": ["src/analytics/**"]},
        ])

    # --- criterion 1 --------------------------------------------------------

    def test_project_rule_and_its_path_exception_both_reach_the_session(self):
        """Acceptance criterion 1. The exception is the half that a flat memory
        loses: without it the rule reads as universal and is wrong in the one
        directory it was written to spare."""
        self.seed_project_a()
        code, out = self.run_main("projectA")
        self.assertEqual(code, 0)
        self.assertIn(self.AWAIT_SERVICES, out)
        self.assertIn("src/services/**", out)
        self.assertIn("except: src/analytics/**", out)

    # --- criterion 2 --------------------------------------------------------

    def test_a_second_project_gets_its_opposite_rule_and_none_of_the_first(self):
        """Acceptance criterion 2. Two projects hold contradictory conventions on
        the same topic; each session may only see its own."""
        self.seed_project_a()
        self.seed_project_b()
        _, out = self.run_main("projectB")
        self.assertIn(self.AWAIT_ANALYTICS, out)
        self.assertNotIn(self.AWAIT_SERVICES, out)
        self.assertNotIn("src/services/**", out)

    # --- criterion 3 --------------------------------------------------------

    def test_rules_are_printed_even_when_the_memory_store_is_unreachable(self):
        """Acceptance criterion 3, and the reason rules live in files: recall
        fails silently when Qdrant is down, so rules kept in the store would
        vanish with it and nobody would see that happen."""
        self.seed_project_a()

        def dead(scope):
            raise OSError("connection refused")
        ri.collect = dead

        code, out = self.run_main("projectA")
        self.assertEqual(code, 0)
        self.assertIn(self.AWAIT_SERVICES, out)

    def test_memories_still_print_when_there_are_no_rules(self):
        """The blocks are independent in both directions."""
        ri.collect = lambda scope: ("Recalled from long-term memory", [{"memory": "m"}])
        _, out = self.run_main("projectA")
        self.assertIn("Recalled from long-term memory", out)

    def test_rules_are_printed_before_the_memory_block(self):
        """Rules are binding; recalled memories are explicitly framed as
        unverified background. The binding thing goes first."""
        self.seed_project_a()
        ri.collect = lambda scope: ("Recalled from long-term memory", [{"memory": "m"}])
        _, out = self.run_main("projectA")
        self.assertLess(out.index("Scoped rules"),
                        out.index("Recalled from long-term memory"))

    # --- criterion 4 --------------------------------------------------------

    def test_corrupt_rules_file_prints_nothing_and_exits_zero(self):
        """Acceptance criterion 4. A session must start whether or not this
        feature works at all."""
        self.write_raw("projectA", "{ not json at all")
        code, out = self.run_main("projectA")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_missing_rules_file_prints_nothing_and_exits_zero(self):
        code, out = self.run_main("projectWithoutRules")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    # --- criterion 5 --------------------------------------------------------

    def test_budget_overflow_keeps_high_severity_and_repeats_identically(self):
        """Acceptance criterion 5. When more rules exist than fit, the block must
        be the same one every time — a block that reshuffles per session is a
        convention nobody can rely on."""
        self.write_rules("projectA",
                         [{"id": "n%02d" % i, "instruction": "normal rule %d" % i}
                          for i in range(15)]
                         + [{"id": "h%02d" % i, "instruction": "high rule %d" % i,
                             "severity": "high"} for i in range(3)])
        _, first = self.run_main("projectA", session_id="s1")
        _, second = self.run_main("projectA", session_id="s2")
        self.assertEqual(first, second)
        for i in range(3):
            self.assertIn("high rule %d" % i, first)
        self.assertEqual(first.count("\n- "), rules.MAX_RULES)

    # --- tier behaviour at the injection seam -------------------------------

    def test_shared_tier_rules_reach_a_project_session(self):
        self.write_rules("personal", [{"id": "commit", "instruction": "Conventional commits kullan."}])
        self.seed_project_a()
        _, out = self.run_main("projectA")
        self.assertIn("Conventional commits kullan.", out)
        self.assertIn("(personal)", out)
        self.assertIn("(projectA)", out)

    def test_project_rule_shadows_the_personal_rule_with_the_same_id(self):
        self.write_rules("personal", [{"id": "await-services",
                                       "instruction": self.AWAIT_SERVICES}])
        self.write_rules("projectA", [{"id": "await-services",
                                       "instruction": self.AWAIT_ANALYTICS}])
        _, out = self.run_main("projectA")
        self.assertIn(self.AWAIT_ANALYTICS, out)
        self.assertNotIn(self.AWAIT_SERVICES, out)

    def test_disabled_rules_never_reach_a_session(self):
        self.write_rules("projectA", [{"id": "off", "instruction": self.AWAIT_SERVICES,
                                       "status": "disabled"}])
        _, out = self.run_main("projectA")
        self.assertEqual(out, "")

    def test_a_resumed_session_is_not_served_the_block_twice(self):
        """The rules block follows the same skip decision as the memory block:
        on resume the transcript is replayed, so both are already in context."""
        self.seed_project_a()
        ri.record_injected("s-resumed", [{"memory": "anything"}])
        ri.scope_for = lambda cwd: "projectA"
        stdin, stdout = io.StringIO(json.dumps(
            {"cwd": "/tmp/x", "session_id": "s-resumed", "source": "resume"})), io.StringIO()
        real_in, real_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = stdin, stdout
        try:
            ri.main()
        finally:
            sys.stdin, sys.stdout = real_in, real_out
        self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
