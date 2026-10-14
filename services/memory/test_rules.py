#!/usr/bin/env python3
"""Unit tests for scoped rules (docs/superpowers/specs/2026-08-12-scoped-rules-design.md).

Run: .venv/bin/python -m pytest test_rules.py -q
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

import rules  # noqa: E402


AWAIT_SERVICES = "Promise donen servis cagrilarinda await kullan."
AWAIT_ANALYTICS = "Analytics eventlerinde await kullanma, fire-and-forget wrapper kullan."
COMMIT_STYLE = "Commit mesajlarini conventional commits formatinda yaz."


def rule(rid, instruction, **kw):
    r = {"id": rid, "instruction": instruction}
    r.update(kw)
    return r


class RulesDirCase(unittest.TestCase):
    """Every test gets its own rules directory; none of them may touch ~/.mem0."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = rules.RULES_DIR
        rules.RULES_DIR = self._tmp.name

    def tearDown(self):
        rules.RULES_DIR = self._orig
        self._tmp.cleanup()

    def write(self, scope, entries):
        path = os.path.join(rules.RULES_DIR, "%s.json" % scope)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"rules": entries}, f)
        return path

    def write_raw(self, scope, text):
        path = os.path.join(rules.RULES_DIR, "%s.json" % scope)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class TestFailOpen(RulesDirCase):
    """A rules file is read on SessionStart. Nothing about it may raise, because
    the caller's contract is that a session starts whether or not this works."""

    def test_missing_file_is_no_rules_not_an_error(self):
        self.assertEqual(rules.load_rules("sample-app"), [])

    def test_corrupt_json_yields_no_rules(self):
        """Acceptance criterion 4. A half-parsed rules file must not become a
        half-applied convention."""
        self.write_raw("sample-app", "{ this is not json")
        self.assertEqual(rules.load_rules("sample-app"), [])

    def test_top_level_list_instead_of_object_yields_no_rules(self):
        self.write_raw("sample-app", '[{"id": "a", "instruction": "b"}]')
        self.assertEqual(rules.load_rules("sample-app"), [])

    def test_rules_key_not_a_list_yields_no_rules(self):
        self.write_raw("sample-app", '{"rules": "oops"}')
        self.assertEqual(rules.load_rules("sample-app"), [])

    def test_malformed_entry_is_skipped_and_siblings_survive(self):
        """One bad hand-edit must not silently drop every other rule in the
        file — that would remove conventions without anyone noticing."""
        self.write("sample-app", ["not-a-dict",
                             {"instruction": "no id here"},
                             {"id": "no-instruction"},
                             rule("good", AWAIT_SERVICES)])
        got = rules.load_rules("sample-app")
        self.assertEqual([r["id"] for r in got], ["good"])

    def test_unreadable_directory_is_not_fatal(self):
        rules.RULES_DIR = os.path.join(self._tmp.name, "nope", "\0bad")
        self.assertEqual(rules.load_rules("sample-app"), [])


class TestDefaults(RulesDirCase):

    def test_absent_optional_fields_get_defaults(self):
        self.write("sample-app", [rule("r", AWAIT_SERVICES)])
        got = rules.load_rules("sample-app")[0]
        self.assertEqual(got["paths"], [])
        self.assertEqual(got["exclude_paths"], [])
        self.assertEqual(got["severity"], "normal")
        self.assertEqual(got["status"], "active")

    def test_unknown_severity_falls_back_to_normal(self):
        """A typo in a hand-edited file must not invent a new priority class."""
        self.write("sample-app", [rule("r", AWAIT_SERVICES, severity="URGENT!!")])
        self.assertEqual(rules.load_rules("sample-app")[0]["severity"], "normal")

    def test_unknown_status_falls_back_to_active(self):
        self.write("sample-app", [rule("r", AWAIT_SERVICES, status="maybe")])
        self.assertEqual(rules.load_rules("sample-app")[0]["status"], "active")

    def test_non_list_paths_are_ignored_rather_than_crashing(self):
        self.write("sample-app", [rule("r", AWAIT_SERVICES, paths="src/**")])
        self.assertEqual(rules.load_rules("sample-app")[0]["paths"], [])


class TestStatus(RulesDirCase):

    def test_disabled_rules_are_not_injected(self):
        self.write("sample-app", [rule("off", AWAIT_SERVICES, status="disabled"),
                             rule("on", COMMIT_STYLE)])
        self.assertEqual([r["id"] for r in rules.load_rules("sample-app")], ["on"])

    def test_disabled_rules_are_still_listed_for_the_author(self):
        """Deleting a rule loses the evidence for why it existed; disabling keeps
        it, so the authoring view has to be able to see it."""
        self.write("sample-app", [rule("off", AWAIT_SERVICES, status="disabled")])
        self.assertEqual(rules.list_rules("sample-app", include_disabled=False), [])
        self.assertEqual(
            [r["id"] for r in rules.list_rules("sample-app", include_disabled=True)], ["off"])


class TestTierMerge(RulesDirCase):

    def test_both_tiers_load_for_a_project_session(self):
        self.write("personal", [rule("commit", COMMIT_STYLE)])
        self.write("sample-app", [rule("await", AWAIT_SERVICES)])
        self.assertEqual(sorted(r["id"] for r in rules.load_rules("sample-app")),
                         ["await", "commit"])

    def test_other_projects_never_leak(self):
        """Acceptance criterion 2's isolation half: scope isolation is the point
        of the two-tier model."""
        self.write("sample-app", [rule("await", AWAIT_SERVICES)])
        self.write("sample-panel", [rule("other", AWAIT_ANALYTICS)])
        self.assertEqual([r["id"] for r in rules.load_rules("sample-app")], ["await"])

    def test_project_version_overrides_the_personal_one_with_the_same_id(self):
        """Rendering both would put two lines with the same id, and possibly
        contradictory instructions, in front of the model — the exact failure
        this design exists to prevent."""
        self.write("personal", [rule("await", AWAIT_SERVICES)])
        self.write("sample-app", [rule("await", AWAIT_ANALYTICS)])
        got = rules.load_rules("sample-app")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["instruction"], AWAIT_ANALYTICS)
        self.assertEqual(got[0]["tier"], "sample-app")

    def test_personal_session_reads_only_the_shared_tier(self):
        self.write("personal", [rule("commit", COMMIT_STYLE)])
        self.write("sample-app", [rule("await", AWAIT_SERVICES)])
        self.assertEqual([r["id"] for r in rules.load_rules("personal")], ["commit"])

    def test_each_rule_knows_which_tier_it_came_from(self):
        self.write("personal", [rule("commit", COMMIT_STYLE)])
        self.write("sample-app", [rule("await", AWAIT_SERVICES)])
        tiers = {r["id"]: r["tier"] for r in rules.load_rules("sample-app")}
        self.assertEqual(tiers, {"commit": "personal", "await": "sample-app"})


class TestOrderAndBudget(RulesDirCase):

    def test_high_severity_sorts_before_normal(self):
        self.write("sample-app", [rule("b-normal", COMMIT_STYLE),
                             rule("a-high", AWAIT_SERVICES, severity="high")])
        self.assertEqual([r["id"] for r in rules.load_rules("sample-app")],
                         ["a-high", "b-normal"])

    def test_within_a_severity_personal_sorts_before_project(self):
        self.write("personal", [rule("zzz", COMMIT_STYLE)])
        self.write("sample-app", [rule("aaa", AWAIT_SERVICES)])
        self.assertEqual([r["id"] for r in rules.load_rules("sample-app")], ["zzz", "aaa"])

    def test_ties_break_on_id_so_output_is_stable(self):
        self.write("sample-app", [rule("c", COMMIT_STYLE), rule("a", COMMIT_STYLE),
                             rule("b", COMMIT_STYLE)])
        self.assertEqual([r["id"] for r in rules.load_rules("sample-app")], ["a", "b", "c"])

    def test_respects_the_rule_count_budget(self):
        self.write("sample-app", [rule("r%02d" % i, COMMIT_STYLE) for i in range(20)])
        self.assertEqual(len(rules.load_rules("sample-app")), rules.MAX_RULES)

    def test_char_budget_drops_whole_rules(self):
        """Half a rule is a misleading rule — the same reasoning the memory
        selector uses when its budget runs out."""
        self.write("sample-app", [rule("a", "x" * 200), rule("b", "y" * 200)])
        got = rules.load_rules("sample-app", max_chars=250)
        self.assertEqual([r["id"] for r in got], ["a"])

    def test_budget_overflow_keeps_high_severity_and_is_deterministic(self):
        """Acceptance criterion 5. When the budget bites, the rules that matter
        most must survive, and two runs must produce the same block."""
        entries = [rule("n%02d" % i, "normal rule %d" % i) for i in range(15)]
        entries += [rule("h%02d" % i, "high rule %d" % i, severity="high")
                    for i in range(3)]
        self.write("sample-app", entries)
        first = rules.load_rules("sample-app")
        second = rules.load_rules("sample-app")
        self.assertEqual([r["id"] for r in first], [r["id"] for r in second])
        self.assertLessEqual(len(first), rules.MAX_RULES)
        for hid in ("h00", "h01", "h02"):
            self.assertIn(hid, [r["id"] for r in first])

    def test_file_order_does_not_change_the_result(self):
        entries = [rule("a", COMMIT_STYLE, severity="high"),
                   rule("b", AWAIT_SERVICES), rule("c", AWAIT_ANALYTICS)]
        self.write("sample-app", entries)
        forward = [r["id"] for r in rules.load_rules("sample-app")]
        self.write("sample-app", list(reversed(entries)))
        self.assertEqual(forward, [r["id"] for r in rules.load_rules("sample-app")])


class TestScopeText(unittest.TestCase):
    """Every rule is rendered with its scope — that IS the conflict-resolution
    mechanism, since the file being edited is unknown at SessionStart."""

    def test_no_paths_reads_as_project_wide(self):
        self.assertEqual(rules.scope_text({"paths": [], "exclude_paths": []}),
                         "all paths")

    def test_paths_are_listed(self):
        got = rules.scope_text({"paths": ["src/services/**", "src/api/**"],
                                "exclude_paths": []})
        self.assertEqual(got, "paths: src/services/**, src/api/**")

    def test_exclusions_are_shown_next_to_the_paths(self):
        got = rules.scope_text({"paths": ["src/**"],
                                "exclude_paths": ["src/analytics/**"]})
        self.assertEqual(got, "paths: src/** | except: src/analytics/**")

    def test_exclusion_without_paths_still_shows(self):
        got = rules.scope_text({"paths": [], "exclude_paths": ["src/analytics/**"]})
        self.assertEqual(got, "all paths | except: src/analytics/**")


class TestRender(RulesDirCase):

    def test_no_rules_renders_nothing_at_all(self):
        """A header with no content under it costs context and teaches the
        reader to skip the block."""
        self.assertEqual(rules.render([], "sample-app"), "")

    def test_header_frames_rules_as_binding(self):
        """Recalled memories are framed as unverified background; rules are the
        opposite and must not read the same way."""
        self.write("sample-app", [rule("a", AWAIT_SERVICES)])
        out = rules.render(rules.load_rules("sample-app"), "sample-app").lower()
        self.assertIn("binding", out)

    def test_line_carries_tier_severity_scope_and_instruction(self):
        self.write("sample-app", [rule("a", AWAIT_SERVICES, severity="high",
                                  paths=["src/services/**"],
                                  exclude_paths=["src/analytics/**"])])
        out = rules.render(rules.load_rules("sample-app"), "sample-app")
        self.assertIn("(sample-app)", out)
        self.assertIn("[high]", out)
        self.assertIn("paths: src/services/**", out)
        self.assertIn("except: src/analytics/**", out)
        self.assertIn(AWAIT_SERVICES, out)


class TestOverlapLint(unittest.TestCase):
    """A warning, never a block: the check is a heuristic, and a heuristic that
    refuses writes is worse than the ambiguity it is guarding against."""

    def test_same_glob_in_two_rules_warns(self):
        existing = [{"id": "a", "paths": ["src/analytics/**"], "status": "active"}]
        new = {"id": "b", "paths": ["src/analytics/**"], "status": "active"}
        warns = rules.overlap_warnings(new, existing)
        self.assertTrue(any("src/analytics/**" in w for w in warns))

    def test_two_project_wide_rules_warn(self):
        existing = [{"id": "a", "paths": [], "status": "active"}]
        new = {"id": "b", "paths": [], "status": "active"}
        self.assertTrue(rules.overlap_warnings(new, existing))

    def test_disjoint_globs_do_not_warn(self):
        existing = [{"id": "a", "paths": ["src/services/**"], "status": "active"}]
        new = {"id": "b", "paths": ["src/analytics/**"], "status": "active"}
        self.assertEqual(rules.overlap_warnings(new, existing), [])

    def test_disabled_rules_are_not_compared_against(self):
        existing = [{"id": "a", "paths": [], "status": "disabled"}]
        new = {"id": "b", "paths": [], "status": "active"}
        self.assertEqual(rules.overlap_warnings(new, existing), [])

    def test_a_rule_does_not_overlap_itself(self):
        """Re-adding the same id is an edit, not a collision."""
        existing = [{"id": "a", "paths": ["src/**"], "status": "active"}]
        new = {"id": "a", "paths": ["src/**"], "status": "active"}
        self.assertEqual(rules.overlap_warnings(new, existing), [])


class TestWriteSide(RulesDirCase):

    def test_add_then_load_round_trips(self):
        rules.add_rule("sample-app", rule("await", AWAIT_SERVICES, severity="high",
                                     paths=["src/services/**"]))
        got = rules.load_rules("sample-app")
        self.assertEqual(got[0]["instruction"], AWAIT_SERVICES)
        self.assertEqual(got[0]["paths"], ["src/services/**"])

    def test_add_creates_the_directory(self):
        rules.RULES_DIR = os.path.join(self._tmp.name, "fresh")
        rules.add_rule("sample-app", rule("a", COMMIT_STYLE))
        self.assertEqual(len(rules.load_rules("sample-app")), 1)

    def test_add_stamps_timestamps(self):
        rules.add_rule("sample-app", rule("a", COMMIT_STYLE), now="2026-08-12")
        got = rules.list_rules("sample-app")[0]
        self.assertEqual(got["created_at"], "2026-08-12")
        self.assertEqual(got["updated_at"], "2026-08-12")

    def test_re_adding_an_id_replaces_it_and_keeps_created_at(self):
        rules.add_rule("sample-app", rule("a", COMMIT_STYLE), now="2026-08-01")
        rules.add_rule("sample-app", rule("a", AWAIT_SERVICES), now="2026-08-12")
        got = rules.list_rules("sample-app")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["instruction"], AWAIT_SERVICES)
        self.assertEqual(got[0]["created_at"], "2026-08-01")
        self.assertEqual(got[0]["updated_at"], "2026-08-12")

    def test_add_returns_overlap_warnings_but_still_writes(self):
        rules.add_rule("sample-app", rule("a", COMMIT_STYLE))
        warns = rules.add_rule("sample-app", rule("b", AWAIT_SERVICES))
        self.assertTrue(warns)
        self.assertEqual(len(rules.list_rules("sample-app")), 2)

    def test_add_preserves_disabled_siblings(self):
        rules.add_rule("sample-app", rule("off", COMMIT_STYLE, status="disabled"))
        rules.add_rule("sample-app", rule("on", AWAIT_SERVICES))
        self.assertEqual(len(rules.list_rules("sample-app", include_disabled=True)), 2)

    def test_remove_deletes_by_id(self):
        rules.add_rule("sample-app", rule("a", COMMIT_STYLE))
        self.assertTrue(rules.remove_rule("sample-app", "a"))
        self.assertEqual(rules.list_rules("sample-app"), [])

    def test_remove_reports_a_miss_rather_than_pretending(self):
        self.assertFalse(rules.remove_rule("sample-app", "never-existed"))

    def test_written_file_is_readable_json_with_a_rules_key(self):
        """The file is meant to be hand-edited and diffed; its shape is part of
        the contract, not an implementation detail."""
        rules.add_rule("sample-app", rule("a", COMMIT_STYLE))
        with open(os.path.join(rules.RULES_DIR, "sample-app.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data.get("rules"), list)


class TestCli(RulesDirCase):
    """`mem.py rules` end to end. The CLI is a thin parser over rules.py, so
    these tests are about wiring: argument shapes, exit codes, and which stream
    each kind of output goes to."""

    def run_cli(self, *argv):
        import mem

        out, err = io.StringIO(), io.StringIO()
        real_argv, real_out, real_err = sys.argv, sys.stdout, sys.stderr
        sys.argv = ["mem.py", *argv]
        sys.stdout, sys.stderr = out, err
        try:
            code = mem.main()
        finally:
            sys.argv, sys.stdout, sys.stderr = real_argv, real_out, real_err
        return code, out.getvalue(), err.getvalue()

    def test_add_writes_a_rule_that_load_rules_can_read(self):
        code, out, _ = self.run_cli("rules", "add", "await-services", AWAIT_SERVICES,
                                    "--scope", "sample-app", "--paths", "src/services/**",
                                    "--severity", "high")
        self.assertEqual(code, 0)
        got = rules.load_rules("sample-app")
        self.assertEqual(got[0]["id"], "await-services")
        self.assertEqual(got[0]["severity"], "high")
        self.assertEqual(got[0]["paths"], ["src/services/**"])

    def test_comma_separated_globs_become_a_list(self):
        self.run_cli("rules", "add", "r", AWAIT_SERVICES, "--scope", "sample-app",
                     "--paths", "src/services/**, src/api/**",
                     "--exclude", "src/analytics/**")
        got = rules.load_rules("sample-app")[0]
        self.assertEqual(got["paths"], ["src/services/**", "src/api/**"])
        self.assertEqual(got["exclude_paths"], ["src/analytics/**"])

    def test_scope_defaults_to_the_shared_tier(self):
        self.run_cli("rules", "add", "commit", COMMIT_STYLE)
        self.assertEqual([r["id"] for r in rules.list_rules("personal")], ["commit"])

    def test_overlap_warns_on_stderr_and_still_exits_zero(self):
        """The human decides. A lint that blocked the write would be worse than
        the ambiguity it is guarding against."""
        self.run_cli("rules", "add", "a", COMMIT_STYLE, "--scope", "sample-app")
        code, _, err = self.run_cli("rules", "add", "b", AWAIT_SERVICES, "--scope", "sample-app")
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertEqual(len(rules.list_rules("sample-app")), 2)

    def test_list_shows_the_scope_of_each_rule(self):
        self.run_cli("rules", "add", "r", AWAIT_SERVICES, "--scope", "sample-app",
                     "--paths", "src/services/**")
        _, out, _ = self.run_cli("rules", "list", "--scope", "sample-app")
        self.assertIn("paths: src/services/**", out)
        self.assertIn(AWAIT_SERVICES, out)

    def test_list_hides_disabled_rules_unless_asked(self):
        rules.add_rule("sample-app", rule("off", COMMIT_STYLE, status="disabled"))
        _, plain, _ = self.run_cli("rules", "list", "--scope", "sample-app")
        self.assertIn("(empty)", plain)
        _, everything, _ = self.run_cli("rules", "list", "--scope", "sample-app", "--all")
        self.assertIn("disabled", everything)

    def test_rm_deletes_and_reports(self):
        self.run_cli("rules", "add", "r", COMMIT_STYLE, "--scope", "sample-app")
        code, out, _ = self.run_cli("rules", "rm", "r", "--scope", "sample-app")
        self.assertEqual(code, 0)
        self.assertIn("deleted", out)
        self.assertEqual(rules.list_rules("sample-app"), [])

    def test_rm_of_an_unknown_id_fails_loudly(self):
        """A silent no-op would let a typo read as a successful deletion."""
        code, _, err = self.run_cli("rules", "rm", "ghost", "--scope", "sample-app")
        self.assertEqual(code, 1)
        self.assertIn("no rule", err)

    def test_rules_commands_do_not_need_the_mem0_stack(self):
        """Rules are files. Requiring a running Ollama and Qdrant to write one
        would make the command unusable exactly when the stack is broken and a
        rule about it is what you want to record."""
        import mem
        self.assertNotIn("get_memory", dir(mem))


class TestPathMatching(unittest.TestCase):

    def test_matches_path_glob(self):
        self.assertTrue(rules.matches_path("src/services/user.ts", "src/services/**"))
        self.assertTrue(rules.matches_path("src/services/deep/user.ts", "src/services/**"))
        self.assertFalse(rules.matches_path("src/analytics/event.ts", "src/services/**"))
        self.assertTrue(rules.matches_path("src/utils/crypto.ts", "*.ts"))
        self.assertTrue(rules.matches_path("src/utils/crypto.ts", "crypto.ts"))

    def test_rule_applies_to_file_includes_and_excludes(self):
        rule = {
            "id": "await-calls",
            "instruction": "Always await service calls",
            "paths": ["src/services/**"],
            "exclude_paths": ["src/services/legacy/**"],
        }
        self.assertTrue(rules.rule_applies_to_file(rule, "src/services/auth.ts"))
        self.assertFalse(rules.rule_applies_to_file(rule, "src/services/legacy/old_auth.ts"))
        self.assertFalse(rules.rule_applies_to_file(rule, "src/controllers/main.ts"))

    def test_rule_without_paths_applies_to_all_files(self):
        rule = {
            "id": "commit-msg",
            "instruction": "Follow conventional commits",
            "paths": [],
            "exclude_paths": [],
        }
        self.assertTrue(rules.rule_applies_to_file(rule, "src/anywhere/file.py"))


if __name__ == "__main__":
    unittest.main()
