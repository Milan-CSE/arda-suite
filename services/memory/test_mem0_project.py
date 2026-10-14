#!/usr/bin/env python3
"""Project identity: URL spellings, path shapes, and the compatibility pin.

Run: .venv/bin/python -m unittest test_mem0_project -v
"""

import os
import subprocess
import tempfile
import unittest

import mem0_project as mp

# Every project scope that currently holds memories, with the remote the working
# copy actually has. If a change to the resolver moves any of these, the memories
# under that name become unreachable — so this is a pin, not an example.
LIVE_SCOPES = [
    ("sample-calibration", "https://github.com/example-user/sample-calibration.git"),
    ("sample-assistant", "https://github.com/example-user/sample-assistant.git"),
    ("sample-panel", "https://github.com/example-user/sample-panel.git"),
    ("SampleShop", "git@github.com:example-user/SampleShop.git"),
]


class TestRepoNameFromUrl(unittest.TestCase):
    """The same repo cloned four ways is one project."""

    def test_ssh_scp_style(self):
        self.assertEqual(mp.repo_name_from_url("git@github.com:user/repo.git"), "repo")

    def test_https_with_git_suffix(self):
        self.assertEqual(
            mp.repo_name_from_url("https://github.com/user/repo.git"), "repo")

    def test_https_without_suffix(self):
        self.assertEqual(mp.repo_name_from_url("https://github.com/user/repo"), "repo")

    def test_ssh_scheme_form(self):
        self.assertEqual(
            mp.repo_name_from_url("ssh://git@github.com/user/repo.git"), "repo")

    def test_trailing_slash(self):
        self.assertEqual(mp.repo_name_from_url("https://github.com/user/repo/"), "repo")

    def test_all_spellings_agree(self):
        forms = ["git@github.com:user/repo.git",
                 "https://github.com/user/repo.git",
                 "https://github.com/user/repo",
                 "ssh://git@github.com/user/repo",
                 "https://github.com/user/repo/"]
        self.assertEqual(len({mp.repo_name_from_url(f) for f in forms}), 1)

    def test_self_hosted_host_with_port(self):
        self.assertEqual(
            mp.repo_name_from_url("ssh://git@git.example.com:2222/team/repo.git"),
            "repo")

    def test_nested_group_path(self):
        """GitLab-style subgroups: the repo is still the last component."""
        self.assertEqual(
            mp.repo_name_from_url("https://gitlab.com/group/sub/repo.git"), "repo")

    def test_dot_in_repo_name_survives(self):
        self.assertEqual(
            mp.repo_name_from_url("https://github.com/user/my.tool.git"), "my.tool")

    def test_empty_and_garbage(self):
        for bad in ("", None, "   "):
            self.assertEqual(mp.repo_name_from_url(bad), "")

    def test_windows_path_is_not_split_on_backslash(self):
        """A local Windows remote must not be chopped at backslashes by a
        POSIX-flavoured parser."""
        self.assertEqual(
            mp.repo_name_from_url("C:/Users/y/src/repo.git"), "repo")


class TestResolutionOrder(unittest.TestCase):

    def test_explicit_id_wins_over_everything(self):
        self.assertEqual(
            mp.project_id(cwd="/anywhere", env={"MEM0_PROJECT_ID": "chosen"}),
            "chosen")

    def test_blank_explicit_id_is_ignored(self):
        """An exported-but-empty variable must not mean "no project"."""
        got = mp.project_id(cwd=tempfile.gettempdir(), env={"MEM0_PROJECT_ID": "  "})
        self.assertNotEqual(got, "  ")

    def test_non_project_directory_is_personal(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mp.project_id(cwd=d, env={}), mp.SHARED)


class TestLegacyLayout(unittest.TestCase):
    """Non-git directories under ~/Projeler must keep resolving as before."""

    def test_first_component_under_projeler(self):
        self.assertEqual(
            mp._legacy_scope(os.path.join(mp.LEGACY_ROOT, "someproj")), "someproj")

    def test_nested_path_still_yields_the_project(self):
        self.assertEqual(
            mp._legacy_scope(os.path.join(mp.LEGACY_ROOT, "someproj", "src", "deep")),
            "someproj")

    def test_projeler_root_itself_is_not_a_project(self):
        self.assertEqual(mp._legacy_scope(mp.LEGACY_ROOT), "")

    def test_outside_projeler_is_not_a_project(self):
        self.assertEqual(mp._legacy_scope("/tmp/whatever"), "")


class TestRealGitRepos(unittest.TestCase):
    """Against actual git repos — the parsing above is only half the contract."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "checkout-named-differently")
        os.makedirs(self.repo)
        self._run("git", "init", "-q")
        self._run("git", "config", "user.email", "t@example.com")
        self._run("git", "config", "user.name", "t")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args, cwd=None):
        subprocess.run(args, cwd=cwd or self.repo, capture_output=True, check=False)

    def test_remote_beats_directory_name(self):
        """The point of the whole change: a clone in a differently-named folder,
        as happens on another machine, is still the same project."""
        self._run("git", "remote", "add", "origin",
                  "https://github.com/user/realname.git")
        self.assertEqual(mp.project_id(cwd=self.repo, env={}), "realname")

    def test_nested_directory_resolves_to_the_repo(self):
        self._run("git", "remote", "add", "origin",
                  "https://github.com/user/realname.git")
        deep = os.path.join(self.repo, "a", "b")
        os.makedirs(deep)
        self.assertEqual(mp.project_id(cwd=deep, env={}), "realname")

    def test_repo_without_remote_falls_back_to_toplevel(self):
        self.assertEqual(mp.project_id(cwd=self.repo, env={}),
                         "checkout-named-differently")

    def test_worktree_agrees_with_its_main_checkout(self):
        """A worktree lives in a different directory but is the same project."""
        self._run("git", "remote", "add", "origin",
                  "https://github.com/user/realname.git")
        open(os.path.join(self.repo, "f.txt"), "w").close()
        self._run("git", "add", "-A")
        self._run("git", "commit", "-qm", "init")
        wt = os.path.join(self.tmp.name, "wt-different-name")
        self._run("git", "worktree", "add", "-q", wt, "-b", "side")
        if not os.path.isdir(wt):
            self.skipTest("git worktree unavailable")
        self.assertEqual(mp.project_id(cwd=wt, env={}), "realname")


class TestBackwardCompatibility(unittest.TestCase):
    """Guards the memories already in the store."""

    def test_every_populated_scope_still_resolves_to_its_own_name(self):
        for scope, remote in LIVE_SCOPES:
            with self.subTest(scope=scope):
                self.assertEqual(mp.repo_name_from_url(remote), scope)

    def test_case_is_preserved(self):
        """`SampleShop` and `sample-panel` are distinct scopes in the store;
        lowercasing ids would merge or orphan them."""
        self.assertEqual(
            mp.repo_name_from_url("git@github.com:example-user/SampleShop.git"),
            "SampleShop")


if __name__ == "__main__":
    unittest.main()
