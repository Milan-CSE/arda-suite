#!/usr/bin/env python3
"""Unit tests for the Architectures panel. Stdlib unittest only — pytest is not installed.

Run: .venv/bin/python -m unittest dashboard.test_architectures -v
"""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import architectures as arch  # noqa: E402
import arch_templates as tpl  # noqa: E402
import migrate_architectures as migrate  # noqa: E402
import server  # noqa: E402
import mem0_project  # noqa: E402


class ArchFixture(unittest.TestCase):
    """Points ARCH_DIR at a temp dir and gives every test a valid repo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.arch_dir = base / "architectures"
        self.repo = base / "Projeler" / "demo"
        self.repo.mkdir(parents=True)
        self._orig_dir = arch.ARCH_DIR
        arch.ARCH_DIR = self.arch_dir

    def tearDown(self):
        arch.ARCH_DIR = self._orig_dir
        self._tmp.cleanup()

    def resolver(self, value):
        """Stand-in for server._resolve_repo: accepts only self.repo."""
        path = Path(value).expanduser().resolve()
        if path != self.repo.resolve():
            raise PermissionError(f"outside allowed roots: {path}")
        return path

    def make(self, title="Auth refresh flow", summary=""):
        return arch.create_architecture(
            title, str(self.repo), summary, resolve_repo=self.resolver
        )


# --------------------------------------------------------------------------
# 1. The phase catalog is the spec — pin it.
# --------------------------------------------------------------------------
class TestPhaseCatalog(unittest.TestCase):

    def test_has_exactly_sixteen_phases(self):
        self.assertEqual(len(arch.PHASES), 16)

    def test_phases_are_in_protocol_order(self):
        self.assertEqual(arch.PHASES, (
            "discover", "spec", "context", "design",
            "contracts", "decompose", "prioritize", "build",
            "verify", "review", "approve", "commit",
            "ci", "release", "observe", "learn",
        ))

    def test_phase_keys_are_unique(self):
        self.assertEqual(len(set(arch.PHASES)), len(arch.PHASES))


# --------------------------------------------------------------------------
# 2-3. Slugs — Turkish-safe, collision-safe.
# --------------------------------------------------------------------------
class TestSlugify(unittest.TestCase):

    def test_plain_title(self):
        self.assertEqual(arch.slugify("Auth refresh flow"), "auth-refresh-flow")

    def test_turkish_dotted_capital_i_folds_to_ascii(self):
        # str.lower() alone yields "i" + U+0307 and drops ş/ı entirely,
        # producing "izleme-ak". The explicit translation must beat it.
        self.assertEqual(arch.slugify("İzleme Akışı"), "izleme-akisi")

    def test_turkish_dotless_i_and_friends(self):
        self.assertEqual(arch.slugify("Ilık Çöp Ağı Şubesi"), "ilik-cop-agi-subesi")

    def test_collapses_punctuation_and_trims(self):
        self.assertEqual(arch.slugify("  Hello --- World!!  "), "hello-world")

    def test_truncates_to_64_chars(self):
        self.assertLessEqual(len(arch.slugify("x" * 200)), 64)

    def test_title_with_no_usable_characters_is_rejected(self):
        with self.assertRaises(ValueError):
            arch.slugify("!!! ???")


class TestSlugCollision(ArchFixture):

    def test_second_same_title_gets_suffix(self):
        first = self.make("Auth refresh flow")
        second = self.make("Auth refresh flow")
        self.assertEqual(first["id"], "auth-refresh-flow")
        self.assertEqual(second["id"], "auth-refresh-flow-2")

    def test_third_same_title_keeps_counting(self):
        self.make("Auth refresh flow")
        self.make("Auth refresh flow")
        self.assertEqual(self.make("Auth refresh flow")["id"], "auth-refresh-flow-3")


# --------------------------------------------------------------------------
# 4. The id guard is the only path resolution point.
# --------------------------------------------------------------------------
class TestIdGuard(ArchFixture):

    def test_accepts_a_simple_id(self):
        path = arch._arch_path("auth-refresh-flow")
        self.assertEqual(path.parent, self.arch_dir.resolve())
        self.assertEqual(path.name, "auth-refresh-flow.json")

    def test_rejects_parent_traversal(self):
        for bad in ("../escape", "..", "a/../../b", "foo/bar"):
            with self.subTest(bad=bad), self.assertRaises((ValueError, PermissionError)):
                arch._arch_path(bad)

    def test_rejects_absolute_path(self):
        with self.assertRaises((ValueError, PermissionError)):
            arch._arch_path("/etc/passwd")

    def test_rejects_hidden_and_empty(self):
        for bad in (".hidden", "", None, "   "):
            with self.subTest(bad=bad), self.assertRaises((ValueError, PermissionError)):
                arch._arch_path(bad)

    def test_rejects_overlong_id(self):
        with self.assertRaises((ValueError, PermissionError)):
            arch._arch_path("a" * 65)

    def test_rejects_uppercase_and_spaces(self):
        for bad in ("Auth-Flow", "auth flow", "auth_flow!"):
            with self.subTest(bad=bad), self.assertRaises((ValueError, PermissionError)):
                arch._arch_path(bad)


# --------------------------------------------------------------------------
# 5. Repo binding reuses the caller's guard.
# --------------------------------------------------------------------------
class TestRepoBinding(ArchFixture):

    def test_repo_outside_roots_is_refused(self):
        outside = Path(self._tmp.name) / "elsewhere"
        outside.mkdir()
        with self.assertRaises(PermissionError):
            arch.create_architecture(
                "Nope", str(outside), "", resolve_repo=self.resolver
            )

    def test_nothing_is_written_when_the_repo_is_refused(self):
        outside = Path(self._tmp.name) / "elsewhere"
        outside.mkdir()
        with self.assertRaises(PermissionError):
            arch.create_architecture("Nope", str(outside), "", resolve_repo=self.resolver)
        self.assertEqual(arch.list_architectures()["count"], 0)

    def test_repo_is_required(self):
        with self.assertRaises(ValueError):
            arch.create_architecture("No repo", "", "", resolve_repo=self.resolver)

    def test_wires_against_the_real_server_guard(self):
        """The contract that matters: server._resolve_repo is an acceptable resolver."""
        orig_root, orig_extra = server.PROJECTS_ROOT, server.EXTRA_REPOS
        server.PROJECTS_ROOT = self.repo.parent
        server.EXTRA_REPOS = ()
        try:
            doc = arch.create_architecture(
                "Wired", str(self.repo), "", resolve_repo=server._resolve_repo
            )
            self.assertEqual(doc["repo"], str(self.repo.resolve()))
            outside = Path(self._tmp.name) / "outside-roots"
            outside.mkdir()
            with self.assertRaises(PermissionError):
                arch.create_architecture(
                    "Rejected", str(outside), "", resolve_repo=server._resolve_repo
                )
        finally:
            server.PROJECTS_ROOT, server.EXTRA_REPOS = orig_root, orig_extra


# --------------------------------------------------------------------------
# 6. Create → read round trip.
# --------------------------------------------------------------------------
class TestCreateAndRead(ArchFixture):

    def test_seeds_all_sixteen_phases_as_pending(self):
        doc = arch.read_architecture(self.make()["id"])
        self.assertEqual(list(doc["phases"]), list(arch.PHASES))
        self.assertTrue(all(p["status"] == "pending" for p in doc["phases"].values()))

    def test_starts_at_the_first_phase(self):
        self.assertEqual(self.make()["current_phase"], "discover")

    def test_carries_schema_version(self):
        self.assertEqual(self.make()["schema_version"], arch.SCHEMA_VERSION)

    def test_project_id_matches_mem0_scope_resolution(self):
        doc = self.make()
        self.assertEqual(doc["project_id"], mem0_project.project_id(cwd=str(self.repo)))

    def test_defaults_to_active_with_no_tasks(self):
        doc = self.make()
        self.assertEqual(doc["status"], "active")
        self.assertEqual(doc["tasks"], [])

    def test_persists_to_a_json_file_named_after_the_id(self):
        doc = self.make()
        on_disk = json.loads((self.arch_dir / f"{doc['id']}.json").read_text("utf-8"))
        self.assertEqual(on_disk["title"], "Auth refresh flow")

    def test_title_is_required(self):
        for bad in ("", "   ", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                arch.create_architecture(bad, str(self.repo), "", resolve_repo=self.resolver)

    def test_overlong_title_is_refused(self):
        with self.assertRaises(ValueError):
            arch.create_architecture(
                "x" * (arch.MAX_TITLE + 1), str(self.repo), "", resolve_repo=self.resolver
            )

    def test_reading_an_unknown_id_raises_not_found(self):
        with self.assertRaises(FileNotFoundError):
            arch.read_architecture("does-not-exist")


# --------------------------------------------------------------------------
# 7. Enum validation at the boundary.
# --------------------------------------------------------------------------
class TestValidation(ArchFixture):

    def test_unknown_phase_key_is_refused(self):
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.add_task(aid, "deploy", "not a real phase")

    def test_bad_task_status_is_refused(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "t")["tasks"][0]["id"]
        with self.assertRaises(ValueError):
            arch.update_task(aid, tid, status="finished")

    def test_bad_architecture_status_is_refused(self):
        with self.assertRaises(ValueError):
            arch.update_architecture(self.make()["id"], status="ongoing")

    def test_bad_phase_status_is_refused(self):
        with self.assertRaises(ValueError):
            arch.update_architecture(
                self.make()["id"], phase={"key": "build", "status": "almost"}
            )

    def test_bad_current_phase_is_refused(self):
        with self.assertRaises(ValueError):
            arch.update_architecture(self.make()["id"], current_phase="deploy")

    def test_task_title_is_required(self):
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.add_task(aid, "build", "  ")

    def test_overlong_notes_are_refused(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "t")["tasks"][0]["id"]
        with self.assertRaises(ValueError):
            arch.update_task(aid, tid, notes="x" * (arch.MAX_TEXT + 1))

    def test_valid_updates_are_accepted(self):
        aid = self.make()["id"]
        doc = arch.update_architecture(
            aid, status="paused", current_phase="build",
            phase={"key": "discover", "status": "done", "note": "ok"},
        )
        self.assertEqual(doc["status"], "paused")
        self.assertEqual(doc["current_phase"], "build")
        self.assertEqual(doc["phases"]["discover"]["status"], "done")
        self.assertEqual(doc["phases"]["discover"]["note"], "ok")


# --------------------------------------------------------------------------
# 8-9. Tasks, and the counts derived from them.
# --------------------------------------------------------------------------
class TestTasks(ArchFixture):

    def test_add_returns_the_task_with_an_id(self):
        aid = self.make()["id"]
        doc = arch.add_task(aid, "build", "Add /api/architectures")
        self.assertEqual(len(doc["tasks"]), 1)
        self.assertTrue(doc["tasks"][0]["id"])
        self.assertEqual(doc["tasks"][0]["phase"], "build")
        self.assertEqual(doc["tasks"][0]["status"], "todo")

    def test_task_ids_are_unique(self):
        aid = self.make()["id"]
        for i in range(10):
            doc = arch.add_task(aid, "build", f"task {i}")
        ids = [t["id"] for t in doc["tasks"]]
        self.assertEqual(len(set(ids)), 10)

    def test_update_changes_status_and_title(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "old")["tasks"][0]["id"]
        doc = arch.update_task(aid, tid, status="done", title="new")
        self.assertEqual(doc["tasks"][0]["status"], "done")
        self.assertEqual(doc["tasks"][0]["title"], "new")

    def test_delete_removes_only_that_task(self):
        aid = self.make()["id"]
        keep = arch.add_task(aid, "build", "keep")["tasks"][0]["id"]
        drop = arch.add_task(aid, "build", "drop")["tasks"][1]["id"]
        doc = arch.delete_task(aid, drop)
        self.assertEqual([t["id"] for t in doc["tasks"]], [keep])

    def test_updating_an_unknown_task_raises_not_found(self):
        aid = self.make()["id"]
        with self.assertRaises(FileNotFoundError):
            arch.update_task(aid, "t-nope", status="done")

    def test_every_status_transition_is_accepted(self):
        """No state machine, no preconditions: an agent reporting `doing` must
        never be refused, and must not have to reach `done` through any
        particular path."""
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "t")["tasks"][0]["id"]
        for source in arch.TASK_STATUSES:
            for target in arch.TASK_STATUSES:
                with self.subTest(source=source, target=target):
                    arch.update_task(aid, tid, status=source)
                    doc = arch.update_task(aid, tid, status=target)
                    self.assertEqual(doc["tasks"][0]["status"], target)

    def test_repeating_a_status_is_harmless(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "t")["tasks"][0]["id"]
        for _ in range(3):
            doc = arch.update_task(aid, tid, status="doing")
        self.assertEqual(doc["counts"], {"total": 1, "todo": 0, "doing": 1, "blocked": 0, "done": 0})

    def test_doing_is_visible_in_the_counts(self):
        """The panel shows work in flight from this count; if `doing` were
        dropped the task would appear to jump from todo to done."""
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "t")["tasks"][0]["id"]
        doc = arch.update_task(aid, tid, status="doing")
        self.assertEqual(doc["counts"]["doing"], 1)
        self.assertEqual(doc["phases"]["build"]["counts"]["doing"], 1)

    def test_counts_derive_from_tasks_not_storage(self):
        aid = self.make()["id"]
        arch.add_task(aid, "build", "a")
        tid = arch.add_task(aid, "build", "b")["tasks"][1]["id"]
        arch.update_task(aid, tid, status="done")
        doc = arch.read_architecture(aid)
        self.assertEqual(doc["phases"]["build"]["counts"]["total"], 2)
        self.assertEqual(doc["phases"]["build"]["counts"]["done"], 1)
        self.assertAlmostEqual(doc["phases"]["build"]["progress"], 0.5)

    def test_derived_counts_are_never_persisted(self):
        aid = self.make()["id"]
        arch.add_task(aid, "build", "a")
        on_disk = json.loads((self.arch_dir / f"{aid}.json").read_text("utf-8"))
        self.assertNotIn("counts", on_disk["phases"]["build"])
        self.assertNotIn("progress", on_disk["phases"]["build"])

    def test_empty_phase_reports_zero_progress_not_full(self):
        doc = arch.read_architecture(self.make()["id"])
        self.assertEqual(doc["phases"]["spec"]["counts"]["total"], 0)
        self.assertEqual(doc["phases"]["spec"]["progress"], 0.0)

    def test_overall_task_counts_span_every_phase(self):
        aid = self.make()["id"]
        arch.add_task(aid, "build", "a")
        tid = arch.add_task(aid, "verify", "b")["tasks"][1]["id"]
        arch.update_task(aid, tid, status="done")
        doc = arch.read_architecture(aid)
        self.assertEqual(doc["counts"], {"total": 2, "done": 1, "doing": 0, "blocked": 0, "todo": 1})
        self.assertAlmostEqual(doc["task_progress"], 0.5)

    def test_moving_a_task_updates_both_phases(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "a")["tasks"][0]["id"]
        doc = arch.update_task(aid, tid, phase="verify")
        self.assertEqual(doc["phases"]["build"]["counts"]["total"], 0)
        self.assertEqual(doc["phases"]["verify"]["counts"]["total"], 1)

    def test_moving_a_task_to_an_unknown_phase_is_refused(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "a")["tasks"][0]["id"]
        with self.assertRaises(ValueError):
            arch.update_task(aid, tid, phase="deploy")


# --------------------------------------------------------------------------
# Auto-advance — a finished phase moves on by itself, behind named gates.
# --------------------------------------------------------------------------
class AutoAdvanceFixture(ArchFixture):

    def arch_with(self, phase, titles, template_id="full-16"):
        aid = arch.create_architecture(
            "Auto " + phase, str(self.repo), "", resolve_repo=self.resolver,
            template_id=template_id)["id"]
        arch.bulk_update_workflow(aid, {phase: {"status": "active"}})
        ids = []
        for title in titles:
            ids.append(arch.add_task(aid, phase, title)["tasks"][-1]["id"])
        return aid, ids

    def finish(self, aid, task_ids):
        for tid in task_ids:
            result = arch.update_task(aid, tid, status="done")
        return result


class TestOverallProgress(AutoAdvanceFixture):
    """Progress is phases behind you plus how far into the current one — counting
    tasks alone made one finished task in phase 2 of 4 read as 100% complete."""

    def test_a_fresh_architecture_is_at_zero(self):
        self.assertEqual(arch.read_architecture(self.make()["id"])["progress"], 0.0)

    def test_one_task_done_in_phase_two_of_four_is_not_complete(self):
        aid, ids = self.arch_with("build", ["only"], template_id="bugfix")
        arch.bulk_update_workflow(aid, {"discover": {"status": "done"},
                                        "build": {"status": "active"}})
        doc = arch.read_architecture(aid)
        self.assertEqual(doc["task_progress"], 0.0)
        self.assertAlmostEqual(doc["progress"], 0.25)          # 1 of 4 phases done
        arch.update_task(aid, ids[0], status="done")           # finishes build, advances
        doc = arch.read_architecture(aid)
        self.assertEqual(doc["task_progress"], 1.0)            # every task is done…
        self.assertAlmostEqual(doc["progress"], 0.5)           # …but half the protocol

    def test_partway_through_a_phase_counts_as_a_fraction(self):
        aid, ids = self.arch_with("build", ["a", "b", "c", "d"], template_id="bugfix")
        arch.bulk_update_workflow(aid, {"discover": {"status": "done"},
                                        "build": {"status": "active"}})
        arch.update_task(aid, ids[0], status="done")
        # 1 phase done + 1/4 of the current one, over 4 phases
        self.assertAlmostEqual(arch.read_architecture(aid)["progress"], (1 + 0.25) / 4)

    def test_only_the_last_phase_reaches_one(self):
        aid, ids = self.arch_with("verify", ["a"], template_id="prototype")
        arch.bulk_update_workflow(aid, {"discover": {"status": "done"},
                                        "build": {"status": "done"},
                                        "verify": {"status": "active"}})
        arch.update_task(aid, ids[0], status="done")           # last phase, no advance
        self.assertLess(arch.read_architecture(aid)["progress"], 1.0)
        doc = arch.bulk_update_workflow(aid, {"verify": {"status": "done"}})
        self.assertEqual(doc["progress"], 1.0)

    def test_an_empty_phase_contributes_nothing(self):
        aid = self.make()["id"]
        arch.bulk_update_workflow(aid, {"discover": {"status": "done"},
                                        "spec": {"status": "active"}})
        self.assertAlmostEqual(arch.read_architecture(aid)["progress"], 1 / 16)


class TestRollbackLogging(AutoAdvanceFixture):

    def test_going_back_a_phase_is_recorded(self):
        aid, ids = self.arch_with("build", ["a"])
        self.finish(aid, ids)                                   # build -> verify
        arch.bulk_update_workflow(aid, {"build": {"status": "active"},
                                        "verify": {"status": "pending"}})
        entry = arch.read_architecture(aid)["auto_advance_log"][-1]
        self.assertEqual((entry["from"], entry["to"], entry["kind"]),
                         ("verify", "build", "rollback"))

    def test_advances_are_labelled_too(self):
        aid, ids = self.arch_with("build", ["a"])
        self.finish(aid, ids)
        self.assertEqual(arch.read_architecture(aid)["auto_advance_log"][-1]["kind"], "advance")

    def test_moving_forward_by_hand_is_not_a_rollback(self):
        aid = self.make()["id"]
        arch.bulk_update_workflow(aid, {"discover": {"status": "done"},
                                        "spec": {"status": "active"}})
        kinds = [e["kind"] for e in arch.read_architecture(aid)["auto_advance_log"]]
        self.assertNotIn("rollback", kinds)


class TestAutoChainSetting(AutoAdvanceFixture):

    def test_off_by_default(self):
        self.assertFalse(arch.read_architecture(self.make()["id"])["auto_chain"])

    def test_toggles(self):
        aid = self.make()["id"]
        self.assertTrue(arch.set_auto_chain(aid, True)["auto_chain"])
        self.assertFalse(arch.set_auto_chain(aid, False)["auto_chain"])

    def test_it_is_independent_of_auto_advance(self):
        aid = self.make()["id"]
        arch.set_auto_chain(aid, True)
        arch.set_auto_advance(aid, False)
        doc = arch.read_architecture(aid)
        self.assertTrue(doc["auto_chain"])
        self.assertTrue(doc["auto_advance_disabled"])


class TestAutoAdvance(AutoAdvanceFixture):

    def test_finishing_every_task_moves_to_the_next_phase(self):
        aid, ids = self.arch_with("build", ["a", "b"])
        result = self.finish(aid, ids)
        move = result["auto_advanced"]
        self.assertEqual((move["from"], move["to"], move["kind"], move["tasks_completed"]),
                         ("build", "verify", "advance", 2))
        doc = arch.read_architecture(aid)
        self.assertEqual(doc["current_phase"], "verify")
        self.assertEqual(doc["phases"]["build"]["status"], "done")
        self.assertEqual(doc["phases"]["verify"]["status"], "active")

    def test_it_does_not_fire_until_the_last_task_is_done(self):
        aid, ids = self.arch_with("build", ["a", "b"])
        first = arch.update_task(aid, ids[0], status="done")
        self.assertIsNone(first["auto_advanced"])
        self.assertEqual(arch.read_architecture(aid)["current_phase"], "build")

    def test_a_blocked_task_holds_the_phase(self):
        aid, ids = self.arch_with("build", ["a", "b"])
        arch.update_task(aid, ids[0], status="done")
        result = arch.update_task(aid, ids[1], status="blocked")
        self.assertIsNone(result["auto_advanced"])
        self.assertEqual(arch.read_architecture(aid)["current_phase"], "build")
        self.assertIn("blocked", " ".join(arch.read_architecture(aid)["auto_advance_blockers"]))

    def test_an_empty_phase_never_advances(self):
        aid = self.make()["id"]
        arch.bulk_update_workflow(aid, {"build": {"status": "active"}})
        doc = arch.read_architecture(aid)
        self.assertEqual(doc["current_phase"], "build")
        self.assertIn("no tasks", " ".join(doc["auto_advance_blockers"]))

    def test_manual_phases_are_never_advanced_by_the_machine(self):
        for phase in arch.MANUAL_PHASES:
            with self.subTest(phase=phase):
                aid, ids = self.arch_with(phase, ["a"])
                result = self.finish(aid, ids)
                self.assertIsNone(result["auto_advanced"])
                self.assertEqual(arch.read_architecture(aid)["current_phase"], phase)

    def test_a_manual_phase_still_advances_when_a_person_says_so(self):
        aid, ids = self.arch_with("approve", ["a"])
        self.finish(aid, ids)
        doc = arch.bulk_update_workflow(aid, {"approve": {"status": "done"},
                                              "commit": {"status": "active"}})
        self.assertEqual(doc["current_phase"], "commit")

    def test_pausing_stops_it_even_with_everything_done(self):
        aid, ids = self.arch_with("build", ["a"])
        arch.set_auto_advance(aid, False)
        result = self.finish(aid, ids)
        self.assertIsNone(result["auto_advanced"])
        self.assertEqual(arch.read_architecture(aid)["current_phase"], "build")

    def test_resuming_then_re_reporting_does_not_advance_twice(self):
        """Resume advances once; re-sending a status the task already has must
        not walk the project a second phase forward."""
        aid, ids = self.arch_with("build", ["a", "b"])
        arch.set_auto_advance(aid, False)
        self.finish(aid, ids)
        self.assertEqual(arch.set_auto_advance(aid, True)["auto_advanced"]["to"], "verify")
        again = arch.update_task(aid, ids[0], status="done")
        self.assertIsNone(again["auto_advanced"])
        self.assertEqual(arch.read_architecture(aid)["current_phase"], "verify")

    def test_resuming_catches_a_phase_that_finished_while_paused(self):
        """The gate is only checked on a write, so without this a phase that
        completed while paused would sit finished-but-not-advanced forever."""
        aid, ids = self.arch_with("build", ["a"])
        arch.set_auto_advance(aid, False)
        self.finish(aid, ids)
        self.assertEqual(arch.read_architecture(aid)["current_phase"], "build")
        result = arch.set_auto_advance(aid, True)
        self.assertEqual(result["auto_advanced"]["to"], "verify")
        self.assertEqual(result["current_phase"], "verify")

    def test_resuming_a_phase_with_open_work_changes_nothing(self):
        aid, ids = self.arch_with("build", ["a", "b"])
        arch.set_auto_advance(aid, False)
        arch.update_task(aid, ids[0], status="done")
        result = arch.set_auto_advance(aid, True)
        self.assertIsNone(result["auto_advanced"])
        self.assertEqual(result["current_phase"], "build")

    def test_pausing_never_advances(self):
        aid, ids = self.arch_with("build", ["a"])
        self.finish(aid, ids)                       # already advanced to verify
        result = arch.set_auto_advance(aid, False)
        self.assertIsNone(result["auto_advanced"])

    def test_pause_and_resume_round_trip(self):
        aid = self.make()["id"]
        self.assertFalse(arch.read_architecture(aid)["auto_advance_disabled"])
        self.assertTrue(arch.set_auto_advance(aid, False)["auto_advance_disabled"])
        self.assertFalse(arch.set_auto_advance(aid, True)["auto_advance_disabled"])

    def test_the_last_phase_has_nowhere_to_go(self):
        aid, ids = self.arch_with("verify", ["a"], template_id="prototype")
        result = self.finish(aid, ids)
        self.assertIsNone(result["auto_advanced"])
        self.assertIn("last phase", " ".join(arch.read_architecture(aid)["auto_advance_blockers"]))

    def test_it_follows_the_template_s_own_order_not_the_master_list(self):
        """bugfix is discover -> build -> verify -> commit; finishing build must
        land on verify, and finishing verify on commit — never on a phase this
        architecture does not have."""
        aid, ids = self.arch_with("verify", ["a"], template_id="bugfix")
        result = self.finish(aid, ids)
        self.assertEqual(result["auto_advanced"], dict(result["auto_advanced"],
                                                       **{"from": "verify", "to": "commit"}))

    def test_deleting_the_last_open_task_can_advance(self):
        aid, ids = self.arch_with("build", ["keep", "drop"])
        arch.update_task(aid, ids[0], status="done")
        result = arch.delete_task(aid, ids[1])
        self.assertEqual(result["auto_advanced"]["to"], "verify")

    def test_each_advance_is_logged(self):
        aid, ids = self.arch_with("build", ["a"])
        self.finish(aid, ids)
        entry = arch.read_architecture(aid)["auto_advance_log"][-1]
        self.assertEqual((entry["from"], entry["to"], entry["tasks_completed"]),
                         ("build", "verify", 1))
        self.assertTrue(entry["triggered_at"].endswith("Z"))

    def test_the_log_does_not_grow_without_bound(self):
        aid = self.make()["id"]
        doc = arch._load(aid)
        doc["auto_advance_log"] = [{"from": "a", "to": "b"} for _ in range(50)]
        arch._save(doc)
        arch.bulk_update_workflow(aid, {"build": {"status": "active"}})
        tid = arch.add_task(aid, "build", "a")["tasks"][0]["id"]
        arch.update_task(aid, tid, status="done")
        self.assertLessEqual(len(arch.read_architecture(aid)["auto_advance_log"]),
                             arch.AUTO_ADVANCE_LOG_LIMIT)

    def test_it_advances_only_one_phase_per_report(self):
        """The next phase is empty, so it must not cascade through the protocol."""
        aid, ids = self.arch_with("build", ["a"])
        self.finish(aid, ids)
        self.assertEqual(arch.read_architecture(aid)["current_phase"], "verify")

    def test_blockers_are_empty_exactly_when_it_would_fire(self):
        aid, ids = self.arch_with("build", ["a"])
        self.assertTrue(arch.auto_advance_blockers(arch._load(aid)))
        arch.update_task(aid, ids[0], status="done")
        doc = arch._load(aid)
        self.assertEqual(doc["current_phase"], "verify")     # it fired
        self.assertTrue(arch.auto_advance_blockers(doc))     # verify is empty now

    def test_reported_shape_carries_the_flag_even_when_nothing_moved(self):
        aid, ids = self.arch_with("build", ["a", "b"])
        self.assertIn("auto_advanced", arch.update_task(aid, ids[0], status="doing"))


# --------------------------------------------------------------------------
# Templates — the shapes a new architecture can start from.
# --------------------------------------------------------------------------
class TestBuiltinCatalog(unittest.TestCase):

    def test_five_builtins(self):
        self.assertEqual([t["id"] for t in tpl.BUILTINS],
                         ["full-16", "feature", "bugfix", "code-review", "prototype"])

    def test_every_builtin_validates(self):
        for t in tpl.BUILTINS:
            with self.subTest(template=t["id"]):
                self.assertEqual(tpl.validate(t), [])

    def test_phases_are_an_ordered_subset_of_the_master_catalog(self):
        for t in tpl.BUILTINS:
            with self.subTest(template=t["id"]):
                self.assertTrue(set(t["phases"]) <= set(tpl.PHASES))
                self.assertEqual(t["phases"], sorted(t["phases"], key=tpl.PHASES.index))
                self.assertEqual(len(set(t["phases"])), len(t["phases"]))

    def test_loops_only_reference_their_own_phases(self):
        for t in tpl.BUILTINS:
            for loop in t["loops"]:
                with self.subTest(template=t["id"], loop=loop):
                    self.assertIn(loop["from"], t["phases"])
                    self.assertIn(loop["to"], t["phases"])

    def test_full_16_is_the_whole_protocol(self):
        full = next(t for t in tpl.BUILTINS if t["id"] == "full-16")
        self.assertEqual(full["phases"], list(tpl.PHASES))

    def test_tiers_are_valid(self):
        for t in tpl.BUILTINS:
            self.assertIn(t["tier"], tpl.TIERS)


class TestTemplateValidation(unittest.TestCase):

    def base(self, **over):
        return dict({"id": "x", "name": "X", "tier": "S",
                     "phases": ["discover", "build"]}, **over)

    def test_rejects_unknown_phase(self):
        self.assertTrue(tpl.validate(self.base(phases=["discover", "deploy"])))

    def test_rejects_out_of_order_phases(self):
        self.assertTrue(tpl.validate(self.base(phases=["build", "discover"])))

    def test_rejects_duplicate_phases(self):
        self.assertTrue(tpl.validate(self.base(phases=["build", "build"])))

    def test_rejects_empty_phases(self):
        self.assertTrue(tpl.validate(self.base(phases=[])))

    def test_rejects_loop_outside_its_phases(self):
        self.assertTrue(tpl.validate(self.base(loops=[{"from": "verify", "to": "build"}])))

    def test_rejects_bad_tier_and_long_name(self):
        self.assertTrue(tpl.validate(self.base(tier="XL")))
        self.assertTrue(tpl.validate(self.base(name="x" * 61)))

    def test_rejects_bad_id(self):
        for bad in ("../escape", "Has Caps", "", None):
            with self.subTest(bad=bad):
                self.assertTrue(tpl.validate(self.base(id=bad)))

    def test_rejects_playbook_for_a_phase_not_in_the_template(self):
        self.assertTrue(tpl.validate(self.base(playbook={"verify": {}})))

    def test_rejects_seed_task_outside_its_phases(self):
        self.assertTrue(tpl.validate(self.base(seed_tasks=[{"phase": "verify", "title": "x"}])))

    def test_accepts_a_minimal_template(self):
        self.assertEqual(tpl.validate(self.base()), [])

    def test_loop_label_defaults_to_fail(self):
        t = tpl.normalize(self.base(loops=[{"from": "build", "to": "discover"}]))
        self.assertEqual(t["loops"][0]["label"], "fail")


class TestUserTemplates(ArchFixture):

    def user_file(self, name, doc):
        d = tpl.user_dir(arch.ARCH_DIR)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(doc) if isinstance(doc, dict) else doc, encoding="utf-8")

    def sample(self, **over):
        return dict({"id": "spike", "name": "Spike", "tier": "S",
                     "phases": ["discover", "build"],
                     "loops": [{"from": "build", "to": "discover", "label": "rethink"}]}, **over)

    def test_round_trip_create_list_delete(self):
        arch.create_template(self.sample())
        ids = [t["id"] for t in arch.templates()["results"]]
        self.assertIn("spike", ids)
        arch.delete_template("spike")
        self.assertNotIn("spike", [t["id"] for t in arch.templates()["results"]])

    def test_created_template_is_marked_as_user_source(self):
        arch.create_template(self.sample())
        row = next(t for t in arch.templates()["results"] if t["id"] == "spike")
        self.assertEqual(row["source"], "user")

    def test_creating_with_a_builtin_id_is_refused(self):
        with self.assertRaises(FileExistsError):
            arch.create_template(self.sample(id="bugfix"))

    def test_creating_twice_is_refused(self):
        arch.create_template(self.sample())
        with self.assertRaises(FileExistsError):
            arch.create_template(self.sample())

    def test_deleting_a_builtin_is_refused(self):
        with self.assertRaises(PermissionError):
            arch.delete_template("bugfix")

    def test_deleting_an_unknown_template_is_not_found(self):
        with self.assertRaises(FileNotFoundError):
            arch.delete_template("never-existed")

    def test_template_id_cannot_escape_the_store(self):
        for bad in ("../escape", "/etc/passwd", "Caps"):
            with self.subTest(bad=bad), self.assertRaises((ValueError, PermissionError)):
                arch.delete_template(bad)

    def test_invalid_template_is_refused_at_create(self):
        with self.assertRaises(ValueError):
            arch.create_template(self.sample(phases=["build", "discover"]))

    def test_malformed_file_is_skipped_with_a_warning(self):
        self.user_file("broken.json", "{not json")
        catalog = arch.templates()
        self.assertEqual(len([t for t in catalog["results"] if t["id"] == "broken"]), 0)
        self.assertTrue(any("broken.json" in w for w in catalog["warnings"]))
        self.assertGreaterEqual(catalog["count"], len(tpl.BUILTINS))

    def test_invalid_file_is_skipped_with_a_warning(self):
        self.user_file("bad.json", {"id": "bad", "name": "Bad", "tier": "S",
                                    "phases": ["nope"]})
        catalog = arch.templates()
        self.assertNotIn("bad", [t["id"] for t in catalog["results"]])
        self.assertTrue(any("bad.json" in w for w in catalog["warnings"]))

    def test_user_file_shadows_a_builtin_of_the_same_id(self):
        self.user_file("bugfix.json", {"id": "bugfix", "name": "My Bug Fix", "tier": "M",
                                       "phases": ["build", "verify"]})
        row = next(t for t in arch.templates()["results"] if t["id"] == "bugfix")
        self.assertEqual(row["name"], "My Bug Fix")
        self.assertEqual(row["source"], "user")
        self.assertTrue(row["shadows_builtin"])

    def test_get_template_returns_one_and_404s_on_unknown(self):
        self.assertEqual(arch.get_template("bugfix")["id"], "bugfix")
        with self.assertRaises(FileNotFoundError):
            arch.get_template("nope")

    def test_templates_directory_is_not_listed_as_an_architecture(self):
        arch.create_template(self.sample())
        self.make("Real one")
        self.assertEqual([r["id"] for r in arch.list_architectures()["results"]], ["real-one"])


class TestCreateFromTemplate(ArchFixture):

    def make_from(self, template_id, title="T"):
        return arch.create_architecture(title, str(self.repo), "",
                                        resolve_repo=self.resolver, template_id=template_id)

    def test_every_builtin_produces_a_readable_architecture(self):
        for t in tpl.BUILTINS:
            with self.subTest(template=t["id"]):
                doc = self.make_from(t["id"], "A " + t["id"])
                self.assertEqual(doc["template_id"], t["id"])
                self.assertEqual(list(doc["phases"]), t["phases"])
                self.assertEqual(doc["current_phase"], t["phases"][0])
                self.assertEqual([(l["from"], l["to"]) for l in doc["loops"]],
                                 [(l["from"], l["to"]) for l in t["loops"]])
                self.assertEqual(list(doc["playbook"]), t["phases"])

    def test_default_template_is_the_full_protocol(self):
        doc = arch.create_architecture("Default", str(self.repo), "",
                                       resolve_repo=self.resolver)
        self.assertEqual(doc["template_id"], "full-16")
        self.assertEqual(len(doc["phases"]), 16)

    def test_small_template_has_only_its_own_phases(self):
        doc = self.make_from("bugfix")
        self.assertEqual(list(doc["phases"]), ["discover", "build", "verify", "commit"])
        self.assertNotIn("release", doc["phases"])

    def test_unknown_template_is_refused(self):
        with self.assertRaises(ValueError):
            self.make_from("no-such-template")

    def test_nothing_is_written_when_the_template_is_unknown(self):
        with self.assertRaises(ValueError):
            self.make_from("no-such-template")
        self.assertEqual(arch.list_architectures()["count"], 0)

    def test_task_cannot_use_a_phase_outside_the_template(self):
        aid = self.make_from("bugfix")["id"]
        with self.assertRaises(ValueError):
            arch.add_task(aid, "release", "not in this template")

    def test_bulk_update_cannot_use_a_phase_outside_the_template(self):
        aid = self.make_from("bugfix")["id"]
        with self.assertRaises(ValueError):
            arch.bulk_update_workflow(aid, {"release": {"status": "done"}})

    def test_current_phase_recompute_respects_the_subset(self):
        aid = self.make_from("bugfix")["id"]
        doc = arch.bulk_update_workflow(aid, {"discover": {"status": "done"}})
        self.assertEqual(doc["current_phase"], "build")

    def test_loops_survive_a_round_trip(self):
        aid = self.make_from("full-16")["id"]
        stored = arch.read_architecture(aid)["loops"]
        self.assertEqual(len(stored), 4)
        self.assertIn({"from": "approve", "to": "spec", "label": "reject"}, stored)

    def test_summary_row_carries_template_and_loop_count(self):
        self.make_from("bugfix")
        row = arch.list_architectures()["results"][0]
        self.assertEqual(row["template_id"], "bugfix")
        self.assertEqual(row["loop_count"], 1)

    def test_seed_tasks_are_created(self):
        arch.create_template({"id": "seeded", "name": "Seeded", "tier": "S",
                              "phases": ["build"],
                              "seed_tasks": [{"phase": "build", "title": "first", "priority": "p0"}]})
        doc = self.make_from("seeded")
        self.assertEqual(len(doc["tasks"]), 1)
        self.assertEqual(doc["tasks"][0]["title"], "first")
        self.assertEqual(doc["tasks"][0]["priority"], "P0")

    def test_editing_a_template_does_not_rewrite_existing_architectures(self):
        """The template is copied in, not referenced — work already under way
        must not change shape because a template was edited later."""
        arch.create_template(self.sample_spike())
        aid = self.make_from("spike")["id"]
        arch.delete_template("spike")
        doc = arch.read_architecture(aid)
        self.assertEqual(list(doc["phases"]), ["discover", "build"])
        self.assertEqual(doc["template_id"], "spike")

    def sample_spike(self):
        return {"id": "spike", "name": "Spike", "tier": "S",
                "phases": ["discover", "build"]}


class TestMigrationAddsTemplateFields(ArchFixture):
    """The converter must give a pre-template document its provenance and its
    loops, and must still be a no-op on the second run."""

    def legacy(self):
        return {
            "id": "legacy", "name": "Legacy", "description": "old shape",
            "status": "active", "current_phase": "Build", "progress": 44,
            "workflow": {p: {"status": "in_progress" if p == "build" else "pending"}
                         for p in tpl.PHASES},
            "tasks": [{"id": "task-000", "phase": None, "status": "in_progress",
                       "priority": "P0", "title": "old task"}],
            "context_files": [], "created_at": "2026-08-01", "updated_at": "2026-08-01",
        }

    def test_gains_template_id_and_loops(self):
        out = migrate.migrate_doc(self.legacy(), self.repo, "demo")
        self.assertEqual(out["template_id"], "full-16")
        self.assertEqual([(l["from"], l["to"], l["label"]) for l in out["loops"]],
                         [("verify", "build", "fail"), ("review", "build", "fail"),
                          ("ci", "build", "fail"), ("approve", "spec", "reject")])

    def test_migrated_document_validates(self):
        out = migrate.migrate_doc(self.legacy(), self.repo, "demo")
        self.assertEqual(migrate.validate(out), [])

    def test_is_idempotent(self):
        once = migrate.migrate_doc(self.legacy(), self.repo, "demo")
        twice = migrate.migrate_doc(once, self.repo, "demo")
        self.assertEqual(once, twice)

    def test_does_not_overwrite_an_existing_template_id(self):
        doc = dict(self.legacy(), template_id="bugfix",
                   loops=[{"from": "verify", "to": "build", "label": "fail"}])
        out = migrate.migrate_doc(doc, self.repo, "demo")
        self.assertEqual(out["template_id"], "bugfix")
        self.assertEqual(len(out["loops"]), 1)

    def test_backup_skips_the_templates_directory(self):
        (arch.ARCH_DIR / "templates").mkdir(parents=True, exist_ok=True)
        (arch.ARCH_DIR / "templates" / "spike.json").write_text("{}", encoding="utf-8")
        (arch.ARCH_DIR / "real.json").write_text("{}", encoding="utf-8")
        dest, copied = migrate.backup(arch.ARCH_DIR)
        self.assertEqual(copied, ["real.json"])
        self.assertTrue(dest.is_dir())
        self.assertFalse((dest / "templates").exists())


# --------------------------------------------------------------------------
# Phase playbook — which agents/skills/rules each phase reaches for.
# --------------------------------------------------------------------------
class TestPlaybook(unittest.TestCase):

    def test_covers_every_phase(self):
        book = arch.load_playbook()
        self.assertEqual(list(book), list(arch.PHASES))

    def test_every_entry_has_the_three_lists(self):
        for key, entry in arch.load_playbook().items():
            with self.subTest(phase=key):
                for field in ("agents", "skills", "rules"):
                    self.assertIsInstance(entry[field], list)
                    self.assertTrue(all(isinstance(x, str) and x for x in entry[field]))

    def test_names_carry_no_placeholders(self):
        """Empty is honest; a made-up name would send a chip to a panel entry
        that does not exist."""
        banned = {"tbd", "todo", "none", "human", "n/a", "-"}
        for key, entry in arch.load_playbook().items():
            for field in ("agents", "skills", "rules"):
                for name in entry[field]:
                    with self.subTest(phase=key, name=name):
                        self.assertNotIn(name.strip().lower(), banned)

    def test_approve_is_a_human_gate_with_no_agent(self):
        entry = arch.load_playbook()["approve"]
        self.assertEqual(entry["agents"], [])
        self.assertTrue(entry["note"])

    def test_detail_response_carries_the_playbook(self):
        # Uses the real store path; the fixture-based tests below cover writes.
        book = arch.load_playbook()
        self.assertEqual(len(book), 16)

    def test_broken_playbook_file_degrades_to_empty(self):
        original = arch.PLAYBOOK_FILE
        arch.PLAYBOOK_FILE = Path(tempfile.gettempdir()) / "no-such-playbook.json"
        try:
            book = arch.load_playbook()
        finally:
            arch.PLAYBOOK_FILE = original
        self.assertEqual(list(book), list(arch.PHASES))
        self.assertEqual(book["build"]["agents"], [])


class TestPlaybookOnDetail(ArchFixture):

    def test_read_architecture_includes_playbook_for_all_phases(self):
        doc = arch.read_architecture(self.make()["id"])
        self.assertEqual(list(doc["playbook"]), list(arch.PHASES))
        self.assertIn("code-reviewer", doc["playbook"]["review"]["agents"])

    def test_list_rows_stay_lean(self):
        self.make()
        self.assertNotIn("playbook", arch.list_architectures()["results"][0])


# --------------------------------------------------------------------------
# Bulk workflow update — the phase modal moves several phases atomically.
# --------------------------------------------------------------------------
class TestBulkWorkflow(ArchFixture):

    def test_updates_three_phases_in_one_call(self):
        aid = self.make()["id"]
        doc = arch.bulk_update_workflow(aid, {
            "discover": {"status": "done"},
            "spec": {"status": "done"},
            "context": {"status": "active"},
        })
        self.assertEqual(doc["phases"]["discover"]["status"], "done")
        self.assertEqual(doc["phases"]["spec"]["status"], "done")
        self.assertEqual(doc["phases"]["context"]["status"], "active")

    def test_untouched_phases_keep_their_state(self):
        aid = self.make()["id"]
        arch.bulk_update_workflow(aid, {"discover": {"status": "done", "note": "keep me"}})
        doc = arch.bulk_update_workflow(aid, {"spec": {"status": "active"}})
        self.assertEqual(doc["phases"]["discover"]["status"], "done")
        self.assertEqual(doc["phases"]["discover"]["note"], "keep me")

    def test_notes_can_be_set_alongside_status(self):
        aid = self.make()["id"]
        doc = arch.bulk_update_workflow(aid, {"build": {"status": "active", "note": "wip"}})
        self.assertEqual(doc["phases"]["build"]["note"], "wip")

    def test_current_phase_follows_the_active_phase(self):
        aid = self.make()["id"]
        doc = arch.bulk_update_workflow(aid, {
            "discover": {"status": "done"}, "spec": {"status": "active"},
        })
        self.assertEqual(doc["current_phase"], "spec")

    def test_current_phase_falls_to_first_unfinished_when_none_is_active(self):
        aid = self.make()["id"]
        doc = arch.bulk_update_workflow(aid, {
            "discover": {"status": "done"}, "spec": {"status": "skipped"},
        })
        self.assertEqual(doc["current_phase"], "context")

    def test_current_phase_is_the_last_when_everything_is_finished(self):
        aid = self.make()["id"]
        doc = arch.bulk_update_workflow(aid, {k: {"status": "done"} for k in arch.PHASES})
        self.assertEqual(doc["current_phase"], arch.PHASES[-1])

    def test_unknown_phase_key_is_refused(self):
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.bulk_update_workflow(aid, {"deploy": {"status": "done"}})

    def test_unknown_status_is_refused(self):
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.bulk_update_workflow(aid, {"build": {"status": "in_progress"}})

    def test_empty_patch_is_refused(self):
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.bulk_update_workflow(aid, {})

    def test_non_object_patch_is_refused(self):
        aid = self.make()["id"]
        for bad in ([], "done", None, 3):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                arch.bulk_update_workflow(aid, bad)

    def test_phase_value_must_be_an_object(self):
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.bulk_update_workflow(aid, {"build": "done"})

    def test_all_or_nothing_when_one_key_is_bad(self):
        """A valid phase alongside an invalid one must not be applied."""
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.bulk_update_workflow(aid, {
                "discover": {"status": "done"},     # valid
                "deploy": {"status": "done"},       # not a phase
            })
        self.assertEqual(arch.read_architecture(aid)["phases"]["discover"]["status"], "pending")

    def test_all_or_nothing_when_the_write_fails(self):
        aid = self.make()["id"]
        real_replace = arch.os.replace
        arch.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with self.assertRaises(OSError):
                arch.bulk_update_workflow(aid, {k: {"status": "done"} for k in arch.PHASES})
        finally:
            arch.os.replace = real_replace
        doc = arch.read_architecture(aid)
        self.assertTrue(all(p["status"] == "pending" for p in doc["phases"].values()))

    def test_concurrent_bulk_updates_leave_a_readable_document(self):
        aid = self.make()["id"]
        errors = []

        def worker(key):
            try:
                arch.bulk_update_workflow(aid, {key: {"status": "done"}})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(k,)) for k in arch.PHASES]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        doc = arch.read_architecture(aid)
        self.assertTrue(all(doc["phases"][k]["status"] == "done" for k in arch.PHASES))

    def test_unknown_architecture_raises_not_found(self):
        with self.assertRaises(FileNotFoundError):
            arch.bulk_update_workflow("nope", {"build": {"status": "done"}})


# --------------------------------------------------------------------------
# Task priority — P0 (drop everything) .. P4 (someday).
# --------------------------------------------------------------------------
class TestPriorityCatalog(unittest.TestCase):

    def test_five_ordered_priorities(self):
        self.assertEqual(arch.TASK_PRIORITIES, ("P0", "P1", "P2", "P3", "P4"))

    def test_default_is_p2_and_is_a_valid_priority(self):
        self.assertEqual(arch.TASK_PRIORITY_DEFAULT, "P2")
        self.assertIn(arch.TASK_PRIORITY_DEFAULT, arch.TASK_PRIORITIES)


class TestNormPriority(unittest.TestCase):

    def test_uppercases_lowercase_input(self):
        self.assertEqual(arch._norm_priority("p0"), "P0")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(arch._norm_priority("  p3  "), "P3")

    def test_passes_through_canonical_form(self):
        self.assertEqual(arch._norm_priority("P4"), "P4")

    def test_none_means_the_default(self):
        self.assertEqual(arch._norm_priority(None), arch.TASK_PRIORITY_DEFAULT)

    def test_out_of_range_is_refused(self):
        for bad in ("P5", "P-1", "p9"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                arch._norm_priority(bad)

    def test_nonsense_is_refused(self):
        for bad in ("high", "", "  ", "0", 0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                arch._norm_priority(bad)


class TestTaskPriority(ArchFixture):

    def test_new_task_defaults_to_p2(self):
        aid = self.make()["id"]
        doc = arch.add_task(aid, "build", "no priority given")
        self.assertEqual(doc["tasks"][0]["priority"], "P2")

    def test_new_task_accepts_an_explicit_priority(self):
        aid = self.make()["id"]
        doc = arch.add_task(aid, "build", "urgent", priority="P0")
        self.assertEqual(doc["tasks"][0]["priority"], "P0")

    def test_new_task_normalizes_lowercase(self):
        aid = self.make()["id"]
        doc = arch.add_task(aid, "build", "urgent", priority="p1")
        self.assertEqual(doc["tasks"][0]["priority"], "P1")

    def test_new_task_with_a_bad_priority_is_refused(self):
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.add_task(aid, "build", "nope", priority="P9")

    def test_bad_priority_writes_nothing(self):
        aid = self.make()["id"]
        with self.assertRaises(ValueError):
            arch.add_task(aid, "build", "nope", priority="P9")
        self.assertEqual(arch.read_architecture(aid)["tasks"], [])

    def test_update_changes_priority(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "t")["tasks"][0]["id"]
        doc = arch.update_task(aid, tid, priority="p0")
        self.assertEqual(doc["tasks"][0]["priority"], "P0")

    def test_update_refuses_a_bad_priority(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "t", priority="P1")["tasks"][0]["id"]
        with self.assertRaises(ValueError):
            arch.update_task(aid, tid, priority="urgent")
        self.assertEqual(arch.read_architecture(aid)["tasks"][0]["priority"], "P1")

    def test_priority_survives_a_round_trip_to_disk(self):
        aid = self.make()["id"]
        arch.add_task(aid, "build", "t", priority="P4")
        on_disk = json.loads((self.arch_dir / f"{aid}.json").read_text("utf-8"))
        self.assertEqual(on_disk["tasks"][0]["priority"], "P4")

    def test_updating_another_field_leaves_priority_alone(self):
        aid = self.make()["id"]
        tid = arch.add_task(aid, "build", "t", priority="P0")["tasks"][0]["id"]
        doc = arch.update_task(aid, tid, status="done")
        self.assertEqual(doc["tasks"][0]["priority"], "P0")

    def test_listing_publishes_the_priority_catalog_for_the_ui(self):
        listed = arch.list_architectures()
        self.assertEqual(tuple(listed["task_priorities"]), arch.TASK_PRIORITIES)
        self.assertEqual(listed["task_priority_default"], arch.TASK_PRIORITY_DEFAULT)


# --------------------------------------------------------------------------
# 10. A corrupt file must read as a controlled error, not a 500.
# --------------------------------------------------------------------------
class TestMalformedStorage(ArchFixture):

    def test_malformed_json_raises_value_error(self):
        aid = self.make()["id"]
        (self.arch_dir / f"{aid}.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            arch.read_architecture(aid)

    def test_non_object_json_raises_value_error(self):
        aid = self.make()["id"]
        (self.arch_dir / f"{aid}.json").write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(ValueError):
            arch.read_architecture(aid)

    def test_listing_survives_one_corrupt_file(self):
        good = self.make("Healthy one")["id"]
        bad = self.make("Broken one")["id"]
        (self.arch_dir / f"{bad}.json").write_text("{oops", encoding="utf-8")
        listed = arch.list_architectures()
        ids = [r["id"] for r in listed["results"]]
        self.assertIn(good, ids)
        self.assertIn(bad, listed["errors"][0])


# --------------------------------------------------------------------------
# 11. Writes are atomic.
# --------------------------------------------------------------------------
class TestAtomicWrite(ArchFixture):

    def test_failed_replace_leaves_the_original_intact(self):
        aid = self.make("Original title")["id"]
        original = (self.arch_dir / f"{aid}.json").read_text("utf-8")
        real_replace = arch.os.replace

        def boom(*a, **k):
            raise OSError("disk full")

        arch.os.replace = boom
        try:
            with self.assertRaises(OSError):
                arch.update_architecture(aid, title="Clobbered")
        finally:
            arch.os.replace = real_replace
        self.assertEqual((self.arch_dir / f"{aid}.json").read_text("utf-8"), original)

    def test_failed_replace_leaves_no_temp_files_behind(self):
        aid = self.make()["id"]
        real_replace = arch.os.replace
        arch.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with self.assertRaises(OSError):
                arch.update_architecture(aid, title="Clobbered")
        finally:
            arch.os.replace = real_replace
        self.assertEqual([p.name for p in self.arch_dir.iterdir()], [f"{aid}.json"])


# --------------------------------------------------------------------------
# 12. Concurrent writes must not lose data (ThreadingHTTPServer).
# --------------------------------------------------------------------------
class TestConcurrency(ArchFixture):

    def test_parallel_add_task_loses_nothing(self):
        aid = self.make()["id"]
        errors = []

        def worker(n):
            try:
                arch.add_task(aid, "build", f"task {n}")
            except Exception as e:  # surfaced below — a swallowed one would fake a pass
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(arch.read_architecture(aid)["tasks"]), 20)

    def test_parallel_create_produces_distinct_ids(self):
        made = []

        def worker():
            made.append(self.make("Same title")["id"])

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(made)), 10)


# --------------------------------------------------------------------------
# 13. Delete.
# --------------------------------------------------------------------------
class TestDelete(ArchFixture):

    def test_delete_removes_the_file(self):
        aid = self.make()["id"]
        arch.delete_architecture(aid)
        self.assertFalse((self.arch_dir / f"{aid}.json").exists())

    def test_delete_of_unknown_id_raises_not_found(self):
        with self.assertRaises(FileNotFoundError):
            arch.delete_architecture("never-existed")

    def test_delete_refuses_a_traversing_id(self):
        with self.assertRaises((ValueError, PermissionError)):
            arch.delete_architecture("../../etc/passwd")


# --------------------------------------------------------------------------
# Listing, filtering, and the health count.
# --------------------------------------------------------------------------
class TestListing(ArchFixture):

    def test_empty_store_lists_nothing(self):
        listed = arch.list_architectures()
        self.assertEqual(listed["count"], 0)
        self.assertEqual(listed["results"], [])

    def test_list_exposes_the_phase_catalog_for_the_ui(self):
        self.assertEqual(tuple(arch.list_architectures()["phases"]), arch.PHASES)

    def test_summary_rows_carry_counts_without_the_full_task_list(self):
        aid = self.make()["id"]
        arch.add_task(aid, "build", "a")
        row = arch.list_architectures()["results"][0]
        self.assertEqual(row["counts"]["total"], 1)
        self.assertNotIn("tasks", row)

    def test_filters_by_status(self):
        keep = self.make("Keeper")["id"]
        drop = self.make("Dropper")["id"]
        arch.update_architecture(drop, status="archived")
        ids = [r["id"] for r in arch.list_architectures(status="active")["results"]]
        self.assertEqual(ids, [keep])

    def test_repo_filter_skips_documents_bound_to_nothing(self):
        """Path("").resolve() is the cwd — an unbound document must not match it."""
        aid = self.make("Unbound")["id"]
        doc = arch._load(aid)
        doc["repo"] = ""
        arch._save(doc)
        import os
        listed = arch.list_architectures(repo=os.getcwd())
        self.assertNotIn(aid, [r["id"] for r in listed["results"]])

    def test_filters_by_repo(self):
        self.make("One")
        ids = [r["id"] for r in arch.list_architectures(repo=str(self.repo))["results"]]
        self.assertEqual(len(ids), 1)
        self.assertEqual(arch.list_architectures(repo="/nowhere")["count"], 0)

    def test_query_matches_title_and_summary(self):
        self.make("Auth refresh flow", "token rotation")
        self.make("Billing export", "csv dump")
        self.assertEqual(arch.list_architectures(q="rotation")["count"], 1)
        self.assertEqual(arch.list_architectures(q="AUTH")["count"], 1)
        self.assertEqual(arch.list_architectures(q="zzz")["count"], 0)

    def test_count_helper_matches_the_listing(self):
        self.make("One")
        self.make("Two")
        self.assertEqual(arch.count_architectures(), 2)

    def test_count_helper_is_zero_when_the_directory_is_absent(self):
        self.assertEqual(arch.count_architectures(), 0)


# --------------------------------------------------------------------------
# The panel's markup. The frontend is one inlined string, so the only thing a
# unit test can hold it to is that the wiring is present — same approach
# test_codemap.py takes.
# --------------------------------------------------------------------------
class ArchMarkupFixture(unittest.TestCase):

    def setUp(self):
        from assets import PAGE_HTML
        self.html = PAGE_HTML


class TestOverviewArchitecturesTile(ArchMarkupFixture):
    """`read_health` counts architectures; the Overview has to show the number."""

    def _tiles_block(self):
        start = self.html.index("const tiles = [")
        return self.html[start:self.html.index("];", start)]

    def test_a_tile_reads_the_health_count(self):
        self.assertIn("h.architectures_count", self._tiles_block())

    def test_the_tile_is_labelled(self):
        self.assertIn("'Architectures'", self._tiles_block())

    def test_the_tile_falls_back_when_the_count_is_missing(self):
        """read_health stores None when the count fails — that must not print "null"."""
        self.assertIn("h.architectures_count ??", self._tiles_block())


class TestArchitectureDeleteControl(ArchMarkupFixture):
    """DELETE /api/architecture has been routed since the panel landed, unreachable."""

    def _delete_fn(self):
        start = self.html.index("async function deleteArchitecture(")
        return self.html[start:self.html.index("\n}", start)]

    def test_the_detail_header_has_a_delete_button(self):
        self.assertIn('id="arch-delete"', self.html)

    def test_the_button_is_wired(self):
        self.assertIn("$('#arch-delete').addEventListener", self.html)

    def test_it_confirms_before_deleting(self):
        self.assertIn("confirm(", self._delete_fn())

    def test_it_calls_the_delete_route_with_an_encoded_id(self):
        block = self._delete_fn()
        self.assertIn("/api/architecture?id=${encodeURIComponent(id)}", block)
        self.assertIn("method: 'DELETE'", block)

    def test_it_returns_to_the_list_afterwards(self):
        """The detail view it was rendering no longer has a document behind it."""
        self.assertIn("renderArchList", self._delete_fn())

    def test_it_reports_a_failure_rather_than_swallowing_it(self):
        self.assertIn("toast(", self._delete_fn())
        self.assertIn("catch", self._delete_fn())


# --------------------------------------------------------------------------
# The human gate, end to end. Finishing an approve task is not approval —
# somebody still has to say yes. This is the transition nothing covered, and
# the reason `approve` looked stuck: the tasks were done and the phase stayed.
# --------------------------------------------------------------------------
class TestHumanGateEndToEnd(AutoAdvanceFixture):

    def gate(self):
        """A code-review architecture parked on `approve` with open tasks."""
        aid, ids = self.arch_with("approve", ["verify the diff", "check the tests"],
                                  template_id="code-review")
        return aid, ids

    def test_finishing_every_approve_task_does_not_advance_the_phase(self):
        aid, ids = self.gate()
        doc = self.finish(aid, ids)
        self.assertEqual(doc["current_phase"], "approve")
        self.assertEqual(doc["phases"]["approve"]["status"], "active")
        self.assertEqual(doc["phases"]["commit"]["status"], "pending")

    def test_the_panel_can_say_which_gate_is_holding(self):
        aid, ids = self.gate()
        self.finish(aid, ids)
        blockers = arch.read_architecture(aid)["auto_advance_blockers"]
        self.assertIn("`approve` is a manual phase — a person advances it", blockers)

    def test_no_task_is_left_open_by_the_gate(self):
        """The hold is the gate, not unfinished work — otherwise the reason the
        panel prints would be wrong."""
        aid, ids = self.gate()
        self.finish(aid, ids)
        counts = arch.read_architecture(aid)["phases"]["approve"]["counts"]
        self.assertEqual(counts["done"], len(ids))
        self.assertEqual(counts["todo"], 0)

    def test_the_human_advances_it_and_commit_becomes_active(self):
        aid, ids = self.gate()
        self.finish(aid, ids)
        doc = arch.bulk_update_workflow(aid, {"approve": {"status": "done"},
                                              "commit": {"status": "active"}})
        self.assertEqual(doc["current_phase"], "commit")
        self.assertEqual(doc["phases"]["approve"]["status"], "done")
        self.assertEqual(doc["phases"]["commit"]["status"], "active")

    def test_the_gate_holds_even_with_auto_advance_left_on(self):
        """auto_advance is the automation the manual phase overrides; a user who
        never touched the toggle must still get the gate."""
        aid, ids = self.gate()
        self.assertFalse(arch.read_architecture(aid).get("auto_advance_disabled"))
        self.finish(aid, ids)
        self.assertEqual(arch.read_architecture(aid)["current_phase"], "approve")

    def test_a_non_manual_phase_still_advances_by_itself(self):
        """Regression: the gate must not have frozen every phase."""
        aid, ids = self.arch_with("context", ["read the repo"], template_id="code-review")
        doc = self.finish(aid, ids)
        self.assertEqual(doc["current_phase"], "review")


# --------------------------------------------------------------------------
# The vector store moved out of Docker and into a vendored binary. The health
# tile kept reporting the stopped container as a fault — a red light nobody can
# act on trains you to ignore the row it sits in.
# --------------------------------------------------------------------------
class TestVectorStoreTransport(unittest.TestCase):

    def call(self, qdrant, docker):
        import server
        return server.vector_store_transport(dict(qdrant), dict(docker))

    def test_a_stopped_container_is_not_a_fault_when_qdrant_answers(self):
        qdrant, docker = self.call({"status": "up", "points": 197},
                                   {"status": "down", "detail": "container not running"})
        self.assertEqual(docker["status"], "unused")
        self.assertEqual(qdrant["transport"], "native")

    def test_it_says_why_the_container_is_not_needed(self):
        _, docker = self.call({"status": "up"}, {"status": "down", "detail": "container not running"})
        self.assertIn("natively", docker["detail"])

    def test_a_running_container_is_reported_as_the_transport(self):
        qdrant, docker = self.call({"status": "up"}, {"status": "up", "detail": "Up 2 hours"})
        self.assertEqual(qdrant["transport"], "docker")
        self.assertEqual(docker["status"], "up")
        self.assertEqual(docker["detail"], "Up 2 hours")

    def test_a_down_qdrant_keeps_the_container_diagnostic(self):
        """With no vector store answering, the container's real state is the
        first thing worth knowing — that is the one time it belongs in red."""
        qdrant, docker = self.call({"status": "down", "error": "refused"},
                                   {"status": "down", "detail": "container not running"})
        self.assertEqual(docker["status"], "down")
        self.assertNotIn("transport", qdrant)

    def test_an_unknown_docker_stays_unknown_when_qdrant_is_down(self):
        _, docker = self.call({"status": "down"}, {"status": "unknown", "error": "no docker"})
        self.assertEqual(docker["status"], "unknown")

    def test_docker_missing_entirely_is_not_an_error_when_qdrant_answers(self):
        """No Docker installed at all is the expected state on a native setup."""
        qdrant, docker = self.call({"status": "up"}, {"status": "unknown", "error": "not found"})
        self.assertEqual(docker["status"], "unused")
        self.assertEqual(qdrant["transport"], "native")


class TestManualPhaseWordingInThePanel(ArchMarkupFixture):
    """The panel's own detail view has always had the advance control, but in a
    gate phase it called it "Mark Phase Complete" while the architecture page
    called the same action "Approve & continue". Somebody looking for a way to
    approve did not recognise either the button or the ⚠️ badge next to it."""

    def _actions(self):
        block = self.html[self.html.index('<div class="arch-actions">'):]
        return block[:block.index("</div>") + 6]

    def test_the_advance_button_says_approve_in_a_manual_phase(self):
        self.assertIn("✓ Approve &amp; continue", self._actions())

    def test_the_back_button_says_send_back_in_a_manual_phase(self):
        self.assertIn("← Send back", self._actions())

    def test_the_run_button_offers_the_brief_in_a_manual_phase(self):
        self.assertIn("▶ Prepare approval", self._actions())

    def test_every_gate_decision_reads_the_same_manual_flag(self):
        """One source of truth for "is this a gate" — controls reading their own
        conditions is how the two screens drifted apart in the first place."""
        self.assertGreaterEqual(self._actions().count("manualPhase"), 3)

    def test_the_badge_says_where_the_decision_is_made(self):
        self.assertIn("approve or send it back here", self.html)


class TestApprovalIsAsked(ArchMarkupFixture):
    """A permanent Approve button trains the click. The gate controls exist only
    while a gate is holding, and approving asks before it moves anything."""

    def _actions(self):
        block = self.html[self.html.index('<div class="arch-actions">'):]
        return block[:block.index("</div>") + 6]

    def test_the_gate_controls_are_absent_outside_a_manual_phase(self):
        """Not relabelled — not rendered. An ordinary phase advances itself."""
        self.assertIn("${manualPhase ? `", self._actions())
        self.assertIn('id="phase-next"', self._actions())

    def test_an_ordinary_phase_keeps_no_advance_button(self):
        block = self._actions()
        self.assertNotIn("Mark Phase Complete →", block)

    def test_update_phase_survives_as_the_way_to_move_by_hand(self):
        self.assertIn('id="phase-update"', self._actions())

    def _approve_fn(self):
        start = self.html.index("function openApproveModal(")
        return self.html[start:self.html.index("\n}\n", start) + 3]

    def test_approving_opens_a_dialog_instead_of_advancing(self):
        block = self._approve_fn()
        self.assertIn("openModal(", block)
        self.assertIn("approve-gate", block)

    def test_the_dialog_names_the_phase_being_approved(self):
        self.assertIn("${esc(phase)}", self._approve_fn())

    def test_the_dialog_reports_unfinished_work_before_you_say_yes(self):
        """Approving over open tasks is legal and sometimes right — but never by
        accident, so the count is on the dialog."""
        block = self._approve_fn()
        self.assertIn("open", block)
        self.assertIn("still open", block)

    def test_the_button_is_wired_to_the_dialog_not_to_the_move(self):
        self.assertIn("$('#phase-next').addEventListener('click', () => openApproveModal(", self.html)

    def test_only_the_dialog_advances_the_phase(self):
        self.assertIn("modalState.type === 'approve-gate'", self.html)

    def test_the_confirm_button_says_what_it_does(self):
        """"Save" on a dialog that approves somebody's work says nothing about
        what the click commits to."""
        self.assertIn("$('#modal-save').textContent = 'Approve & continue'",
                      self._approve_fn())

    def test_the_dialog_points_at_the_way_to_reject(self):
        self.assertIn("← Send back", self._approve_fn())


class TestHealthTileMarkup(ArchMarkupFixture):

    def _tiles(self):
        block = self.html[self.html.index("const tiles = ["):]
        return block[:block.index("];")]

    def test_the_docker_tile_reads_not_in_use_rather_than_stopped(self):
        self.assertIn("NOT IN USE", self._tiles())

    def test_an_unused_container_is_not_painted_as_a_fault(self):
        self.assertIn("(s === 'up' || s === 'unused') ? 'ok'", self.html)

    def test_the_qdrant_tile_says_how_it_is_served(self):
        self.assertIn("h.qdrant.transport", self._tiles())


if __name__ == "__main__":
    unittest.main()
