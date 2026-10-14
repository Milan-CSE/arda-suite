#!/usr/bin/env python3
"""Unit tests for the Code Map tab. Stdlib unittest only — pytest is not installed.

Run: .venv/bin/python -m unittest dashboard.test_codemap -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402


def write_graph(repo, nodes=None, links=None, built_at_commit=None, raw=None):
    """Write graphify-out/graph.json. `raw` writes a literal string instead."""
    out = repo / "graphify-out"
    out.mkdir(parents=True, exist_ok=True)
    target = out / "graph.json"
    if raw is not None:
        target.write_text(raw, encoding="utf-8")
        return target
    doc = {
        "directed": False,
        # Mirrors the real graphify shape: the LLM-assigned cluster name lives on
        # every node as `community_name`, not in .graphify_analysis.json.
        "nodes": nodes if nodes is not None else [
            {"id": "a", "label": "alpha.py", "community": 0,
             "community_name": "Core", "source_file": "alpha.py", "source_location": "L1"},
            {"id": "b", "label": "beta()", "community": 0,
             "community_name": "Core", "source_file": "alpha.py", "source_location": "L10"},
            {"id": "c", "label": "gamma()", "community": 1,
             "community_name": "Edges", "source_file": "gamma.py", "source_location": "L3"},
        ],
        "links": links if links is not None else [
            {"source": "a", "target": "b", "relation": "contains",
             "confidence": "EXTRACTED"},
            {"source": "a", "target": "c", "relation": "imports",
             "confidence": "EXTRACTED"},
        ],
    }
    if built_at_commit:
        doc["built_at_commit"] = built_at_commit
    target.write_text(json.dumps(doc), encoding="utf-8")
    return target


def write_analysis(repo, gods=None, communities=None, surprises=None, raw=None):
    out = repo / "graphify-out"
    out.mkdir(parents=True, exist_ok=True)
    target = out / ".graphify_analysis.json"
    if raw is not None:
        target.write_text(raw, encoding="utf-8")
        return target
    doc = {
        # Real graphify gods carry {id, label, degree} only — no location. The
        # server joins them to the graph nodes by id to recover source_file.
        "gods": gods if gods is not None else [
            {"id": "c", "label": "gamma()", "degree": 1},
            {"id": "a", "label": "alpha.py", "degree": 2},
        ],
        "communities": communities if communities is not None else [
            {"id": 0, "name": "core"},
            {"id": 1, "name": "edges"},
        ],
        "surprises": surprises if surprises is not None else [
            {"source": "alpha.py", "target": "gamma()", "why": "cross-module"},
        ],
        "cohesion": {},
        "questions": [],
    }
    target.write_text(json.dumps(doc), encoding="utf-8")
    return target


class RootsFixture(unittest.TestCase):
    """Points PROJECTS_ROOT and EXTRA_REPOS at temp dirs for the duration."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.projects_root = base / "Projeler"
        self.projects_root.mkdir()
        self.extra = base / "mem0-local"
        self.extra.mkdir()
        self._orig_projects = server.PROJECTS_ROOT
        self._orig_extra = server.EXTRA_REPOS
        server.PROJECTS_ROOT = self.projects_root
        server.EXTRA_REPOS = (self.extra,)
        server._codemap_cache.clear()
        server._projects_cache.update(ts=0.0, data=None)

    def tearDown(self):
        server.PROJECTS_ROOT = self._orig_projects
        server.EXTRA_REPOS = self._orig_extra
        server._codemap_cache.clear()
        server._projects_cache.update(ts=0.0, data=None)
        self._tmp.cleanup()

    def make_repo(self, name, under_projects=True):
        repo = (self.projects_root / name) if under_projects else (Path(self._tmp.name) / name)
        repo.mkdir(parents=True, exist_ok=True)
        return repo.resolve()


class TestRepoDiscovery(RootsFixture):

    def test_lists_projects_root_children(self):
        a = self.make_repo("alpha")
        b = self.make_repo("beta")
        found = server.iter_repo_candidates()
        self.assertIn(a, found)
        self.assertIn(b, found)

    def test_skips_dot_directories(self):
        (self.projects_root / ".hidden").mkdir()
        names = [p.name for p in server.iter_repo_candidates()]
        self.assertNotIn(".hidden", names)

    def test_includes_extra_repos_outside_projects_root(self):
        found = server.iter_repo_candidates()
        self.assertIn(self.extra.resolve(), found)

    def test_deduplicates_repo_reachable_twice(self):
        link = self.projects_root / "mem0-link"
        link.symlink_to(self.extra, target_is_directory=True)
        found = server.iter_repo_candidates()
        self.assertEqual(found.count(self.extra.resolve()), 1)

    def test_survives_missing_projects_root(self):
        server.PROJECTS_ROOT = Path(self._tmp.name) / "does-not-exist"
        found = server.iter_repo_candidates()
        self.assertEqual(found, [self.extra.resolve()])


class TestResolveRepo(RootsFixture):

    def test_accepts_repo_under_projects_root(self):
        repo = self.make_repo("alpha")
        self.assertEqual(server._resolve_repo(str(repo)), repo)

    def test_accepts_extra_repo(self):
        self.assertEqual(server._resolve_repo(str(self.extra)), self.extra.resolve())

    def test_rejects_path_outside_allowed_roots(self):
        with self.assertRaises(PermissionError):
            server._resolve_repo("/etc")

    def test_rejects_symlink_escaping_allowed_root(self):
        outside = Path(self._tmp.name) / "outside-repo"
        outside.mkdir()
        escape = self.projects_root / "escape"
        escape.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(PermissionError):
            server._resolve_repo(str(escape))

    def test_rejects_subdirectory_of_a_repo(self):
        repo = self.make_repo("alpha")
        (repo / "src").mkdir()
        with self.assertRaises(PermissionError):
            server._resolve_repo(str(repo / "src"))

    def test_rejects_missing_path(self):
        with self.assertRaises(FileNotFoundError):
            server._resolve_repo(str(self.projects_root / "nope"))

    def test_rejects_file_instead_of_directory(self):
        repo = self.make_repo("alpha")
        f = repo / "file.txt"
        f.write_text("x", encoding="utf-8")
        with self.assertRaises((ValueError, PermissionError)):
            server._resolve_repo(str(f))

    def test_rejects_empty_path(self):
        with self.assertRaises(ValueError):
            server._resolve_repo("")


def git_init(repo):
    """Make `repo` a git repo with deterministic identity. Returns nothing."""
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"],
                   check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e"],
                   check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"],
                   check=True, env=env)


def git_commit(repo, filename, text):
    """Commit one file, return the full SHA."""
    (repo / filename).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "c " + filename],
                   check=True)
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


class TestFreshness(RootsFixture):

    def test_unknown_when_not_a_git_repo(self):
        repo = self.make_repo("plain")
        self.assertEqual(server._codemap_freshness(repo, "abc1234")["state"], "unknown")

    def test_unknown_when_no_built_commit(self):
        repo = self.make_repo("g")
        git_init(repo)
        git_commit(repo, "a.txt", "1")
        self.assertEqual(server._codemap_freshness(repo, None)["state"], "unknown")

    def test_unknown_when_sha_is_not_hex(self):
        repo = self.make_repo("g")
        git_init(repo)
        git_commit(repo, "a.txt", "1")
        got = server._codemap_freshness(repo, "; rm -rf /")
        self.assertEqual(got["state"], "unknown")

    def test_unknown_when_sha_has_trailing_newline(self):
        """Python's `$` in a regex allows one trailing newline — `\\Z` does not.
        A built_at_commit of "abc1234\\n" must be rejected at validation
        (state "unknown"), not passed through to git and rejected there
        (state "unknown_commit")."""
        repo = self.make_repo("g")
        git_init(repo)
        git_commit(repo, "a.txt", "1")
        got = server._codemap_freshness(repo, "abc1234\n")
        self.assertEqual(got["state"], "unknown")

    def test_unknown_commit_when_sha_absent_from_repo(self):
        repo = self.make_repo("g")
        git_init(repo)
        git_commit(repo, "a.txt", "1")
        got = server._codemap_freshness(repo, "0" * 40)
        self.assertEqual(got["state"], "unknown_commit")

    def test_fresh_when_built_equals_head(self):
        repo = self.make_repo("g")
        git_init(repo)
        sha = git_commit(repo, "a.txt", "1")
        got = server._codemap_freshness(repo, sha)
        self.assertEqual(got["state"], "fresh")
        self.assertNotIn("behind", got)

    def test_stale_reports_commit_count(self):
        repo = self.make_repo("g")
        git_init(repo)
        first = git_commit(repo, "a.txt", "1")
        git_commit(repo, "b.txt", "2")
        git_commit(repo, "c.txt", "3")
        got = server._codemap_freshness(repo, first)
        self.assertEqual(got["state"], "stale")
        self.assertEqual(got["behind"], 2)

    def test_diverged_carries_no_commit_count(self):
        repo = self.make_repo("g")
        git_init(repo)
        base = git_commit(repo, "a.txt", "1")
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
        side = git_commit(repo, "side.txt", "s")
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
        git_commit(repo, "main.txt", "m")
        got = server._codemap_freshness(repo, side)
        self.assertEqual(got["state"], "diverged")
        self.assertNotIn("behind", got)
        self.assertNotEqual(base, side)


class TestReadCodemap(RootsFixture):

    def test_missing_graph_reports_build_command(self):
        repo = self.make_repo("alpha")
        got = server.read_codemap(str(repo))
        self.assertTrue(got["missing"])
        self.assertEqual(got["cmd"], "graphify extract . --code-only")

    def test_happy_path_counts_and_analysis_source(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo)
        got = server.read_codemap(str(repo))
        self.assertEqual(got["counts"], {"nodes": 3, "links": 2, "communities": 2})
        self.assertEqual(got["gods_source"], "analysis")
        self.assertTrue(got["analysis"])
        self.assertIsNone(got["analysis_warning"])

    def test_god_order_follows_analysis_file_not_degree(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo)  # gamma() first, though alpha.py has the higher degree
        got = server.read_codemap(str(repo))
        self.assertEqual([g["label"] for g in got["gods"]], ["gamma()", "alpha.py"])

    def test_missing_analysis_falls_back_to_degree(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        got = server.read_codemap(str(repo))
        self.assertFalse(got["analysis"])
        self.assertIsNotNone(got["analysis_warning"])
        self.assertEqual(got["gods_source"], "degree")
        self.assertEqual(got["gods"][0]["label"], "alpha.py")  # degree 2
        self.assertEqual(got["surprises"], [])

    def test_malformed_graph_json_is_controlled(self):
        repo = self.make_repo("alpha")
        write_graph(repo, raw='{"nodes": [')
        got = server.read_codemap(str(repo))
        self.assertTrue(got["invalid"])
        self.assertIn("graph.json", got["error"])

    def test_malformed_analysis_json_is_controlled(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo, raw="{oops")
        got = server.read_codemap(str(repo))
        self.assertTrue(got["invalid"])

    def test_invalid_shape_is_rejected(self):
        repo = self.make_repo("alpha")
        write_graph(repo, raw=json.dumps({"nodes": {"a": 1}, "links": []}))
        got = server.read_codemap(str(repo))
        self.assertTrue(got["invalid"])

    def test_cache_invalidates_when_analysis_changes(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo, gods=[{"label": "gamma()", "degree": 1}])
        first = server.read_codemap(str(repo))
        self.assertEqual([g["label"] for g in first["gods"]], ["gamma()"])
        write_analysis(repo, gods=[{"label": "alpha.py", "degree": 2},
                                   {"label": "gamma()", "degree": 1}])
        second = server.read_codemap(str(repo))
        self.assertEqual([g["label"] for g in second["gods"]], ["alpha.py", "gamma()"])

    def test_cache_invalidates_on_new_commit(self):
        repo = self.make_repo("alpha")
        git_init(repo)
        sha = git_commit(repo, "a.txt", "1")
        write_graph(repo, built_at_commit=sha)
        self.assertEqual(server.read_codemap(str(repo))["freshness"]["state"], "fresh")
        git_commit(repo, "b.txt", "2")
        self.assertEqual(server.read_codemap(str(repo))["freshness"]["state"], "stale")

    def test_rejects_repo_outside_roots(self):
        with self.assertRaises(PermissionError):
            server.read_codemap("/etc")

    def test_unhashable_node_community_is_rejected(self):
        repo = self.make_repo("alpha")
        write_graph(repo, nodes=[
            {"id": "a", "label": "alpha.py", "community": ["x"]},
        ], links=[])
        got = server.read_codemap(str(repo))
        self.assertTrue(got["invalid"])

    def test_unhashable_node_id_is_rejected(self):
        repo = self.make_repo("alpha")
        write_graph(repo, nodes=[
            {"id": {"nested": True}, "label": "alpha.py", "community": 0},
        ], links=[])
        got = server.read_codemap(str(repo))
        self.assertTrue(got["invalid"])

    def test_unhashable_link_source_is_rejected(self):
        repo = self.make_repo("alpha")
        write_graph(repo, nodes=[
            {"id": "a", "label": "alpha.py", "community": 0},
            {"id": "b", "label": "beta()", "community": 0},
        ], links=[{"source": ["a"], "target": "b"}])
        got = server.read_codemap(str(repo))
        self.assertTrue(got["invalid"])

    def test_null_entries_in_analysis_lists_are_filtered(self):
        """graphify-out/*.json is untrusted, externally-written input. A `null`
        entry in `gods` or `surprises` must be dropped, not passed through as
        `None` to the client (where `esc(g.label)` would throw)."""
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo, raw=json.dumps({
            "gods": [None],
            "surprises": [None, {"source": "a", "target": "b"}],
        }))
        got = server.read_codemap(str(repo))
        self.assertNotIn("invalid", got)
        # gods was [null] only -> filtered to [] -> falls back to degree ranking
        self.assertEqual(got["gods_source"], "degree")
        self.assertTrue(all(isinstance(g, dict) for g in got["gods"]))
        self.assertEqual(got["surprises"], [{"source": "a", "target": "b"}])

    def test_none_and_int_community_values_still_valid(self):
        repo = self.make_repo("alpha")
        write_graph(repo, nodes=[
            {"id": "a", "label": "alpha.py", "community": None},
            {"id": "b", "label": "beta()", "community": 2},
        ], links=[{"source": "a", "target": "b"}])
        got = server.read_codemap(str(repo))
        self.assertNotIn("invalid", got)


class TestProjectsIsolation(RootsFixture):
    """The Projects tab shares repo discovery with the Code Map tab, but must
    keep listing only PROJECTS_ROOT children — EXTRA_REPOS (mem0-local) is a
    Code Map-only concept and must never appear as a Projects card, nor be
    reachable through the project-detail endpoint."""

    def test_include_extra_false_excludes_extra_repos(self):
        found_default = server.iter_repo_candidates()
        found_restricted = server.iter_repo_candidates(include_extra=False)
        self.assertIn(self.extra.resolve(), found_default)
        self.assertNotIn(self.extra.resolve(), found_restricted)

    def test_extra_repo_in_codemaps_not_in_projects(self):
        write_graph(self.extra)
        codemap_names = [r["name"] for r in server.read_codemaps()["repos"]]
        project_names = [p["name"] for p in server.read_projects()["projects"]]
        self.assertIn(self.extra.name, codemap_names)
        self.assertNotIn(self.extra.name, project_names)

    def test_resolve_repo_still_accepts_extra_repo(self):
        # The Code Map path must keep working even though Projects excludes it.
        self.assertEqual(server._resolve_repo(str(self.extra)), self.extra.resolve())

    def test_project_detail_accepts_plain_repo_under_projects_root(self):
        repo = self.make_repo("alpha")
        got = server.read_project_detail(str(repo))
        self.assertEqual(got["name"], "alpha")
        self.assertEqual(got["path"], str(repo))

    def test_project_detail_rejects_extra_repo(self):
        with self.assertRaises(PermissionError):
            server.read_project_detail(str(self.extra))


class TestCodemapsPicker(RootsFixture):

    def test_lists_repos_with_and_without_graphs(self):
        withg = self.make_repo("has-graph")
        self.make_repo("no-graph")
        write_graph(withg)
        got = server.read_codemaps()
        by_name = {r["name"]: r for r in got["repos"]}
        self.assertTrue(by_name["has-graph"]["has_graph"])
        self.assertFalse(by_name["no-graph"]["has_graph"])

    def test_extra_repo_is_discoverable_in_picker(self):
        write_graph(self.extra)
        names = [r["name"] for r in server.read_codemaps()["repos"]]
        self.assertIn("mem0-local", names)

    def test_repos_with_graphs_sort_first(self):
        self.make_repo("aaa-no-graph")
        withg = self.make_repo("zzz-has-graph")
        write_graph(withg)
        repos = server.read_codemaps()["repos"]
        self.assertEqual(repos[0]["name"], "zzz-has-graph")

    def test_picker_does_not_parse_graph_files(self):
        broken = self.make_repo("broken")
        write_graph(broken, raw="{not json")
        got = server.read_codemaps()  # must not raise
        by_name = {r["name"]: r for r in got["repos"]}
        self.assertTrue(by_name["broken"]["has_graph"])
        self.assertNotIn("counts", by_name["broken"])


class TestCodemapMarkup(unittest.TestCase):
    """The view is embedded HTML; assert the wiring exists and nothing is raw."""

    def setUp(self):
        from assets import PAGE_HTML
        self.html = PAGE_HTML

    def test_nav_button_exists(self):
        self.assertIn('data-view="codemap"', self.html)

    def test_view_section_exists(self):
        self.assertIn('id="view-codemap"', self.html)

    def test_registered_in_load_dispatch(self):
        self.assertIn("codemap: loadCodemap", self.html)

    def test_render_escapes_every_graph_value(self):
        """No graph-sourced value may reach innerHTML unescaped.

        `g.source_file`/`g.source_location` (and their `m.` community-member
        equivalents) are deliberately NOT checked by literal name here: they
        never appear as a bare `${g.source_file}` template interpolation —
        they are only ever passed as arguments to `cmLoc(file, loc)`, which
        joins them into its own local `ref` variable before escaping. A
        literal-name check for `g.source_file` would be a no-op (it can never
        match, escaped or not), so `ref` is checked instead — it is the
        actual value `cmLoc` interpolates, and covers every call site
        (god nodes and community members alike). `f.state` covers `cmBadge`.
        """
        start = self.html.index("/* ---- Code Map ---- */")
        end = self.html.index("/* ---- end codemap ---- */")
        block = self.html[start:end]
        # Every name below must be a real `${esc(...)}` site in the block, or the
        # assertion is a no-op. test_escaping_field_list_is_not_a_no_op pins that.
        for field in ("g.label", "g.degree", "ref", "cl.name", "cl.size",
                      "s.source", "s.target", "n.label", "f.state",
                      "d.gods_source", "d.analysis_warning"):
            bare = "${" + field + "}"
            # assertFalse, not assertNotIn: assertNotIn dumps the whole block
            # (20KB of JS) into the failure message and buries the point.
            self.assertFalse(bare in block,
                             "%s reaches the DOM unescaped — wrap it in esc()" % field)

    def test_escaping_field_list_is_not_a_no_op(self):
        """Guards the guard.

        The list above shipped twice with names the render code no longer used
        (`c.name` when the variable was `c2`; `c2.name`/`m.label` after the
        community accordion was replaced). A name that appears nowhere can never
        match, so the assertion passes whether or not the value is escaped. This
        test fails the moment a checked field stops being an escaped
        interpolation site.
        """
        import re
        start = self.html.index("/* ---- Code Map ---- */")
        end = self.html.index("/* ---- end codemap ---- */")
        block = self.html[start:end]
        escaped = set(re.findall(r"\$\{esc\(([^)]+)\)", block))
        for field in ("g.label", "g.degree", "ref", "cl.name", "cl.size",
                      "s.source", "s.target", "n.label", "f.state",
                      "d.gods_source", "d.analysis_warning"):
            self.assertTrue(
                any(field == e or e.startswith(field + " ") for e in escaped),
                "%s is checked for bare interpolation but is not an escaped "
                "interpolation site — the check is a no-op, update the list" % field)

    def test_render_body_is_inside_the_try_block(self):
        """A throw during rendering (e.g. `esc(null)` on a bad graph entry)
        must land in the catch and show an error state, not escape as an
        unhandled rejection that leaves the tab stuck on its spinner. That
        only holds if the try block wraps the whole render, not just the
        initial `await api(...)` fetch.

        This checks the `cm-stats` block — the main innerHTML assignment that
        does the actual rendering — sits textually between `try {` and the
        function's `} catch (e) {`. If the try/catch were narrowed back to
        wrapping only the fetch, `cm-stats` would fall after the catch and
        this assertion would fail.
        """
        start = self.html.index("async function renderCodemap")
        end = self.html.index("function wireRefs", start)
        block = self.html[start:end]
        try_idx = block.index("try {")
        catch_idx = block.index("} catch (e) {", try_idx)
        stats_idx = block.index('<div class="cm-stats">')
        self.assertTrue(
            try_idx < stats_idx < catch_idx,
            "the full render body (cm-stats innerHTML assignment) must be "
            "inside renderCodemap's try block, not after it",
        )


class TestClusterNamesAndGodJoin(RootsFixture):
    """The shape assumptions that shipped wrong the first time.

    The original spec assumed `.graphify_analysis.json["communities"]` was a list
    of {id, name}. It is a dict of id -> member ids and carries no names, so every
    cluster rendered as "Community N" while the real names sat unread on the
    nodes. The fixtures encoded the same wrong assumption, so the tests agreed
    with the bug. These tests pin the real shape.
    """

    def test_cluster_names_come_from_node_community_name(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo)
        got = server.read_codemap(str(repo))
        names = sorted(c["name"] for c in got["communities"])
        self.assertEqual(names, ["Core", "Edges"])
        self.assertTrue(all(c["named"] for c in got["communities"]))

    def test_analysis_communities_dict_does_not_break_naming(self):
        """Real graphify writes communities as a dict, not a list."""
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo, communities={"0": ["a", "b"], "1": ["c"]})
        got = server.read_codemap(str(repo))
        self.assertEqual(sorted(c["name"] for c in got["communities"]), ["Core", "Edges"])
        self.assertIsNone(got["analysis_warning"])

    def test_unnamed_clusters_warn_with_the_label_command(self):
        repo = self.make_repo("alpha")
        write_graph(repo, nodes=[
            {"id": "a", "label": "alpha.py", "community": 0,
             "source_file": "alpha.py", "source_location": "L1"},
        ], links=[])
        write_analysis(repo, gods=[])
        got = server.read_codemap(str(repo))
        self.assertEqual(got["communities"][0]["name"], "Community 0")
        self.assertFalse(got["communities"][0]["named"])
        self.assertIn("graphify label", got["analysis_warning"])

    def test_placeholder_community_name_counts_as_unnamed(self):
        """graphify writes 'Community N' as its own placeholder — not a real name."""
        repo = self.make_repo("alpha")
        write_graph(repo, nodes=[
            {"id": "a", "label": "alpha.py", "community": 3,
             "community_name": "Community 3", "source_file": "alpha.py"},
        ], links=[])
        got = server.read_codemap(str(repo))
        self.assertFalse(got["communities"][0]["named"])

    def test_gods_gain_location_by_id_join(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo)
        gods = server.read_codemap(str(repo))["gods"]
        first = gods[0]
        self.assertEqual(first["label"], "gamma()")          # analysis order preserved
        self.assertEqual(first["source_file"], "gamma.py")   # recovered from the node
        self.assertEqual(first["source_location"], "L3")
        self.assertEqual(first["community_name"], "Edges")

    def test_god_without_matching_node_degrades_quietly(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo, gods=[{"id": "ghost", "label": "ghost()", "degree": 3}])
        god = server.read_codemap(str(repo))["gods"][0]
        self.assertEqual(god["label"], "ghost()")
        self.assertEqual(god["source_file"], "")


class TestGraphPayload(RootsFixture):

    def test_payload_carries_drawable_nodes_and_links(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo)
        g = server.read_codemap(str(repo))["graph"]
        self.assertEqual(len(g["nodes"]), 3)
        self.assertEqual(len(g["links"]), 2)
        self.assertEqual(g["truncated"], 0)
        node = next(n for n in g["nodes"] if n["id"] == "a")
        self.assertEqual(node["degree"], 2)
        self.assertIsInstance(node["group"], int)

    def test_group_indexes_match_community_order(self):
        repo = self.make_repo("alpha")
        write_graph(repo)
        write_analysis(repo)
        d = server.read_codemap(str(repo))
        order = [c["name"] for c in d["communities"]]
        node = next(n for n in d["graph"]["nodes"] if n["id"] == "c")
        self.assertEqual(order[node["group"]], "Edges")

    def test_large_graph_is_truncated_and_links_pruned(self):
        repo = self.make_repo("alpha")
        nodes = [{"id": "n%d" % i, "label": "n%d" % i, "community": 0,
                  "community_name": "Big", "source_file": "x.py"} for i in range(700)]
        # n0 is the hub, so it survives; the link to the pruned tail must not.
        links = [{"source": "n0", "target": "n%d" % i} for i in range(1, 700)]
        write_graph(repo, nodes=nodes, links=links)
        g = server.read_codemap(str(repo))["graph"]
        self.assertEqual(len(g["nodes"]), 600)
        self.assertEqual(g["truncated"], 100)
        kept = {n["id"] for n in g["nodes"]}
        for link in g["links"]:
            self.assertIn(link["source"], kept)
            self.assertIn(link["target"], kept)


class TestRealGraphifyOutput(unittest.TestCase):
    """Reads this repo's actual graphify-out, not a fixture.

    Every other test here builds its own JSON, so a wrong assumption about
    graphify's schema gets encoded in both the code and the fixture and the
    suite agrees with itself. This one is the check against reality. It skips
    when the graph has not been generated.
    """

    def setUp(self):
        self.repo = Path(server.MEM0_ROOT)
        if not (self.repo / server.GRAPH_REL).is_file():
            self.skipTest("no graphify-out/graph.json — run `graphify extract . --code-only`")
        server._codemap_cache.clear()
        self.data = server.read_codemap(str(self.repo))

    def test_is_not_an_error_shape(self):
        self.assertNotIn("invalid", self.data)
        self.assertNotIn("missing", self.data)

    def test_clusters_are_named_not_numbered(self):
        named = [c for c in self.data["communities"] if c["named"]]
        self.assertTrue(named, "every cluster fell back to 'Community N' — "
                               "cluster naming regressed")

    def test_top_god_has_a_file_location(self):
        god = self.data["gods"][0]
        self.assertTrue(god["source_file"], "god nodes lost their file location")

    def test_graph_payload_is_drawable(self):
        g = self.data["graph"]
        self.assertTrue(g["nodes"])
        ids = {n["id"] for n in g["nodes"]}
        for link in g["links"]:
            self.assertIn(link["source"], ids)
            self.assertIn(link["target"], ids)


class TestEditorLink(unittest.TestCase):
    """Clicking a symbol hands off to a desktop editor via a URL scheme."""

    def setUp(self):
        from assets import PAGE_HTML
        self.html = PAGE_HTML
        self._saved = os.environ.get("DASHBOARD_EDITOR")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("DASHBOARD_EDITOR", None)
        else:
            os.environ["DASHBOARD_EDITOR"] = self._saved

    def test_defaults_to_vscode_when_unset(self):
        os.environ.pop("DASHBOARD_EDITOR", None)
        self.assertEqual(server._editor_url_template(), "vscode://file{path}:{line}")

    def test_env_selects_the_editor(self):
        os.environ["DASHBOARD_EDITOR"] = "cursor"
        self.assertEqual(server._editor_url_template(), "cursor://file{path}:{line}")

    def test_env_is_case_and_space_insensitive(self):
        os.environ["DASHBOARD_EDITOR"] = "  Cursor \n"
        self.assertEqual(server._editor_url_template(), "cursor://file{path}:{line}")

    def test_unknown_editor_falls_back_rather_than_raising(self):
        """A typo in a shell profile should cost the editor, not the panel."""
        os.environ["DASHBOARD_EDITOR"] = "emacsclient-typo"
        self.assertEqual(server._editor_url_template(), server.EDITOR_URLS["vscode"])

    def test_every_template_carries_both_placeholders(self):
        """cmOpen substitutes exactly these two; a template missing one would
        navigate to a URL with a literal '{line}' in it."""
        for name, tpl in server.EDITOR_URLS.items():
            self.assertIn("{path}", tpl, name)
            self.assertIn("{line}", tpl, name)

    def test_payload_carries_the_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            graph = repo / "graph.json"
            graph.write_text(json.dumps({
                "nodes": [{"id": "a", "label": "a", "community": 0,
                           "source_file": "x.py", "source_location": "L1"}],
                "links": [],
            }))
            data = server._build_codemap(repo, graph, repo / "missing.json")
        self.assertEqual(data["editor_url"], server._editor_url_template())

    def test_location_button_exposes_file_and_line_separately(self):
        """cmOpen needs the path and the location apart — a single 'file:L12'
        string would have to be re-split client-side, and a Windows-style path
        or a label containing ':' would split wrong."""
        self.assertIn('data-file="${esc(file)}"', self.html)
        self.assertIn('data-loc="${esc(loc || \'\')}"', self.html)

    def test_alt_click_still_copies(self):
        """The copy path predates this and is the only way to get the ref as
        text; opening must not have replaced it."""
        start = self.html.index("function wireRefs")
        block = self.html[start:start + 600]
        self.assertIn("e.altKey", block)
        self.assertIn("clipboard.writeText", block)

    def test_open_absolutises_against_the_repo_root(self):
        """graph.json stores repo-relative paths; an editor needs an absolute
        one, so cmOpen must join with the repo the payload reported."""
        start = self.html.index("function cmOpen")
        block = self.html[start:self.html.index("const cmLoc")]
        self.assertIn("cmRepo", block)
        self.assertIn("{path}", block)
        self.assertIn("{line}", block)


if __name__ == "__main__":
    unittest.main()
