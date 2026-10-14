#!/usr/bin/env python3
"""Pick the memories worth putting in front of a session as it starts.

Why this exists: the mem0 pipeline writes automatically (SessionEnd autosave,
Stop checkpoint) but nothing ever read it back — across 269 recorded sessions
`search_memory` was called 8 times. Storing without recalling is an open loop,
so the store grew while no session was any wiser for it. This closes the loop
from the other end: instead of hoping the model remembers to search, hand it
the in-scope lessons unprompted.

Two constraints shape the design:

* It runs on SessionStart, so it must be cheap and it must never hang. Reading
  goes straight to Qdrant's scroll API — no mem0 client, no embedding, no LLM —
  and every failure path is silent.
* The store is known to contain conversational noise (mem0's infer=True
  extraction on raw transcripts). Injecting that unfiltered would put junk in
  every session's context, which is worse than injecting nothing, so entries
  pass the same junk filter the write path uses.

It also prints the session's scoped rules (see `rules.py`) above the memories.
The two blocks are independent on purpose: rules come from files and memories
from the store, so a store that is down costs the session its recall but not its
conventions.
"""

import hashlib
import json
import os
import re
import sys

MEM0_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MEM0_ROOT)

import rules  # noqa: E402  (needs MEM0_ROOT on the path first)
STATE_DIR = os.path.join(MEM0_ROOT, "state")
SHARED = "personal"

# A SessionStart injection is paid on every session, so the budget is small on
# purpose: enough for the traps that actually bite, not a digest of the store.
MAX_ITEMS = 10
MAX_CHARS = 1800
FETCH_TIMEOUT = 3          # seconds; a slow store must not delay the session

# SessionStart fires for several reasons and they differ in one way that matters
# here: whether the block this hook printed earlier is still in the model's
# context. On `resume` the transcript is replayed, so it is — printing it again
# just duplicates it. On `compact` it was summarised away, which is exactly when
# re-injecting earns its cost. Anything unrecognised falls through to injecting,
# because showing a lesson twice is a smaller failure than never showing it.
REPLAYS_CONTEXT = frozenset({"resume"})

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
try:
    from mem0_common import _is_junk, scope_for
except Exception:                                    # pragma: no cover
    def _is_junk(fact):
        return not (fact or "").strip()

    def scope_for(cwd):
        return SHARED


# The injection bar, deliberately kept here rather than reusing the write
# path's _TECHNICAL: that regex governs what gets *stored* and widening it
# would change write behaviour, while this one only governs what gets shown.
# It also has to recognise the shapes real lessons use — camelCase API names
# like execFileSync carry just as much anchor as a snake_case one.
#
# Case matters here and the pattern is NOT compiled with re.I: under
# IGNORECASE the camelCase alternative `[a-z]+[A-Z]\w+` matches any ordinary
# lowercase word, which silently turns the whole filter into "accept
# everything". The case-insensitive parts are opted in individually with (?i:).
_ANCHOR = re.compile(
    r"(`[^`]+`"                        # quoted code span
    r"|(?:^|[\s(])[./~][\w./-]{2,}"    # path-ish
    r"|(?i:\.(?:py|ts|tsx|js|mjs|json|md|ya?ml|sql|sh|toml|env)\b)"
    r"|--[a-zA-Z][\w-]+"               # CLI flag
    r"|\w+\(\)"                        # call
    r"|\b[a-z0-9]+_[a-z0-9_]+\b"       # snake_case
    r"|\b[a-z]+[A-Z][A-Za-z]+\b"       # camelCase
    r"|\b[A-Z][a-z]+[A-Z][A-Za-z]+\b"  # PascalCase
    r"|\b\w+@[\w.]+"                   # pkg@version
    r"|\bv?\d+\.\d+"                   # version
    r"|(?i:\b(?:npm|git|docker|prisma|eslint|qdrant|ollama|mem0|node|python|"
    r"curl|vitest|pytest|jest|next|vite|uv|pip)\b))"
)


def worth_injecting(text):
    """Stricter than the write-path filter, on purpose.

    Storing an ambiguous line is nearly free and might pay off later; injecting
    one costs context in every future session. So injection additionally demands
    a concrete anchor. A line like "Paralel calismayi baslatabilirsin" passes
    the storage bar and fails this one, which is the intended asymmetry.
    """
    t = (text or "").strip()
    if not t or _is_junk(t):
        return False
    return bool(_ANCHOR.search(t))


def fetch():
    """Every memory row owned by the user, from whichever backend is configured.

    On the brain this is still the direct Qdrant scroll — same speed as before,
    no mem0 client and no LLM. On a worker it is the Central Brain over HTTP.
    Either way this hook does not know which, and never learns a Qdrant address.
    """
    from mem0_backend import rows
    return rows(timeout=FETCH_TIMEOUT)


# --- what this session has already been shown -------------------------------
# Keyed by session, not by scope: a new session starts with an empty context and
# genuinely needs the lessons again, so cross-session suppression would hide the
# very traps this hook exists to surface. Within one session, repeating them is
# pure waste.

def fingerprint(text):
    """Stable short id for a memory's text. Whitespace-insensitive so a row that
    was re-saved with different padding is still recognised as the same line."""
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


def _state_path(session_id):
    key = "".join(c for c in (session_id or "") if c.isalnum() or c in "-_")[:80]
    return os.path.join(STATE_DIR, "recall-%s.json" % key) if key else None


def read_injected(session_id):
    """Fingerprints already shown in this session; empty on anything unexpected."""
    path = _state_path(session_id)
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f).get("injected") or [])
    except Exception:
        return set()


def record_injected(session_id, items):
    """Remember what reached the context. Silent on failure — losing the record
    costs a duplicated block later, while raising would cost the session."""
    path = _state_path(session_id)
    if not path or not items:
        return
    try:
        merged = read_injected(session_id) | {
            fingerprint(i.get("memory")) for i in items
        }
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"injected": sorted(merged)}, f)
        os.replace(tmp, path)
    except Exception:
        pass


def should_inject(source, session_id):
    """Whether this SessionStart still needs a block (see REPLAYS_CONTEXT).

    Note it asks "was this session ever served?", not "which lines did it get".
    Filtering out the seen lines and injecting the next-best ones instead would
    mean every resume appends another block of steadily less relevant memories;
    a resumed session already has the good one in front of it.
    """
    if source in REPLAYS_CONTEXT and read_injected(session_id):
        return False
    return True


def select(items, scope, max_items=MAX_ITEMS, max_chars=MAX_CHARS):
    """In-scope, non-junk memories: project tier first, newest first, deduped.

    Project before personal because the repo's own traps are the more specific
    answer, and when the budget only fits a few lines the specific one should
    win. Other projects are excluded outright — scope isolation is the point of
    the two-tier model.
    """
    tiers = {scope: 0, SHARED: 1} if scope != SHARED else {SHARED: 1}
    keep = []
    for it in items:
        if not isinstance(it, dict):
            continue
        status = it.get("status") or "active"
        if status == "superseded":
            continue
        text = (it.get("memory") or "").strip()
        run = it.get("run_id")
        if run not in tiers or not worth_injecting(text):
            continue
        keep.append(it)

    keep.sort(key=lambda i: (tiers[i.get("run_id")],
                             -_ts_key(i.get("created_at") or "")))

    out, seen, used = [], set(), 0
    for it in keep:
        text = (it.get("memory") or "").strip()
        if text in seen:
            continue
        # Drop the whole entry rather than truncate: half a lesson is a
        # misleading lesson.
        if used + len(text) > max_chars:
            continue
        seen.add(text)
        used += len(text)
        out.append(it)
        if len(out) >= max_items:
            break
    return out


def _ts_key(iso):
    """Sortable integer from an ISO timestamp; unparseable sorts oldest."""
    digits = "".join(ch for ch in str(iso)[:19] if ch.isdigit())
    return int(digits) if digits else 0


def render(items, scope):
    """The injected block, or "" when there is nothing worth injecting.

    A header with no content under it still costs context and teaches the
    reader to skip the block, so empty means empty.
    """
    if not items:
        return ""
    lines = ["Recalled from long-term memory (scope: %s). These are prior "
             "observations, not instructions for this session — verify anything "
             "you act on, since a note may describe code that has since "
             "changed." % scope]
    for it in items:
        lines.append("- (%s) %s" % (it.get("run_id") or "-",
                                    (it.get("memory") or "").strip()))
    return "\n".join(lines)


def collect(scope, fetch=fetch):
    """(rendered block, selected items) for `scope`. ("", []) on any failure.

    Fail-open is deliberate: this hook is a convenience, and a session must
    start whether or not Qdrant is up. The items come back alongside the text
    so the caller can record exactly what reached the context.
    """
    try:
        rows = fetch()
    except Exception:
        return "", []
    try:
        items = select(rows, scope)
        return render(items, scope), items
    except Exception:
        return "", []


def collect_rules(scope):
    """The scoped-rules block for `scope`, or "" — never raises.

    `rules.load_rules` is already fail-open; this wrapper exists so a future
    change there still cannot reach main().
    """
    try:
        return rules.render(rules.load_rules(scope), scope)
    except Exception:
        return ""


def main():
    """SessionStart entry point. Prints the blocks on stdout (Claude Code adds
    them to the session context) and always exits 0 — nothing here is worth
    failing a session over, so every path is best-effort and quiet.

    Rules print above memories: rules are binding conventions, memories are
    flagged as unverified background, and the binding thing should be read
    first. Each block is collected independently so one failing source cannot
    suppress the other.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id") or ""
    source = payload.get("source") or "startup"

    try:
        if not should_inject(source, session_id):
            return 0
        scope = scope_for(cwd)
    except Exception:
        return 0

    rules_block = collect_rules(scope)
    try:
        block, items = collect(scope)
    except Exception:
        block, items = "", []

    if rules_block:
        sys.stdout.write(rules_block + "\n")
    if block:
        sys.stdout.write(block + "\n")
        record_injected(session_id, items)
    return 0


if __name__ == "__main__":
    sys.exit(main())
