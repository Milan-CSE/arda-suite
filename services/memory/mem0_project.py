"""Which project a session belongs to — the same answer on every machine.

The old rule was "first path component under ~/Projeler, else personal". That is
a statement about one Mac's directory layout: a worker checking the same repo out
to D:\\work\\repo or /home/y/src/repo filed everything under `personal`, quietly
turning the shared tier into a dumping ground. Identity has to come from the repo,
not from where someone happened to clone it.

Resolution order, first hit wins:

  1. $MEM0_PROJECT_ID          explicit override, always wins
  2. git remote repo name      same answer for every clone of the repo
  3. git toplevel basename     repos with no remote
  4. ~/Projeler/<name>         legacy layout, for non-git directories there
  5. "personal"                not in a project at all

Why the id is the bare repo name and not host/owner/repo: the scope stays
consistent when a repository moves between hosts. The cost is that two owners'
repositories with the same name collide — $MEM0_PROJECT_ID exists for that, and
test_mem0_project.py pins the intended behavior.
"""

import os
import subprocess

SHARED = "personal"
LEGACY_ROOT = os.path.expanduser("~/Projeler")

# Hooks run on SessionStart/Stop; a slow answer here is felt on every turn.
GIT_TIMEOUT = 3


def _git(cwd, *args):
    """Read-only git query. Returns "" for anything that is not a clean answer —
    not a repo, no git, timeout, error."""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except Exception:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def repo_name_from_url(url):
    """The repo's name from any remote URL spelling, or "".

    All of these are the same project and must produce the same id:
        git@github.com:user/repo.git
        https://github.com/user/repo.git
        https://github.com/user/repo
        ssh://git@github.com/user/repo
    Parsed as a URL, never with os.path — a Windows client must not have its
    backslashes treated as separators here, and a POSIX one must not treat a
    stray backslash in a URL as one either.
    """
    u = (url or "").strip()
    if not u:
        return ""
    u = u.rstrip("/")
    # scp-style (git@host:owner/repo) has no scheme; both forms end in the path.
    if "://" in u:
        u = u.split("://", 1)[1]
    if "@" in u.split("/", 1)[0]:
        u = u.split("@", 1)[1]
    # Now host[:port]/owner/repo or host:owner/repo — split on both separators.
    tail = u.replace(":", "/").split("/")
    tail = [p for p in tail if p]
    if not tail:
        return ""
    name = tail[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def _legacy_scope(cwd):
    """The pre-existing rule, kept for non-git directories under ~/Projeler."""
    if cwd and cwd.startswith(LEGACY_ROOT + os.sep):
        first = cwd[len(LEGACY_ROOT) + 1:].split(os.sep)[0]
        if first:
            return first
    return ""


def project_id(cwd=None, env=None):
    """Scope id for a session running in `cwd`."""
    env = os.environ if env is None else env
    explicit = (env.get("MEM0_PROJECT_ID") or "").strip()
    if explicit:
        return explicit

    cwd = cwd or os.getcwd()

    # A worktree's directory basename differs from the main checkout's, but they
    # share a remote — so the remote is what makes worktrees agree.
    name = repo_name_from_url(_git(cwd, "remote", "get-url", "origin"))
    if name:
        return name

    top = _git(cwd, "rev-parse", "--show-toplevel")
    if top:
        # Basename without os.path: the toplevel git prints is always
        # forward-slashed, including on Windows.
        base = top.replace("\\", "/").rstrip("/").split("/")[-1]
        if base:
            return base

    return _legacy_scope(cwd) or SHARED
