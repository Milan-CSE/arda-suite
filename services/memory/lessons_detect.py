#!/usr/bin/env python3
"""
lessons_detect — SessionEnd transcript scanner for verified failure->fix cycles.

Pure Python, no LLM (see docs/lessons-loop-spec.md §4.1, §4.2 and Decision Log D1/D2).
Reads a Claude Code transcript, applies the evidence-based filters, reconciles
against pending-failures.jsonl (cross-session), and appends verified failure->fix
records to verified-failures.jsonl — the evidence the lesson-extraction pass reads.

A verified failure->fix requires ALL of:
  1. an allowlisted test/build command (normalized + hashed; `npm test` == `npm run test`)
  2. a GENUINE failure — is_error==true AND content not in the exclusion list
     (permission/classifier/user-reject/timeout/tool-error are NOT failures)
  3. a later pass of the SAME command hash
  4. >= 1 Edit/Write between the failure and the pass (no edit = flaky rerun)
"""
import argparse
import datetime
import hashlib
import json
import os
import re

MEM0_ROOT = os.environ.get("MEM0_ROOT", os.path.dirname(os.path.abspath(__file__)))
PENDING_LOG = os.path.join(MEM0_ROOT, "pending-failures.jsonl")
VERIFIED_LOG = os.path.join(MEM0_ROOT, "verified-failures.jsonl")

TEST_COMMANDS = ["pytest", "npm test", "npm run test", "yarn test", "pnpm test",
                 "bun test", "vitest", "jest", "flutter test",
                 "python -m unittest", "python3 -m unittest", "unittest",
                 "go test", "cargo test", "tsc", "npm run typecheck",
                 "next build", "npm run build", "make test"]
ERROR_EXCLUDE = ["denied by the claude code auto mode classifier",
                 "permission for this action was denied",
                 "tool use was rejected", "doesn't want to proceed",
                 "temporarily unavailable", "timed out", "<tool_use_error>"]
PENDING_TTL_DAYS = 7


def normalize(cmd):
    c = re.sub(r"\s+", " ", (cmd or "").strip().lower())
    return re.sub(r"^(npm|pnpm|yarn) run ", r"\1 ", c)  # "npm run test" -> "npm test"


def cmd_hash(cmd):
    return hashlib.sha1(normalize(cmd).encode()).hexdigest()[:12]


def is_test_command(cmd):
    n = normalize(cmd)
    for seg in re.split(r"&&|;|\|", n):        # any &&-segment may be the test cmd
        seg = seg.strip()
        for t in TEST_COMMANDS:
            tn = normalize(t)
            if seg == tn or seg.startswith(tn + " "):
                return True
    return False


def _content_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return str(c)


def is_genuine_failure(is_error, content):
    if not is_error:
        return False
    low = _content_text(content).lower()
    return not any(p in low for p in ERROR_EXCLUDE)


def parse_transcript(path):
    """Ordered events: ('bash', ts, cmd, is_error, content) | ('edit', ts, file)."""
    uses, events = {}, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = e.get("timestamp", "")
            c = e.get("message", {}).get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    uses[b.get("id")] = (b.get("name"), b.get("input", {}) or {})
                    if b.get("name") in ("Edit", "Write"):
                        events.append(("edit", ts, (b.get("input", {}) or {}).get("file_path", "")))
                elif b.get("type") == "tool_result":
                    name, inp = uses.get(b.get("tool_use_id"), (None, {}))
                    if name == "Bash":
                        events.append(("bash", ts, inp.get("command", ""),
                                       bool(b.get("is_error")), b.get("content", "")))
    return events


def _days_between(iso, now_iso):
    try:
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        n = datetime.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        return (n - t).days
    except Exception:
        return None


def load_pending():
    if not os.path.exists(PENDING_LOG):
        return []
    out = []
    for line in open(PENDING_LOG, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def write_pending(items):
    with open(PENDING_LOG, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def scan(path, project, now_iso, dry_run=False):
    events = parse_transcript(path)
    session = os.path.basename(path).split(".")[0]

    open_fail, kept_pending = {}, []
    for p in load_pending():                     # seed cross-session failures
        age = _days_between(p.get("ts", ""), now_iso)
        if age is not None and age > PENDING_TTL_DAYS:
            continue                              # expired → drop
        open_fail[p["cmd_hash"]] = {"command": p["command"], "ts": p["ts"],
                                    "idx": -1, "cross": True}
        kept_pending.append(p)

    verified, edits = [], []
    for idx, ev in enumerate(events):
        if ev[0] == "edit":
            edits.append((idx, ev[2]))
            continue
        _, ts, cmd, is_err, content = ev
        if not is_test_command(cmd):
            continue
        h = cmd_hash(cmd)
        if is_genuine_failure(is_err, content):
            open_fail[h] = {"command": cmd, "ts": ts, "idx": idx, "cross": False}
        elif not is_err and h in open_fail:       # a pass closing an open failure
            of = open_fail.pop(h)
            lo = -1 if of["cross"] else of["idx"]  # cross-session: any edit this session
            files = sorted({f for (i, f) in edits if lo < i < idx and f})
            if files:                              # gate #4: edit between
                verified.append({
                    "command": cmd, "cmd_hash": h, "fail_ts": of["ts"], "pass_ts": ts,
                    "edited_files": files, "cross_session": of["cross"],
                    "project": project, "session": session, "detected": now_iso,
                    "processed": False,   # idempotency: extraction (2b) flips this once
                })
            kept_pending = [p for p in kept_pending if p["cmd_hash"] != h]

    new_pending = kept_pending + [
        {"cmd_hash": h, "command": of["command"], "ts": of["ts"],
         "session": session, "project": project}
        for h, of in open_fail.items() if not of["cross"]
    ]

    if not dry_run:
        write_pending(new_pending)
        if verified:
            with open(VERIFIED_LOG, "a", encoding="utf-8") as f:
                for v in verified:
                    f.write(json.dumps(v, ensure_ascii=False) + "\n")
    return {"verified": verified, "pending": new_pending}


def main():
    ap = argparse.ArgumentParser(description="SessionEnd verified failure->fix scanner")
    ap.add_argument("transcript")
    ap.add_argument("--project", default="")
    ap.add_argument("--now", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    now = a.now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = scan(a.transcript, a.project, now, dry_run=a.dry_run)
    print(f"verified failure->fix: {len(r['verified'])}  |  pending carried: {len(r['pending'])}")
    for v in r["verified"]:
        tag = " [cross-session]" if v["cross_session"] else ""
        print(f"  {v['command'][:44]} | fix files={v['edited_files']}{tag}")


if __name__ == "__main__":
    main()
