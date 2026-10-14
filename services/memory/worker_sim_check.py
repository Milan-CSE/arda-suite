#!/usr/bin/env python
"""Simulate a remote worker on this machine and check it behaves like one.

A worker is defined by what it does *not* have: no Qdrant, no Ollama, no mem0,
no idea where any of those live. Testing that on the brain machine is awkward
because all of them are right there and would be used by accident — so the
checks below actively poison the local addresses. If a step still passes with
Qdrant pointed at a black hole, it passed without touching Qdrant.

    ./bin/mem0-python worker_sim_check.py

Needs the HTTP MCP server running. Writes only to a disposable scope, and
deletes what it wrote on the way out.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BRAIN_URL = os.environ.get("MEM0_BRAIN_URL", "http://127.0.0.1:8812/mcp")

# The simulated checkout's remote decides the scope, so point it at a name that
# is obviously disposable: the project id becomes __mcp_verify__.
VERIFY_SCOPE = "__mcp_verify__"
FAKE_REMOTE = "https://github.com/testorg/%s.git" % VERIFY_SCOPE

# Anything pointed here hangs rather than connects — the point is that a correct
# remote path never dials it.
BLACKHOLE = "10.255.255.1"


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok)))
        print("  %-46s %s  %s" % (name, "PASS" if ok else "FAIL", detail))

    @property
    def failed(self):
        return [r for r in self.rows if not r[1]]


def make_worker_checkout(tmp):
    """A git repo that looks like the same project cloned on another machine:
    outside ~/Projeler, and in a directory whose name is not the project's."""
    path = os.path.join(tmp, "some-unrelated-folder-name")
    os.makedirs(path)
    run = lambda *a: subprocess.run(a, cwd=path, capture_output=True)
    run("git", "init", "-q")
    run("git", "remote", "add", "origin", FAKE_REMOTE)
    return path


def worker_env(cwd, token_override=None, brain_url=None):
    """The environment a worker actually has: remote backend, brain URL, token —
    and deliberately broken Qdrant/Ollama addresses."""
    env = dict(os.environ)
    env.update({
        "MEM0_BACKEND": "remote",
        "MEM0_BRAIN_URL": brain_url or BRAIN_URL,
        "MEM0_QDRANT_HOST": BLACKHOLE,      # any direct vector-store use fails
        "MEM0_QDRANT_PORT": "6333",
    })
    env.pop("MEM0_PROJECT_ID", None)
    if token_override is not None:
        env["MEM0_MCP_TOKEN"] = token_override
    return env


def child(code, env, cwd=None, timeout=90):
    """Run a snippet in a fresh interpreter with the worker's environment, so
    nothing this process already imported can mask a missing dependency."""
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=cwd,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


PRELUDE = "import sys; sys.path.insert(0, %r)\n" % HERE


def cleanup():
    from config import get_memory
    try:
        m = get_memory()
        rows = m.get_all(filters={"user_id": os.environ.get("MEM0_OWNER", "default"),
                                  "run_id": VERIFY_SCOPE}, top_k=1000)["results"]
        for r in rows:
            m.delete(r["id"])
        return len(rows)
    except Exception as ex:
        print("  cleanup FAILED (%s) — remove scope %r by hand" % (ex, VERIFY_SCOPE))
        return -1


def main():
    rep = Report()
    marker = uuid.uuid4().hex[:12]
    print("== worker_sim_check (marker %s) ==" % marker)
    tmp = tempfile.mkdtemp(prefix="worker-sim-")
    try:
        repo = make_worker_checkout(tmp)
        env = worker_env(repo)

        # A. identity comes from the repo, not the path
        rc, out, err = child(PRELUDE + "import mem0_project as p;"
                             "print(p.project_id())", env, cwd=repo)
        rep.check("A. project id from repo, not path", out == VERIFY_SCOPE,
                  "-> %r" % (out or err[:60]))

        rep.check("A2. not under ~/Projeler",
                  not repo.startswith(os.path.expanduser("~/Projeler")), repo)

        # B. a worker outside any known layout must not land in the shared tier
        rep.check("B. does not leak into personal", out != "personal", "-> %r" % out)

        # C. write over HTTP, with the vector store unreachable
        text = ("worker_sim kanarya %s: remote backend uzerinden yazildi, "
                "dogrulama portu 8812" % marker)
        rc, out, err = child(
            PRELUDE + "import mem0_backend as b;"
            "r = b.write(%r, %r, 'worker-sim');"
            "print('RESULT', 'None' if r is None else len(r))" % (text, VERIFY_SCOPE),
            env, cwd=repo)
        # Non-None is the whole assertion: it means the brain accepted and
        # consumed the text. How many facts extraction chose to keep (often 0
        # for a canary sentence) is the extractor's call, not a transport fault —
        # and the caller's cursor logic already treats [] and None differently.
        wrote = out.startswith("RESULT") and out.split()[1] != "None"
        rep.check("C. hook write path reaches the brain", wrote,
                  "%s (0 fact = cikarici sakladi, yine de tuketildi)" % out
                  if out else err[-80:])

        # D. round-trip over HTTP, still with Qdrant poisoned.
        #
        # Deliberately NOT reusing C's write. What C sends goes through the
        # brain's LLM extraction, which is allowed to paraphrase it, drop the
        # marker, or decide the sentence is not durable and store nothing at all
        # — all correct behaviour, all fatal to a round-trip assertion. Observed
        # both: a dropped UUID, and a run where extraction kept zero facts.
        #
        # So the read path gets a fixture it controls: verbatim=True stores the
        # text exactly, over the same client and the same socket.
        rc, out, err = child(
            PRELUDE + "import mem0_backend as b;"
            "c = b._remote_client();"
            "c.call('add_memory', {'text': 'worker_sim roundtrip %s', 'scope': %r,"
            " 'agent': 'worker-sim', 'verbatim': True});"
            "rows = b.rows();"
            "print(sum(1 for r in rows if %r in (r['memory'] or '')))"
            % (marker, VERIFY_SCOPE, marker),
            env, cwd=repo)
        rep.check("D. write+read round-trip over HTTP",
                  out.isdigit() and int(out) >= 1,
                  "marker eslesmesi: %s" % (out or err[-80:]))

        # E. proven by C and D having run with MEM0_QDRANT_HOST at a black hole.
        rep.check("E. never dials Qdrant directly", wrote and out.isdigit(),
                  "MEM0_QDRANT_HOST=%s boyunca calisti" % BLACKHOLE)

        # F. and without importing the AI stack at all
        rc, out, err = child(
            PRELUDE + "import mem0_backend as b;"
            "b.write('worker_sim ikinci kanarya %s', %r, 'worker-sim');"
            "import sys;"
            "print(','.join(sorted(m for m in ('mem0','ollama','qdrant_client','config')"
            " if m in sys.modules)) or 'NONE')" % (marker, VERIFY_SCOPE),
            env, cwd=repo)
        rep.check("F. no mem0/ollama/qdrant imported on the worker",
                  out == "NONE", "yuklenen: %s" % (out or err[-60:]))

        # G. a bad token must fail closed, and must not raise into the hook
        rc, out, err = child(
            PRELUDE + "import mem0_backend as b;"
            "print('RESULT', b.write('x'*400, %r, 'worker-sim'))" % VERIFY_SCOPE,
            worker_env(repo, token_override="b" * 64), cwd=repo)
        rep.check("G. wrong token -> None (fail-safe)", out == "RESULT None",
                  out or err[-60:])

        # H. an unreachable brain must not hold up a session start
        env_down = worker_env(repo, brain_url="http://127.0.0.1:9/mcp")
        rc, out, err = child(
            PRELUDE + "import json,io,sys;"
            "sys.stdin = io.StringIO(json.dumps({'cwd':%r,'session_id':'sim',"
            "'source':'startup'}));"
            "import recall_inject as ri; print('EXIT', ri.main())" % repo,
            env_down, cwd=repo, timeout=30)
        rep.check("H. brain down -> recall exits 0, silently",
                  out.endswith("EXIT 0") and rc == 0, out or err[-60:])

        rc, out, err = child(
            PRELUDE + "import mem0_backend as b;"
            "print('RESULT', b.write('y'*400, %r, 'worker-sim'))" % VERIFY_SCOPE,
            env_down, cwd=repo, timeout=30)
        rep.check("H2. brain down -> write returns None, no raise",
                  out == "RESULT None", out or err[-60:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print("cleanup: %s satir silindi" % cleanup())

    if rep.failed:
        print("FAIL: %d check" % len(rep.failed))
        return 1
    print("OK: %d check" % len(rep.rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
