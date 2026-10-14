# Lessons Loop — Spec

**Home:** `services/memory/docs/lessons-loop-spec.md` (the system revolves around mem0;
Jarvis and Claude Code are consumers — they only carry a reference line to this file).

**Status:** spec (spec-first by design: a wrong measurement design makes the code
spam lessons, so trigger + schema + promotion are nailed here before implementation).

## Decision Log

- **D1 (2026-08-05) — Fix-detection is a SessionEnd transcript scan, not a live
  PostToolUse hook.** *Rationale:* a live hook would need cross-invocation state
  (fail→pass correlation), would depend on the uncertain availability of exit code /
  `is_error` in `tool_response`, and adds per-command latency. The transcript is the
  authoritative record (every tool call, `is_error`, edits, in order) and is written
  by Claude Code for free. Detecting at SessionEnd honors the "judgment at SessionEnd"
  principle more cleanly and yaslanır to the source of truth. (Supersedes the earlier
  §11-step-1 wording.)
- **D2 (2026-08-05) — `is_error==true` is filtered by evidence, not treated as
  failure.** *Rationale:* sampling all 12 `is_error` results in the spec-authoring
  session found 0 real test failures; a naive scanner = 12/12 false positives. Hence
  the allowlist + content-exclusion filters in §4.1, both seeded from that sample.

---

## 1. Purpose

Close the loop the videos describe: **error → measure → lesson → don't-repeat.**
A durable "don't do this again" is extracted from *verified* mistakes, stored in
mem0 as a structured `lesson`, recalled at task start, and **measured by repeat
rate**. Lessons that keep failing are updated or deleted; lessons that generalize
graduate to the ruleset. mem0 is the *incubator*; rules files are the *graduation*.

**Load-bearing rule (from the videos): "no measurement, no loop."** The success
metric is NOT "how many lessons saved" (Goodhart → memory landfill). It is the
**repeat rate**: does the same `trigger` + similar `mistake` recur *after* the
lesson was recalled into context? If yes, the lesson failed.

---

## 2. What's already built (this spec extends it)

- **Separate extractor is already satisfied at the model level.** The main agent
  (Opus) produces the work; `~/.claude/hooks/mem0-autosave.py` (SessionEnd) hands
  the transcript to a *different* model (qwen3:1.7b via mem0) to extract. The
  "measurer ≠ producer" principle needs no new plumbing — only a lesson-specific
  extraction path alongside the general one.
- Hooks tab, `projects-activity.jsonl`, `usage.jsonl` — the measurement substrate.
- mem0 native Qdrant (`:6333`), scoped memory (`run_id` = project), `agent` tag.

---

## 3. Metadata schema (`type: lesson`)

Stored in mem0 with `infer=False` (raw write; dedup happens in *our* extraction
pass, not mem0's — `rule` sentences are short and mem0's ADD/UPDATE/DELETE
occasionally merges unrelated lessons).

```json
{
  "type": "lesson",
  "agent": "claude-code",
  "project": "sample-project",
  "trigger": "next.js build",
  "mistake": "use client eksik bırakıldı, hook kullanan component'te",
  "rule": "Hook kullanan her component'in başına 'use client' kontrol et",
  "evidence": "commit abc123",
  "created": "2026-08-05T09:00:00Z",
  "last_recalled": null,
  "hit_count": 0,
  "miss_count": 0,
  "recall_pass_count": 0,
  "promoted": false
}
```

- `rule` — the only forward-looking field: one sentence, actionable. This is what
  gets injected and what graduates. `mistake` is context only. `trigger` powers
  recall (agent starting a task searches by trigger).
- `hit_count` / `miss_count` / `recall_pass_count` — see §7 (Measurement).

---

## 4. Triggers — when is a lesson written?

Only on a **verified** mistake, never on the agent's self-doubt ("sanırım hata
yaptım" → garbage). Three signals, with different determinism, phased accordingly:

| Signal | Determinism | Phase |
|--------|-------------|-------|
| **test/build fail → pass** in the same session (fix diff in hand) | **Deterministic** — a PostToolUse hook logs the command + exit code; a failing test/build followed by a passing one is a verified fix | **Phase 1 (start here)** |
| **User correction** ("hayır öyle değil, şöyle yap") | **Fuzzy** — no hook catches intent; left to the extraction pass, behind a **strict** gate | Phase 1 (extractor-gated) |
| **Second occurrence** of the same trigger+mistake | Needs the recall+measurement loop running (chicken-and-egg) | **Phase 2** |

Phase 1 = deterministic fix-detection + gated extraction. Phase 2 = repeat
detection once recall is live.

### 4.1 What counts as a failure — evidence-based, not `is_error`

Detection reads the **transcript** (authoritative: every tool call, its `is_error`
flag, and edits, in order). But `is_error == true` is **NOT** "a test failed."
Sampling the 12 `is_error` results in the spec-authoring session showed **zero**
real test failures — all were benign non-zero exits (`ls`/`pgrep`/`defaults`
returning 1), permission/classifier denials, user rejections, Edit "string not
found", MCP tool errors, and infra "temporarily unavailable." A naive scanner
would have produced **12/12 false positives.** Two filters, both required:

1. **Command allowlist (config-driven, extensible).** Only `Bash` calls whose
   *normalized* command matches a known test/build command: `pytest`,
   `npm test`/`npm run test`, `yarn test`, `pnpm test`, `go test`, `cargo test`,
   `tsc`/`npm run typecheck`, `next build`, `npm run build`, `make test`, … .
   Normalize = lowercase + collapse whitespace + drop the `npm run`/`yarn`/`pnpm`
   prefix, so `npm test` and `npm run test` **hash equal** (§4 gate #1). Non-Bash
   tools and non-allowlisted commands are ignored entirely — this alone kills every
   one of the 12.
2. **Content exclusion (seeded from the evidence).** Even for an allowlisted command,
   if the `is_error` content matches any of these it is **not** a genuine failure:
   `denied by the Claude Code auto mode classifier`,
   `Permission for this action was denied`,
   `tool use was rejected` / `doesn't want to proceed`, `temporarily unavailable`,
   `timed out`, `<tool_use_error>`. The list lives in config and grows as new
   non-failure patterns surface.

A **verified failure→fix** = an allowlisted command, same normalized hash,
genuinely `is_error` at T, later `is_error==false` at T′>T, **with ≥1 `Edit`/`Write`
between T and T′** (§4 gate #2 — no edit between = flaky rerun, not a fix). The
record carries the command, both timestamps, and the **list of files edited in the
window** — so §5 extraction derives the rule from evidence, never guesses.

> **Subagent scope (v1):** subagent tool calls may not appear in the main
> transcript. v1 detection covers the **main thread only**; subagent-internal
> fail→fix cycles are explicitly out of scope, noted here so they are not silently
> missed.

### 4.2 Cross-session — `pending-failures.jsonl`

The most valuable lessons span sessions: fail tonight, fix tomorrow. A single
SessionEnd transcript scan can't see that. So:

- At SessionEnd, genuine failures with **no matching pass yet** in this transcript
  go to `services/memory/pending-failures.jsonl` (`{cmd_hash, command, ts, session,
  project}`).
- The next session's scan **loads pending first**; a pass of the same `cmd_hash`
  (edit between — the edit may live in the new session) **closes** it into a
  verified failure→fix.
- Pending older than **N days** (config, default 7) is dropped so it never grows
  unbounded.

---

## 5. Extraction pass

Runs at SessionEnd (reuses the `mem0-autosave.py` scaffold, separate lesson path).
Reads the transcript + the fix-detection log, and emits **structured lessons or
nothing**.

### 5.1 Extraction model — configurable, with a manual-review gate (ADDITION 1)

> **qwen3:1.7b may be too weak for this gate.** "Is there a durable lesson, else
> write NOTHING" is the system's most critical judgment call, and a 1.7b model is
> prone to over-triggering (extracting a lesson from everything). 1.7b is fine for
> general autosave; lesson extraction is heavier judgment.

- The extraction model is a **separate config field** (`lessons.extraction_model`),
  independent of the autosave/embedding model. Default `qwen3:1.7b`, but designed
  to be swapped.
- **First week: manual review is mandatory.** The user reviews every extracted
  lesson (`jarvis memory list --type lesson`). If the false-positive rate is high,
  escalate the model to **qwen3:8b** — running at SessionEnd, so it does **not**
  block the Ollama queue (the queue concern was for the live voice flow, not for
  post-session batch work).
- **qwen3 thinking block:** extraction output must be clean JSON. The prompt uses
  `/no_think` (or the request disables thinking), **and** the parser strips any
  `<think>…</think>` block before `json.loads` — belt and suspenders, written
  explicitly so a thinking-mode regression doesn't silently corrupt every lesson.

### 5.2 Extraction prompt (contract)

```
You are reviewing a finished coding session for DURABLE lessons only.
A durable lesson is a mistake that was VERIFIED (a test/build failed then passed
after a fix, or the user explicitly corrected the agent) AND is likely to recur.

Output STRICT JSON: an array of lessons, or [] if there is no durable lesson.
Each lesson: {"trigger": <2-5 word task cue>, "mistake": <one clause of context>,
"rule": <ONE forward-looking, actionable sentence>, "evidence": <commit/why>}.

Rules:
- If nothing was VERIFIED, output []. "The agent might have erred" is NOT a lesson.
- One sentence per `rule`. No essays. No stylistic nitpicks a linter would catch.
- Do NOT restate existing rules; a lesson duplicating a ruleset rule is noise.
- When unsure, output []. Under-writing beats a landfill.
/no_think
```

The "output [] / write NOTHING" instruction is the most important line. Bias the
whole gate toward silence.

### 5.3 Write

For each extracted lesson: `mem.add(rule_text, user_id="example-user", run_id=<project>,
agent_id="claude-code", metadata={type:"lesson", ...}, infer=False)`. Dedup against
existing lessons of the same `trigger` in *our* pass (cosine over `rule`), not
mem0's inference.

---

## 6. Recall

At **task start**, scoped search, injected into system context:

```
search("lessons for: <task summary>", filters={type:"lesson", project:<X>}) → top 3-5
```

- Inject **only top 3-5** — never dump all. 50 lessons in context = agent reads
  none, context bloats.
- **Injection log (ADDITION from review — the loop can't close without it):** every
  time a lesson is injected, record `{lesson_id, session_id, ts}` to
  `services/memory/lessons-recall.jsonl` and bump `last_recalled`. Measurement (§7)
  reads this to answer "was the lesson in context when the mistake recurred?" —
  without it, repeat-rate is unmeasurable and the loop is open.

---

## 7. Measurement — the heart of the loop

### 7.1 The real signal is `miss_count`, not `hit_count` (ADDITION 2)

> **`hit_count` is a weak, indirect signal — the spec admits it openly.** "Lesson
> was in context and no mistake happened" is ambiguous: maybe the lesson prevented
> it, maybe the topic never came up. `hit_count` only measures recall *traffic*
> ("is this lesson getting retrieved"), not effectiveness.

- **`miss_count` is the real signal:** a lesson was recalled (per the injection log)
  and the **same `trigger` + similar `mistake` recurred anyway** → the lesson didn't
  work. Increment `miss_count`. A lesson with rising `miss_count` is **rewritten or
  deleted**, not kept.
- **`recall_pass_count`:** recalled AND the same trigger's task completed without a
  verified failure. This is the effectiveness proxy that promotion uses — stronger
  than raw `hit_count` because it requires the trigger to have *actually occurred*.

### 7.2 Repeat rate (system metric)

Track, across all lessons: of the mistakes that recurred, how many had their lesson
in context at the time (from the injection log). A recurrence *with* the lesson in
context is a lesson failure; drives review. This number — not "lessons saved" — is
the loop's health metric.

> **Data access (binding).** Every metric here iterates the **full** lesson set
> (`miss_count`/`recall_pass_count` reconciliation, repeat-rate). It **MUST scroll
> Qdrant directly, never mem0 `get_all`** — `get_all` silently caps at ~20 regardless
> of the limit arg, so on a store of >20 lessons the counts would be quietly wrong and
> promotion would fire on partial data. Filter on the flattened `pl["type"]`, not
> `pl["metadata"]["type"]`. (This is why the two mem0 lessons exist.)

---

## 8. Promotion (graduation) — layered (ADDITION 3)

A lesson lives in mem0 until it proves durable, then graduates OUT of mem0 (deleted
after promotion — mem0 is the incubator, not the archive). **Two graduation paths,
by scope:**

| Lesson scope | Graduates to | Criterion |
|--------------|--------------|-----------|
| **Single-project** (held in 1 project) | that repo's **`AGENTS.md`** (the symlink bridge already loads it into Claude Code) | `recall_pass_count ≥ 3` in that project **and** `miss_count == 0` |
| **Cross-project** (same `rule` held in **N = 2** distinct projects) | **`~/.claude/rules/`** (global) | held (recalled + passed) in **≥ 2** distinct projects, `miss_count == 0` |

- **N is fixed at 2**, not "a few." A rule seen useful in two unrelated projects is
  general enough for the global layer.
- Promotion is based on `recall_pass_count` (recalled **and** passed the same
  trigger) — **never on `hit_count`** — so a much-recalled-but-useless lesson does
  not graduate.
- On promotion: write the `rule` to the target file, then **delete the mem0 lesson**.

> **Data access (binding).** Detecting "same `rule` held in ≥2 projects" scans the
> full lesson set across scopes. Iterate by **Qdrant scroll, never mem0 `get_all`**
> (§7 data-access note) — a capped read would hide qualifying lessons and mis-fire
> promotion in either direction.

---

## 9. Hygiene (existing rules apply unchanged)

- **Rule vs lesson conflict → the rule wins, the lesson is deleted** (already
  decided on the AGENTS.md side). A lesson contradicting the ruleset is stale.
- A lesson that generalizes past N=2 → promote and delete; it must not linger in
  mem0 after graduating.
- `jarvis memory list/rm` gains a **`--type lesson`** filter so dead lessons can be
  swept monthly.
- Never store secrets/PII (per `common/security.md`) — lessons are `rule` sentences,
  not payloads.

---

## 10. Config (`config.py` / lessons block)

```python
LESSONS = {
    "enabled": True,
    "extraction_model": os.environ.get("LESSONS_EXTRACTION_MODEL", "qwen3:1.7b"),
    "manual_review_until": "2026-08-12",   # first-week hard gate
    "recall_top_k": 5,
    "promotion": {"single_project_passes": 3, "cross_project_N": 2},
    # §11.1 — 2b (extraction) may not start until the sample is diverse enough
    "extraction_gate": {"min_records": 5, "min_projects": 2, "min_command_types": 2},
    "recall_log": "services/memory/lessons-recall.jsonl",
    "pending_log": "services/memory/pending-failures.jsonl",
    "pending_ttl_days": 7,
    # §4.1 detection — extensible without touching code
    "test_commands": ["pytest", "npm test", "npm run test", "yarn test",
                      "pnpm test", "go test", "cargo test", "tsc",
                      "npm run typecheck", "next build", "npm run build", "make test"],
    "error_exclude": ["denied by the claude code auto mode classifier",
                      "permission for this action was denied",
                      "tool use was rejected", "doesn't want to proceed",
                      "temporarily unavailable", "timed out", "<tool_use_error>"],
}
```

---

## 11. Implementation order

1. **Fix-detection = SessionEnd transcript scan** (Phase 1 trigger; **not** a live
   PostToolUse hook — see Decision Log D1). Pure Python, no LLM: parse the transcript
   applying §4.1 (allowlist + evidence-based exclusion + edit-between gate), reconcile
   against `pending-failures.jsonl` (§4.2), and write verified failure→fix records to
   `services/memory/verified-failures.jsonl` with the edited-files window.
2. **Lesson extraction** at SessionEnd, right after step 1 (extend `mem0-autosave.py`,
   separate path, reads `verified-failures.jsonl` as evidence, `infer=False`,
   configurable model, `/no_think` + parse cleanup).
3. **Recall + injection log** at task/session start (top-k, write `lessons-recall.jsonl`).
4. **Measurement**: `miss_count` / `recall_pass_count` updater; repeat-rate report
   (a Dashboard "Lessons" view is a natural surface).
5. **Promotion** job (layered) + `jarvis memory --type lesson` CLI.

Step 2 splits: **2a** = wire detection into SessionEnd (done — isolated, no
extraction). **2b** = the extraction pass, written *on real records* (§11.1).
Ship 1→2a first (capture), review manually, then 2b, then 3→4 (close the loop), then 5.

### 11.1 When to start 2b (extraction) — a counted gate, not "a few days"

Do **not** start writing the extraction prompt until `verified-failures.jsonl` holds
a **diverse** real sample. Concretely, the gate is:

- **≥ 5–8 verified records**, AND
- from **≥ 2 distinct projects**, AND
- across **≥ 2 distinct command types** (not only `npm test` — at least one of
  `pytest` / `tsc` / `go test` / … too).

Diversity is not optional: a prompt written against one command type **overfits** to
it. The number is fixed here so "hadi artık geçelim" impatience can't erode it.

**The waiting period is the DETECTOR's review week, not the extraction's.** Every few
days, read `verified-failures.jsonl` by hand and ask of each record: *was this really
"I erred, then fixed it"?* A flaky rerun, a wrong hash match, or an unrelated edit that
slipped in means **detection has a hole** — and finding it now is free; finding it
after extraction is built means poisoned lessons. Fix the detector before 2b.

**No forcing data (Goodhart guard).** "Accumulate records" must not become the target —
deliberately failing then fixing is just a synthetic fixture in disguise. Work
normally; let the file fill at its natural rate. If records accumulate slowly, that is
itself a finding (allowlist too narrow? tests run rarely in real flow?), not a problem
to engineer around.

**Meta-ratio review check.** During bootstrap the first lessons are all *about the
system itself* (extraction model, hook exec, mem0 access) — expected, since building
the system is what you touch most. But after a few weeks, if the lesson pool is **still
~100% meta** (nothing from real project work like sample application development), that is a
signal **detection isn't catching real project fail→fix cycles** — check the allowlist
coverage and the edited-files window against an actual project session. Track the
meta-vs-project ratio as one of the wait-period's questions.

**2b dogfood check:** when 2b begins, the extraction model is config-set to 8b — which
is literally the system's first stored lesson. Confirm that lesson is recalled and
applied at the start of 2b: the first real end-to-end proof the loop feeds itself.

---

## 12. Consumers

- **Jarvis spec:** one reference line → this file. Jarvis is a consumer, not the home.
- **Claude Code:** the hooks in §11 live in `~/.claude/hooks/`; recall injection via
  SessionStart / task-start.
