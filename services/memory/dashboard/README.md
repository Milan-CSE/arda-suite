# 🧠 Shared Brain — Control Panel

A local, self-hosted web dashboard for the mem0-based "shared brain" AI memory
system. Single place to see and manage memories, skills, agents, MCP servers,
rules, and system health across Claude, Codex, Cursor, VS Code, and Cline where
local config files are present.

## Start it

```bash
"$HOME/mem0-local/dashboard/run.sh"
```

or directly:

```bash
"$HOME/mem0-local/bin/mem0-python" "$HOME/mem0-local/dashboard/server.py"
```

Then open <http://localhost:8899>. Stop with `Ctrl+C`.

Run in the background:

```bash
nohup "$HOME/mem0-local/dashboard/run.sh" > /tmp/shared-brain.log 2>&1 &
# stop later:
pkill -f dashboard/server.py
```

## Requirements

- The mem0-local venv at `<repo>/.venv` (already set up). Nothing names an
  interpreter directly — `bin/mem0-python` resolves it in one place
  (`$MEM0_PYTHON` → repo venv → `python3`).
- **Ollama** running (LLM + embeddings) — `Ollama.app`.
- **Qdrant** running — the `mem0-qdrant` Docker container.

The backend uses **only** the Python standard library plus the local `mem0`
package. No Flask/FastAPI, no extra pip installs. The page is fully
self-contained (inline HTML/CSS/JS, no CDNs) so it works offline.

## Panels

| Panel        | Shows |
|--------------|-------|
| **Overview** | Health tiles: Ollama, Qdrant, Docker, plus counts for skills, agents, memories & scopes. Green/amber/red status. |
| **Memory**   | The star. Browse/search/add/delete memories. Semantic search box, scope + agent filters. 🧠 Shared Brain (`personal`, the global tier) is visually distinguished from 📁 project scopes. Project-scoped semantic search also includes the shared tier. Add form (text, scope, agent). |
| **Skills**   | Every discovered `SKILL.md` under Claude and Codex skill/plugin homes (name + description), searchable/filterable. |
| **Agents**   | Sub-agents under local AI tool homes (currently Claude + Codex when present). |
| **MCP**      | Configured MCP servers from Claude, Codex, Cursor, VS Code, and Cline config files, with command. |
| **Rules**    | Rule trees under local AI tool homes, grouped by source/folder. |
| **Architectures** | Long-running build plans tracked through the 16-phase AI Development Protocol — phase rail, per-phase tasks with P0–P4 priorities, and the context files each plan depends on. See below. |

## Scope model

- `personal` = the **global "shared brain"** tier (cross-project) — labeled
  **🧠 Shared Brain (global)**.
- Any other scope value = a **project bucket** (`run_id` in mem0).
- A project-scoped search reads that project bucket plus `personal`, then
  de-duplicates and sorts by semantic score.
- `agent` = who wrote the memory (`agent_id`).

mem0 calls hit the local Ollama LLM and can take a few seconds — the UI shows
loading states while they run.

## API (JSON)

| Method + path | Purpose |
|---------------|---------|
| `GET /` | Dashboard HTML |
| `GET /api/health` | Ollama / Qdrant / Docker status + counts |
| `GET /api/memories?scope=&agent=` | All memories (+ scope/agent tallies) |
| `GET /api/search?q=&scope=&agent=` | Semantic search |
| `POST /api/memory` | Add — body `{text, scope, agent}` |
| `DELETE /api/memory?id=` | Delete one memory |
| `GET /api/skills?q=` | Skills (optional filter) |
| `GET /api/agents` | Agents |
| `GET /api/mcp` | MCP servers |
| `GET /api/rules` | Rules tree |
| `GET /api/architectures?repo=&status=&q=` | Architecture summary rows + the phase/status/priority catalogs |
| `GET /api/architecture?id=` | One architecture, with derived per-phase counts |
| `POST /api/architecture` | Create — body `{title, repo, summary}` → `201` |
| `PATCH /api/architecture` | Update — body `{id, title?, summary?, status?, current_phase?, phase?}` |
| `DELETE /api/architecture?id=` | Delete one architecture |
| `POST /api/architecture/task` | Add task — body `{arch, phase, title, priority}` → `201` |
| `PATCH /api/architecture/task` | Update task — body `{arch, id, …}`; replies with `auto_advanced` when the write finished the phase |
| `POST /api/architecture/pause-auto` | Stop this architecture advancing itself — body `{id}` |
| `POST /api/architecture/resume-auto` | Let it advance itself again — body `{id}` |
| `POST /api/architecture/chain` | Turn auto-chain on or off — body `{id, on}` |
| `DELETE /api/architecture/task?arch=&id=` | Delete one task |
| `PUT /api/architecture/workflow` | Bulk phase update — body `{id, phases:{<phase>:{status,note}}}` |
| `GET /api/architecture/templates` | Template catalog + `warnings` for skipped user files |
| `GET /api/architecture/template?id=` | One template, playbook resolved |
| `POST /api/architecture/template` | Create a **user** template file → `201`; built-in or duplicate id → `409` |
| `DELETE /api/architecture/template?id=` | Delete a user template; built-in → `403` |
| `POST /api/architecture/run` | Start an agent run — body `{id}` → `202`; already running → `409`; no `claude` → `503` |
| `POST /api/architecture/run/kill` | Stop the run in flight — body `{id}`; none → `404` |
| `GET /api/architecture/run/log?id=&run=&offset=` | Incremental log tail + run status |

Errors follow one ladder across these: invalid input → `400`, repo or id outside
the allowed roots → `403`, unknown architecture or task → `404`.

## Code Map

Renders the knowledge graph `graphify extract <repo> --code-only` writes to
`<repo>/graphify-out/`. Read-only by contract: the dashboard shows whatever
graph it finds and reports how stale it is, but never builds one — regenerate
with `graphify extract . --code-only` in the repo itself.

Repos come from `iter_repo_candidates()`: every directory under `~/Projeler`
plus everything in `EXTRA_REPOS` (currently `~/arda-suite/services/memory`, which lives outside
`~/Projeler`). The same helper backs the Projects tab and the `/api/codemap`
path guard, so what the picker offers and what the guard allows cannot drift.

Freshness compares the graph's `built_at_commit` against HEAD using read-only
git metadata. A graph built on a rewritten or different branch reads
`diverged` and deliberately reports no commit distance.

## Architectures

An **architecture** is one long-running build plan, stored as a single JSON file
at `~/.mem0/architectures/<id>.json` — inspectable and greppable from the shell
like the rest of `~/.mem0`.

Every architecture is **bound to a repository**. It carries the repo path plus
the `project_id` that `mem0_project.project_id()` resolves for it, which is the
same string mem0 files that project's memories under — so an architecture and
its memories share one scope. The repo picker offers exactly what the writer
accepts: both go through `iter_repo_candidates()` / `_resolve_repo()`, the same
guard behind the Projects and Code Map tabs.

### The 16-phase AI Development Protocol

```
discover → spec → context → design → contracts → decompose → prioritize → build
        → verify → review → approve → commit → ci → release → observe → learn
```

Fixed, in this order. `dashboard/test_architectures.py` pins the tuple, so the
list cannot drift from this document.

| Vocabulary | Values |
|---|---|
| Architecture status | `active` · `paused` · `done` · `archived` |
| Phase status | `pending` · `active` · `done` · `skipped` |
| Task status | `todo` · `doing` · `blocked` · `done` |
| Task priority | `P0` (drop everything) … `P4` (someday), default **`P2`** |

Priorities are accepted in either case (`p0` → `P0`) and stored uppercase.

### What is stored and what is not

Every task belongs to exactly one architecture **and one phase**. Per-phase task
counts, per-phase progress, overall progress and "which phase are we on" are
**computed on every read, never written to disk** — a stored derivation drifts
from its source the first time anything writes around it. Only the facts are
persisted: phase statuses, tasks, and the architecture's own fields.

Phase moves go through `PUT /api/architecture/workflow` as **one bulk update**:
marking a phase complete also activates the next one, and sending that as two
requests could leave the workflow half-moved. One lock, one atomic write.

`context_files` records the paths a plan depends on. The `exists` flag is
recomputed on every read, and the panel marks a vanished file ⚠️ rather than
dropping it — the record is still the truth about what the work depends on.

### Templates

A new architecture starts from a **template**: a named, ordered *subset* of the
16 phases plus the failure loops that belong to that shape. Templates never
invent phases — the protocol is the protocol, and a template only says which
parts of it apply to this piece of work.

Built-in:

| id | Name | Tier | Phases | Loops |
|---|---|---|---|---|
| `full-16` | 🚀 Full Protocol | L | all 16 | verify→build, review→build, ci→build, approve→spec |
| `feature` | 🧩 Feature on Existing Product | M | 9 (spec…release) | verify→build, review→build, ci→build |
| `bugfix` | 🐛 Bug Fix | S | discover, build, verify, commit | verify→build |
| `code-review` | 🔍 Code Review | S | context, review, approve, commit | approve→review |
| `prototype` | ⚡ Prototype | S | discover, build, verify | verify→build |

**A template is copied into the architecture, not referenced.** Phases, loops,
the playbook and any seed tasks are written into the document at create time, so
editing or deleting a template later never rewrites work already under way. The
`template_id` stays on the document as provenance.

Once created, the architecture's own phase list is authoritative: a task or a
bulk phase update naming a phase outside it is rejected with `400`.

#### Template schema

| Field | Required | Meaning |
|---|---|---|
| `id` | ✅ | slug, `[a-z0-9][a-z0-9-]*`, ≤64 chars — also the filename |
| `name` | ✅ | display name, ≤60 chars |
| `tier` | ✅ | `S` · `M` · `L` — rough size, shown as a badge |
| `phases` | ✅ | non-empty, ordered subset of the 16, no duplicates |
| `description` | | one or two sentences; the gallery clamps it to two lines |
| `icon` | | one emoji |
| `loops` | | `[{"from": …, "to": …, "label": "fail"}]` — both ends must be phases of *this* template; `label` defaults to `fail` |
| `playbook` | | per-phase `{agents, skills, rules}`; keys must be phases of this template. Omit it to inherit the global playbook narrowed to these phases — which is what every built-in does, so one fix to `phase_playbook.json` reaches all of them |
| `seed_tasks` | | `[{"phase": …, "title": …, "priority": "P2"}]`, created with the architecture |

#### Adding your own

Drop a JSON file in `~/.mem0/architectures/templates/`. It is read fresh on every
request — no restart, no cache.

```bash
cat > ~/.mem0/architectures/templates/spike.json <<'JSON'
{
  "id": "spike",
  "name": "Spike",
  "tier": "S",
  "icon": "🧪",
  "description": "Timeboxed probe. Throw the code away, keep the answer.",
  "phases": ["discover", "build"],
  "loops": [{"from": "build", "to": "discover", "label": "rethink"}]
}
JSON
```

**Shadowing.** A user file whose `id` matches a built-in *replaces* it in the
catalog. That is the supported way to adjust a built-in without editing the
source; delete the file to get the original back. The API marks such rows
`"shadows_builtin": true`.

**Broken files never take the catalog down.** A file that is not valid JSON, or
that fails validation, is skipped and named in `warnings` on the templates
response — the panel shows what it ignored rather than silently listing fewer
templates. Built-ins are validated too, but there a failure is a code bug and
raises rather than degrading.

`POST`/`DELETE` only ever touch user files: a built-in id returns `409` on create
and `403` on delete, because built-ins are code.

**v2, not built yet:** editing templates from the UI, and templates that define
phases of their own rather than selecting from the master 16.

### Auto-advance

A phase moves on by itself when its last task reaches `done`. Reporting tasks
accurately *is* how the project advances — nothing has to be clicked, and the
agent has no endpoint for advancing phases even if it wanted one.

It fires only when every one of these holds, and the panel names the one that is
holding rather than staying silent:

| Gate | Why |
|---|---|
| No task in the phase is `blocked` | A blocked task is unfinished work wearing a different hat |
| The phase has at least one task | An empty phase was never worked, so finishing it means nothing |
| The phase is not `approve`, `release` or `learn` | See below |
| Auto-advance is not paused for this architecture | The per-architecture override |
| There is a next phase | The last phase has nowhere to go |

**Three phases are held for a person on purpose.** `approve` is someone signing
their name to the work, `release` puts it in front of users, and `learn` is
reflection that only means anything if a human does it. Completing their tasks
is not the same as being done with them, so the machine stops and the header
shows ⚠️ *manual advance*. **Mark Phase Complete** still works there — the gate
is on the automation, not on you.

The advance and the task report that caused it are **one atomic write**, so two
reports landing at the same moment cannot both see "all done" and advance twice.
It moves exactly one phase per report: the next phase starts empty, which is
itself a gate, so nothing cascades through the protocol.

**Pause and resume** with the *Auto-advance: ON/OFF* button in the detail header,
or `POST /api/architecture/pause-auto` / `resume-auto` with `{id}`. The setting
lives on the architecture as `auto_advance_disabled`.

Every advance is recorded in the document's `auto_advance_log` (last 20) with
the phases, the timestamp and how many tasks were completed; the panel shows the
five most recent under **Auto-advance**.

### Starting a new architecture: what to get right

The panel will let you create something that never moves. These are the four
ways that happens, in the order they bite.

**1. Pick the template for the shape of the work, not its size.** The template
fixes which phases exist, and a phase the template did not select cannot be
added later — a task or a bulk update naming it is refused with `400`. `bugfix`
has no `review`; `prototype` has no `commit`. If in doubt, `feature` is the
honest default for work inside an existing codebase, and `full-16` for anything
that has to survive CI and release.

**2. Put tasks in the phase the agent will actually be run on.** A run only ever
sees the tasks of the *current* phase — that is the whole point of the phase
brief. An architecture with four tasks parked in `build` while the current phase
is `context` will produce a run that correctly reports "nothing to do here" and
stops. Check the header: the phase named under **Workflow** is the one that
matters.

**3. A phase with no tasks is a dead end.** Auto-advance refuses to move an
empty phase, because finishing nothing is not finishing something. So the
sequence *create → run* on a fresh architecture does nothing at all until you
add at least one task. This is also why advancing never cascades: the next phase
starts empty and holds there.

**4. The advance is evaluated on writes.** Nothing sweeps in the background. A
phase becomes complete at the moment a task report lands, and that same write
advances it. If a phase somehow ends up complete without a write — it finished
while paused, or it predates this feature — toggle *Auto-advance* off and on;
resuming re-evaluates and releases it.

Once it is running, the panel answers "why hasn't it moved" itself: the
**Auto-advance** section prints the gate that is holding, in words.

| Symptom | What it means |
|---|---|
| "the phase has no tasks" | add one, or advance manually |
| "N task(s) still open" | working as intended |
| "a task in this phase is blocked" | read that task's notes — the agent left them there |
| "`approve` is a manual phase" | your call; **Mark Phase Complete** |
| "auto-advance is paused" | the toggle in the header |
| "this is the last phase" | the architecture is done |
| blockers empty but nothing moved | the write that would have advanced it never happened — toggle off/on |

**A run does not create work.** It executes the tasks that are already there.
The loop is: you write the tasks → the agent does them and reports → the phase
advances itself → you add the next phase's tasks. The dashboard automates the
middle step, not the first one.

### Running an agent

▶ **Run** starts `claude -p` as a subprocess with the architecture's repo as its
working directory, supervised by a thread inside the dashboard. Nothing runs
automatically: creating an architecture and pointing an agent at a repo are
different decisions, so the wizard's *"run an agent on the first phase right
away"* box is off by default.

```
claude -p "<generated prompt>" --permission-mode acceptEdits     # cwd = architecture.repo
```

**The dashboard stays the single writer.** The agent does not touch the store
directly — it reports through the same HTTP API as the panel, so `_LOCK` and the
atomic writes keep working unchanged. The generated prompt says so, and states
two boundaries explicitly:

- **The agent edits tasks; humans advance phases.** Marking a phase complete is
  a decision made in the dashboard, never by the run.
- **Stuck three times on the same error means stop.** Write what was tried into
  the task's notes and exit, rather than grinding until the timeout.

One run at a time per architecture — a second start returns `409`. ✕ **Kill**
terminates the whole process group (`SIGTERM`, then `SIGKILL` after 5s), so a
child the agent spawned cannot outlive it and keep editing the repo.

While a run is in flight the panel polls: the architecture every 2.5s (task
status moves as the agent reports it) and the log every 1.5s, incrementally by
byte offset. Idle, it sweeps every 5s to catch edits made elsewhere. Polling
stops when you leave the detail view.

| State | Meaning |
|---|---|
| `running` | the process is alive |
| `done` | exited `0` |
| `failed` | non-zero exit, launch failure, or timeout |
| `killed` | stopped from the dashboard |
| `interrupted` | the dashboard restarted while the run was in flight |

Runs cannot survive the process that supervised them, so on startup the server
marks any still-`running` record whose pid is gone as `interrupted` rather than
leaving the architecture looking busy forever.

**Files**, under `~/.mem0/architectures/runs/<architecture id>/`:

| File | Contents |
|---|---|
| `<run id>.json` | the run record (status, pid, exit code, timings) |
| `<run id>.log` | stdout + stderr, append-only |
| `<run id>.prompt` | exactly what the agent was given |

| Env var | Default | Purpose |
|---|---|---|
| `MEM0_ARCH_RUN_TIMEOUT` | `1800` | seconds before a run is killed and marked failed |
| `MEM0_ARCH_CMD` | — | replaces the whole command, run via `sh -c`. Used by the tests to drive a full run lifecycle without ever launching a real agent |

**Security.** A run executes with your own permissions, in your repo, under
`--permission-mode acceptEdits` — it can change files without asking. It is
bounded by the timeout, the kill button, and being visible in the log while it
works; it is not sandboxed. The prompt builder writes no environment values into
the prompt, because the prompt and the log are plain files on disk: a secret that
reached them would be a secret written to disk. ▶ Run is disabled when `claude`
is not on `PATH`.

### Plan runs, do runs and audit runs

A run's mode is decided by what it finds:

| Phase has | Mode | What the run does |
|---|---|---|
| no tasks | **plan** | reads the repo and what earlier phases produced, writes 1–5 concrete tasks with acceptance criteria in `notes`, then **stops** |
| tasks, ordinary phase | **do** | executes them and reports each one |
| tasks, manual phase (`approve`, `release`, `learn`) | **audit** | checks each open task against the repository, writes a `PASS`/`FAIL`/`UNKNOWN` verdict into its `notes`, changes nothing, **sets no status** |

Deciding the work and doing it are separate runs on purpose. An agent that
invents a task and completes it in the same breath has reviewed nothing; the
split is what leaves you — or the next run — something to react to. A planning
run is told explicitly not to edit code, and that an empty plan ("this phase
already holds") is a real answer.

**Audit mode exists because "not yours to decide" is not the same as "nothing to
do".** A gate phase with tasks used to fall through to `do`, which handed the
agent a prompt telling it to mark those tasks `done` — a machine approving its
own work. It now gathers the evidence a person needs and stops; the statuses in
a gate phase belong to whoever is reviewing it. The panel offers **▶ Prepare
approval** instead of ▶ Run there, and says *approval brief ready — human
decision required* when the audit lands.

So the cycle is **plan → do → advance → plan**, with **plan → audit → you** at
every gate, and the panel labels each run `PLAN`, `DO` or `AUDIT`.

### Auto-chain

Off by default, per architecture, with its own confirmation: moving a phase is
bookkeeping, but launching an agent at your repo unattended is not, so it is a
separate switch from auto-advance.

With it on, a finished run starts the next one. It stops — and says which reason
in the run row — on any of these:

| Halt | Why |
|---|---|
| the run did not end `done` | a failure is where a person should look |
| the agent was refused for quota | **a rate or usage limit exits cleanly** and looks like a normal finish. The chain pauses rather than spending its cap on runs that were never going to start — and picks itself back up, see below |
| the phase is `approve` / `release` / `learn` | the manual gates hold for chains too |
| six runs deep | a bound on anything unattended; a run you start yourself resets it |
| two runs in a row moved nothing | no task created, none finished — a stall, not progress, whichever mode they ran in |
| auto-chain is off | the switch |

Chain state is counted back through the run history rather than stored on the
architecture, so the runner still writes nothing into the store — the dashboard
stays its single writer.

#### When the quota runs out

A refused run **did no work**, so there is nothing to restore: picking up means
starting the same phase again. The chain books itself a time and a watcher inside
the dashboard resumes it — you do not have to come back and press anything.

The wait backs off — **5m, 15m, 30m, 1h** — because a quota that just said no
rarely says yes a minute later. After the fourth refusal it stops asking: four
refusals is a situation, not a delay, and the run row says so instead of
retrying forever.

Everything is re-checked at the moment of resuming, not trusted from when the
refusal happened: auto-chain may have been switched off, the phase may have moved
to a manual one, or you may have started a run yourself. A refused run also does
not count against the six-run cap — charging it would let an outage spend the
budget meant for real work.

Resuming survives a dashboard restart: the appointment lives in the run record,
and the watcher starts with the server.

### Progress

`progress` is **phases finished plus how far into the current one**, over the
whole chain — not tasks alone. One finished task in phase 2 of 4 used to read as
100% complete; it now reads as 25%. It cannot reach 100% while any phase is
still open, so finishing the last phase's tasks shows 99% until the phase itself
is closed. The raw task fraction is still there as `task_progress`.

The advance log labels each entry: `→` for an advance, `↩` for a phase moved
backwards by hand. Two identical advances used to appear with no sign of the
rollback between them.

### One language for the records

Everything the system stores — task titles, notes, run summaries, memories — is
written in **English**, and both prompts say so. The repository keeps its own
conventions: its code, comments and commit messages are none of the dashboard's
business.

The reason is mechanical. mem0 retrieves by embedding similarity, and a store
written half in one language and half in another stops matching itself: the
memory bridge would get quietly worse at recall the more it remembered. This was
not hypothetical — a planning run on a Turkish repository wrote its entire plan
in Turkish while every other architecture's tasks were in English.

### Memory bridge

What a run learns is worth more to the *next* run than to the one that learned
it. Runs read the project's past lessons before they start and write their
outcome back when they finish, through this repo's own `mem.py` — the same
shared brain the CLI and the Memory panel use.

**Read.** Before a run starts, the phase name and its open task titles are used
as a query, scoped to the architecture's `project_id`; the top 5 hits go into
the prompt under *Past learnings for this project*, framed as evidence rather
than instructions — if a lesson contradicts the code, the code wins. No hits, or
an unreachable memory layer, means the section is omitted entirely: an empty
heading would claim "we know nothing", which is a different thing from "we could
not ask".

**Write.** When a run reaches a terminal state, one compact fact is stored:

```
arch=<id> phase=<phase> status=<status> duration=<s> | completed: <up to 5 task titles>
    | failed with: <last 3 log lines, only when the run failed>
```

Completing a phase writes one more: `phase build completed for mem0-local: 3
task(s); advanced to verify`.

Both are filed under `run_id = <project_id>`, `agent_id = arch-runner`, so a
project's runs share memory with everything else scoped to that project, and
`personal` folds in automatically on search.

**Nothing secret travels.** Summaries are built from task titles, statuses and
phases. A successful run's log body is never stored — only a *failed* run's last
three lines, because that is the part worth remembering. A test asserts an
environment value planted before a run never reaches a memory write.

**Fail-open, everywhere.** Ollama down, Qdrant down, `mem.py` missing, a hung
call, output that will not parse — each means "no memory this time" and nothing
more. A run's verdict is recorded before memory is touched, so the bridge cannot
change how a run ended. Calls are capped at 15 seconds, and writes triggered
while the architecture store is locked are handed to a daemon thread so a model
load never blocks another writer.

| Env var | Default | Purpose |
|---|---|---|
| `MEM0_ARCH_MEMORY` | `1` | `0` switches the bridge off entirely — no subprocess is spawned at all |
| `MEM0_ARCH_MEM_CMD` | — | replaces the memory CLI; the tests use it to exercise both sides without Ollama or Qdrant |

The run row in **Agent runs** says what happened: `memory: 3 learnings injected ·
write ok`, or `memory: off`.

### Migrating an older store

`migrate_architectures.py` converts documents written before this schema
(`name`/`description`/`workflow`, `in_progress` statuses, stored `progress`).
It is idempotent — running it twice produces a byte-identical file, because it
never stamps the clock — and validates a converted document against the live
module's enums *before* writing anything.

```bash
bin/mem0-python dashboard/migrate_architectures.py --dry-run   # report only
bin/mem0-python dashboard/migrate_architectures.py             # backs up, then converts
```

The backup lands in `~/.mem0/architectures/.backup-<timestamp>/`.

## Files

- `server.py` — stdlib HTTP server + JSON API + data readers.
- `architectures.py` — the only module that knows where architectures live: storage,
  validation, derived state. `server.py` routes; it never touches this store.
- `arch_templates.py` — the layer below it: the master phase catalog, the built-in
  templates, template validation, and user template files.
- `phase_playbook.json` — per-phase agents/skills/rules, inherited by templates.
- `runner.py` — agent runs: the prompt builder, the subprocess supervisor, and the
  run records. Reads `architectures.py`; nothing imports it back, so `server.py`
  composes run state onto the architecture response.
- `migrate_architectures.py` — idempotent converter for pre-v1 architecture files.
- `assets.py` — the inline, self-contained dashboard page (HTML/CSS/JS).
- `test_architectures.py`, `test_codemap.py` — stdlib `unittest`; run with
  `bin/mem0-python -m unittest discover dashboard`.
- `run.sh` — launch helper.
