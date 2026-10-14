#!/usr/bin/env python3
"""The architecture page — one architecture, rendered from live data.

A standalone page rather than another view inside the panel: an architecture is
the thing you sit with while an agent works, and it deserves a URL you can keep
open, reload, and link to. It reads the same API everything else does; it holds
no state of its own.

Self-contained by the same contract as assets.py: inline CSS and vanilla JS, no
CDN, no build step. Served at /architecture?id=<id>.

Visual language: dark, technical, quiet. Colour appears only to signal state —
teal for what is live or primary, amber for what wants attention, green for
what is finished, red for what destroys. Never solid fills except the single
primary action.
"""

ARCH_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>architecture</title>
<style>
:root{
  --bg:#0b0e12; --panel:#12161c; --panel-deep:#0e1317; --line:#242b34;
  --text:#e6edf3; --muted:#a5b1bc; --dim:#6b7683;
  --teal:#39c6c0; --amber:#e5a93d; --red:#e5534b; --green:#4bc26b;
  --teal-ink:#06282a; --btn:#151b22; --log:#0a0d10;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.5 var(--sans);
  -webkit-font-smoothing:antialiased}

/* The page lives inside the dashboard, not beside it: same shell, same nav. */
.shell{display:grid;grid-template-columns:210px 1fr;min-height:100vh}
.sidebar{border-right:1px solid var(--line);padding:22px 14px;display:flex;flex-direction:column;
  gap:5px;position:sticky;top:0;height:100vh;background:var(--panel-deep)}
.brand{display:flex;align-items:center;gap:9px;padding:2px 8px 18px}
.brand .glyph{font-size:20px}
.brand small{display:block;color:var(--dim);font:700 9.5px/1.4 var(--sans);
  letter-spacing:.14em;text-transform:uppercase}
.brand h1{font-size:14px;margin:0;letter-spacing:.01em;font-weight:650}
.nav-btn{appearance:none;border:1px solid transparent;background:transparent;color:var(--dim);
  text-align:left;font:inherit;font-weight:500;padding:8px 11px;border-radius:9px;cursor:pointer;
  display:flex;align-items:center;gap:10px;text-decoration:none;
  transition:background .15s ease,color .15s ease}
.nav-btn .ico{width:16px;text-align:center;opacity:.9;font-size:12px}
.nav-btn:hover{background:var(--btn);color:var(--text);text-decoration:none}
.nav-btn:focus-visible{outline:2px solid var(--teal);outline-offset:2px}
.nav-btn.on{background:rgba(57,198,192,.08);border-color:var(--line);color:var(--text);
  box-shadow:inset 3px 0 0 var(--teal)}
.nav-spacer{flex:1}
.nav-foot{color:var(--dim);font:11px/1 var(--mono);padding:8px;display:flex;align-items:center;gap:7px}
.nav-foot .pulse{width:7px;height:7px;border-radius:50%;background:var(--green);
  animation:pulse 2.4s ease-in-out infinite}

.main{padding:24px 22px 64px;min-width:0}
.wrap{max-width:1240px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
.back{display:inline-flex;align-items:center;gap:7px;font:11.5px/1 var(--mono);color:var(--dim);
  margin-bottom:2px}
.back:hover{color:var(--teal)}
a{color:var(--teal);text-decoration:none}
a:hover{text-decoration:underline}

.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
.label{font:700 11.5px/1.4 var(--sans);letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted);margin:0 0 12px}
.mono{font-family:var(--mono)}
.dim{color:var(--dim)}

.btn{font:inherit;color:var(--text);background:var(--btn);border:1px solid var(--line);
  border-radius:10px;padding:8px 13px;cursor:pointer;
  transition:border-color .15s ease,color .15s ease}
.btn:hover{border-color:#39424e}
.btn:focus-visible{outline:2px solid var(--teal);outline-offset:2px}
.btn[disabled]{opacity:.45;cursor:not-allowed}
.btn.primary{background:var(--teal);border-color:var(--teal);color:var(--teal-ink);font-weight:650}
.btn.primary:hover{filter:brightness(1.07)}
.btn.danger{background:transparent;border-color:rgba(229,83,75,.45);color:var(--red)}
.btn.danger:hover{border-color:var(--red)}
.btn.lg{padding:10px 18px}

.pill{display:inline-flex;align-items:center;gap:6px;font:11px/1 var(--mono);
  padding:5px 10px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.pill.teal{color:var(--teal);border-color:rgba(57,198,192,.45)}
.pill.amber{color:var(--amber);border-color:rgba(229,169,61,.45)}
.pill.green{color:var(--green);border-color:rgba(75,194,107,.45)}
.pill.red{color:var(--red);border-color:rgba(229,83,75,.45)}
.pill .dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.pill.live .dot{animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

.chip{font:11px/1 var(--mono);padding:5px 10px;border-radius:999px;border:1px solid var(--line);
  background:transparent;color:var(--muted);cursor:pointer;
  transition:border-color .15s ease,color .15s ease}
.chip:hover{border-color:#39424e;color:var(--text)}
.chip.on{border-color:rgba(57,198,192,.45);color:var(--teal)}
.chip:focus-visible{outline:2px solid var(--teal);outline-offset:2px}

.toggle{display:inline-flex;align-items:center;gap:9px;cursor:pointer;user-select:none}
.toggle input{position:absolute;opacity:0;width:0;height:0}
.track{width:34px;height:20px;border-radius:999px;background:#1a2029;border:1px solid var(--line);
  position:relative;transition:background .18s ease,border-color .18s ease}
.track::after{content:"";position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;
  background:var(--dim);transition:transform .18s ease,background .18s ease}
.toggle input:checked + .track{background:rgba(57,198,192,.12);border-color:rgba(57,198,192,.45)}
.toggle input:checked + .track::after{transform:translateX(14px);background:var(--teal)}
.toggle input:focus-visible + .track{outline:2px solid var(--teal);outline-offset:2px}
.toggle .name{font:11px/1 var(--mono);color:var(--muted);letter-spacing:.04em}

.head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.head h1{margin:0;font-size:20px;font-weight:650;letter-spacing:-.01em}
.head .meta{display:flex;gap:14px;flex-wrap:wrap;font:11.5px/1 var(--mono);color:var(--dim)}
.head .meta b{color:var(--muted);font-weight:400}

.hero{background:linear-gradient(180deg,rgba(57,198,192,.07),var(--panel) 55%);
  border-color:rgba(57,198,192,.28);display:flex;gap:24px;align-items:flex-start;
  justify-content:space-between;flex-wrap:wrap;padding:20px}
.hero.wait{background:linear-gradient(180deg,rgba(229,169,61,.07),var(--panel) 55%);
  border-color:rgba(229,169,61,.28)}
.hero .left{min-width:260px;flex:1 1 380px}
.hero h2{margin:10px 0 6px;font-size:17px;font-weight:600;letter-spacing:-.01em}
.hero p{margin:0;color:var(--muted);max-width:64ch}
.hero .actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}

.controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:0 2px}
.controls .spacer{flex:1 1 auto}
.confirm{display:none;margin:-6px 2px 0 auto;padding:13px 14px;max-width:440px;
  border:1px dashed rgba(229,83,75,.45);border-radius:10px;background:rgba(229,83,75,.08)}
.confirm.open{display:block}
.confirm p{margin:0 0 11px;color:var(--muted);font-size:13px}
.confirm .row{display:flex;gap:9px}

.grid{display:grid;grid-template-columns:5fr 7fr;gap:16px;align-items:start}
.col{display:flex;flex-direction:column;gap:16px}
.left-col{position:sticky;top:16px}

/* ---- the workflow: a spine, top to bottom ---- */
.spine{display:flex;flex-direction:column}
.node-row{display:grid;grid-template-columns:28px 1fr;gap:12px;align-items:start}
.rail{display:flex;flex-direction:column;align-items:center}
.node{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;flex:none;
  border:1px solid var(--line);background:var(--panel-deep);font:11px/1 var(--mono);color:var(--dim)}
.link{width:2px;flex:1 0 18px;background:var(--line);min-height:18px}
.step.done .node{border-color:rgba(75,194,107,.45);color:var(--green);background:rgba(75,194,107,.08)}
.step.done .link{background:rgba(75,194,107,.45)}
.step.active .node{border-color:rgba(229,169,61,.45);color:var(--amber);background:rgba(229,169,61,.12);
  box-shadow:0 0 0 6px rgba(229,169,61,.10)}
.step.skipped .node{border-style:dashed}
.step{display:grid;grid-template-columns:28px 1fr;gap:12px}
.step .info{padding:3px 0 16px;min-width:0}
.step:last-child .info{padding-bottom:0}
.nm{font:12px/1.3 var(--mono);color:var(--dim);letter-spacing:.02em;display:flex;gap:8px;align-items:baseline}
.step.done .nm{color:var(--muted)}
.step.active .nm{color:var(--amber)}
.nm .count{margin-left:auto;font-size:11px;color:var(--dim)}
.nm .manual{font-size:10px;color:var(--amber);border:1px solid rgba(229,169,61,.45);
  border-radius:999px;padding:2px 6px}
.step .loop{margin-top:4px;font:10.5px/1.4 var(--mono);color:var(--red);opacity:.85}

.strip{display:flex;gap:4px;margin-top:16px}
.seg{height:6px;border-radius:999px;background:#1a2029;flex:1 1 0}
.seg.done{background:var(--teal)}
.seg.current{background:var(--amber);animation:pulse 1.6s ease-in-out infinite}

.check{list-style:none;margin:0;padding:0}
.check li{display:flex;gap:11px;align-items:flex-start;padding:11px 0;
  border-bottom:1px dashed var(--line)}
.check li:last-child{border-bottom:0}
.ico{width:18px;height:18px;border-radius:50%;flex:none;margin-top:1px;display:grid;place-items:center;
  font:10px/1 var(--mono);border:1px solid var(--line);color:var(--dim);background:var(--panel-deep)}
.ico.done{border-color:rgba(75,194,107,.45);color:var(--green);background:rgba(75,194,107,.08)}
.ico.doing{border-color:rgba(229,169,61,.45);color:var(--amber);background:rgba(229,169,61,.12)}
.ico.blocked{border-color:rgba(229,83,75,.45);color:var(--red);background:rgba(229,83,75,.08)}
.check .body{min-width:0;flex:1 1 auto}
.check .t.done{color:var(--muted)}
/* An agent's notes are a paragraph, not a caption. Clamped to three lines so a
   list of five tasks stays a list; the whole thing is one click away. */
.check .sub{margin-top:4px;font:11px/1.55 var(--mono);color:var(--dim);overflow-wrap:anywhere;
  display:-webkit-box;-webkit-line-clamp:3;line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.check .sub.open{-webkit-line-clamp:unset;line-clamp:unset;overflow:visible}
.check .more{margin-top:4px;font:10.5px/1 var(--mono);color:var(--teal);background:none;
  border:0;padding:0;cursor:pointer}
.check .more:hover{text-decoration:underline}
.check .more:focus-visible{outline:2px solid var(--teal);outline-offset:2px}
.check .pri{font:10px/1 var(--mono);color:var(--dim);border:1px solid var(--line);
  border-radius:999px;padding:3px 6px;flex:none;margin-top:1px}
.check .pri.p0{color:var(--red);border-color:rgba(229,83,75,.45)}
.check .pri.p1{color:var(--amber);border-color:rgba(229,169,61,.45)}
.check select{font:11px/1 var(--mono);background:var(--btn);color:var(--muted);
  border:1px solid var(--line);border-radius:999px;padding:4px 7px;flex:none;margin-top:1px;cursor:pointer}

.empty{border:1px dashed var(--line);border-radius:10px;padding:16px;color:var(--dim);font-size:13px}

.addrow{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px;padding-top:14px;
  border-top:1px solid var(--line)}
.addrow input,.addrow select{font:inherit;background:var(--panel-deep);color:var(--text);
  border:1px solid var(--line);border-radius:10px;padding:8px 11px}
.addrow input{flex:1 1 180px;min-width:0}
.addrow select{font:11px/1 var(--mono);color:var(--muted);cursor:pointer}

pre.log{margin:0;background:var(--log);border:1px solid var(--line);border-radius:10px;padding:14px;
  font:12px/1.65 var(--mono);color:var(--muted);max-height:360px;overflow:auto;
  white-space:pre-wrap;overflow-wrap:anywhere}
pre.log .t{color:var(--teal)}
.logbar{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.logbar .spacer{flex:1 1 auto}
.runmeta{margin:10px 0 0;font:11px/1.5 var(--mono);color:var(--dim)}
.warn{margin-top:10px;padding:11px 13px;border-radius:10px;font-size:13px;color:var(--amber);
  border:1px dashed rgba(229,169,61,.45);background:rgba(229,169,61,.08)}

.pb{display:flex;flex-direction:column;gap:8px}
.pb-line{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.pb-line .k{flex:none;width:4.2rem;font:10.5px/1 var(--mono);text-transform:uppercase;
  letter-spacing:.06em;color:var(--dim)}

.ctx{display:flex;flex-direction:column}
.ctx-row{display:flex;gap:9px;align-items:baseline;padding:8px 0;
  border-bottom:1px dashed var(--line);font-size:13px}
.ctx-row:last-child{border-bottom:0}
.ctx-row .p{font:11px/1.4 var(--mono);color:var(--dim);overflow-wrap:anywhere}
.ctx-row.gone .n{color:var(--dim);text-decoration:line-through}
.ctx-row .miss{margin-left:auto;font:10px/1 var(--mono);color:var(--amber);flex:none}

details.hist{background:var(--panel);border:1px solid var(--line);border-radius:14px}
details.hist summary{list-style:none;cursor:pointer;padding:15px 18px;
  font:700 11.5px/1.4 var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
  display:flex;align-items:center;gap:10px}
details.hist summary::-webkit-details-marker{display:none}
details.hist summary::after{content:"+";margin-left:auto;font-family:var(--mono);color:var(--dim)}
details.hist[open] summary::after{content:"−"}
details.hist .inner{padding:0 18px 16px}
.hrow{display:flex;align-items:baseline;gap:12px;padding:9px 0;
  border-bottom:1px dashed var(--line);font:11.5px/1.5 var(--mono)}
.hrow:last-child{border-bottom:0}
.hrow .k{color:var(--green);flex:none;width:14px}
.hrow .k.back{color:var(--amber)}
.hrow .when{margin-left:auto;color:var(--dim)}

.toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,10px);background:var(--panel);
  border:1px solid rgba(57,198,192,.45);color:var(--text);border-radius:10px;padding:11px 16px;
  font:12px/1 var(--mono);opacity:0;pointer-events:none;
  transition:opacity .2s ease,transform .2s ease;z-index:50}
.toast.show{opacity:1;transform:translate(-50%,0)}
.toast.bad{border-color:rgba(229,83,75,.45)}

.loading{color:var(--dim);font:12px/1 var(--mono);padding:28px 0;text-align:center}

@media (max-width:960px){
  .shell{grid-template-columns:1fr}
  .sidebar{position:static;height:auto;flex-direction:row;flex-wrap:wrap;align-items:center;
    border-right:0;border-bottom:1px solid var(--line);padding:12px 14px}
  .brand{padding:0 12px 0 4px}
  .nav-spacer,.nav-foot{display:none}
  .nav-btn{padding:6px 9px}
  .main{padding:18px 16px 56px}
  .grid{grid-template-columns:1fr}
  .left-col{position:static}
  .hero{flex-direction:column}
  .hero .actions{width:100%}
}
</style>
</head>
<body>
<div class="shell">
  <nav class="sidebar" aria-label="Main navigation">
    <div class="brand"><span class="glyph">🧠</span>
      <div><small>Shared Brain</small><h1>Control Panel</h1></div></div>
    <a class="nav-btn" href="/#overview"><span class="ico">◎</span>Overview</a>
    <a class="nav-btn" href="/#projects"><span class="ico">📁</span>Projects</a>
    <a class="nav-btn" href="/#codemap"><span class="ico">🗺</span>Code Map</a>
    <a class="nav-btn on" href="/#architectures"><span class="ico">🏗</span>Architectures</a>
    <a class="nav-btn" href="/#memory"><span class="ico">🧠</span>Memory</a>
    <a class="nav-btn" href="/#skills"><span class="ico">✦</span>Skills</a>
    <a class="nav-btn" href="/#agents"><span class="ico">⌘</span>Agents</a>
    <a class="nav-btn" href="/#mcp"><span class="ico">⇄</span>MCP</a>
    <a class="nav-btn" href="/#rules"><span class="ico">§</span>Rules</a>
    <a class="nav-btn" href="/#hooks"><span class="ico">🪝</span>Hooks</a>
    <div class="nav-spacer"></div>
    <div class="nav-foot"><span class="pulse"></span>localhost:8899</div>
  </nav>
  <main class="main">
    <div class="wrap" id="root"><div class="loading">loading…</div></div>
  </main>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const esc = s => (s==null?"":String(s)).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const ID = new URLSearchParams(location.search).get('id') || '';
const POLL_RUNNING = 2500, POLL_IDLE = 5000, LOG_POLL = 1500;

let doc = null, timers = [], logView = { run:null, offset:0, text:'', filter:'all' };
let confirmOpen = false;                      // survives a repaint

async function api(path, opts){
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}
let toastTimer;
function toast(msg, bad){
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = 'toast', 2800);
}
const pct = n => Math.max(0, Math.min(100, Math.round((Number(n)||0) * 100)));
function ago(iso){
  if (!iso) return '';
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!isFinite(s)) return '';
  if (s < 60) return Math.max(0, Math.round(s)) + 's ago';
  if (s < 3600) return Math.round(s/60) + 'm ago';
  if (s < 86400) return Math.round(s/3600) + 'h ago';
  return Math.round(s/86400) + 'd ago';
}
function stopTimers(){ timers.forEach(clearInterval); timers = []; }

/* ---------- what the page is asking of you ---------- */
function heroFor(d){
  const run = d.run || {};
  const phase = d.current_phase;
  const manual = (d.manual_phases || []).includes(phase);
  const blockers = d.auto_advance_blockers || [];
  const own = (d.tasks || []).filter(t => t.phase === phase);
  const open = own.filter(t => t.status !== 'done');

  if (run.status === 'running'){
    return { tone:'', pill:['teal','live','agent working'],
      title:`An agent is working on ${esc(phase)}`,
      body:`Started ${esc(ago(run.started_at))}. It reports each task as it goes — this page follows along.`,
      actions:[{id:'kill', cls:'', text:'✕ Stop the run'}] };
  }
  if (run.limit_hit){
    return { tone:'wait', pill:['amber','','refused'],
      title:'The agent was refused and did no work',
      body:`${esc(run.limit_hit)}. ` + (run.resume_at
        ? `Picking up again by itself at ${esc(run.resume_at)} (attempt ${run.resume_attempt}).`
        : 'Refused too many times to keep retrying — start it again once the limit clears.'),
      actions:[{id:'run', cls:'primary', text:'▶ Try again now'}] };
  }
  if (manual){
    // What you are approving lives in the phase *before* this one — an approval
    // gate normally has no tasks of its own, and saying "0 tasks" here would
    // describe the gate rather than the work.
    const order = Object.keys(d.phases || {});
    const prev = order[order.indexOf(phase) - 1];
    const prevDone = prev ? (d.tasks || []).filter(t => t.phase === prev && t.status === 'done') : [];
    const canGoBack = order.indexOf(phase) > 0;
    const briefReady = run.mode === 'audit' && run.status === 'done';
    const actions = [];
    // The gate's own tasks are the checks somebody has to make. An agent can
    // gather the evidence for them; it may never tick them off, so this offers
    // a brief rather than a run.
    if (open.length) actions.push({id:'run', cls: briefReady ? '' : 'primary',
                                   text:'▶ Prepare approval'});
    if (canGoBack) actions.push({id:'back', cls:'', text:`← Send back to ${prev}`});
    actions.push({id:'advance', cls:'primary',
                  text: prevDone.length ? 'Approve & continue' : 'Mark complete & continue'});
    if (briefReady){
      return { tone:'wait', pill:['amber','live','your call'],
        title:'Approval brief ready — human decision required',
        body:`An agent checked ${open.length ? open.length + ' open task' + (open.length===1?'':'s') : 'this phase'} `
           + 'against the repository and wrote a verdict into each one’s notes. '
           + 'It marked nothing and changed nothing. Read the findings below, then approve or send it back.',
        actions };
    }
    return { tone:'wait', pill:['amber','live','waiting for you'],
      title: prevDone.length
        ? `${prevDone.length} finished task${prevDone.length===1?'':'s'} waiting on your approval`
        : `${esc(phase)} is yours to decide`,
      body: `The chain stopped here on purpose — ${esc(phase)} is a manual phase. `
          + 'A machine does not approve, release, or reflect on its own work.'
          + (open.length ? ' ▶ Prepare approval sends an agent to gather the evidence for'
                         + ' the checks below — it reports, you decide.' : ''),
      actions };
  }
  if (!own.length){
    return { tone:'', pill:['teal','','ready to plan'],
      title:`Nothing is written for ${esc(phase)} yet`,
      body:'A run starting here will read the repo and write this phase’s tasks, then stop. '
         + 'You can also add them yourself below.',
      actions:[{id:'run', cls:'primary', text:'▶ Plan this phase'}] };
  }
  return { tone:'', pill:['teal','', open.length + ' open'],
    title:`${open.length} task${open.length===1?'':'s'} left in ${esc(phase)}`,
    body: blockers.length
      ? 'Holding: ' + esc(blockers.join('; ')) + '.'
      : 'When the last one is done the phase moves on by itself.',
    actions:[{id:'run', cls:'primary', text:'▶ Run this phase'}] };
}

/* ---------- the workflow, top to bottom ---------- */
function spine(d){
  const order = Object.keys(d.phases || {});
  const manual = d.manual_phases || [];
  const loopsFrom = {};
  (d.loops || []).forEach(l => { (loopsFrom[l.from] = loopsFrom[l.from] || []).push(l); });
  const glyph = { done:'✓', active:'●', skipped:'⊘', pending:'○' };

  const rows = order.map((name, i) => {
    const p = d.phases[name] || {};
    const st = p.status || 'pending';
    const c = p.counts || {};
    const loops = (loopsFrom[name] || [])
      .map(l => `${esc(l.label || 'fail')} → ${esc(l.to)}`).join(' · ');
    return `<div class="step ${esc(st)}" data-phase="${esc(name)}">
      <div class="rail">
        <div class="node">${glyph[st] || '○'}</div>
        ${i < order.length - 1 ? '<div class="link"></div>' : ''}
      </div>
      <div class="info">
        <div class="nm">${esc(name)}
          ${manual.includes(name) ? '<span class="manual">manual</span>' : ''}
          <span class="count">${c.done||0}/${c.total||0}</span></div>
        ${loops ? `<div class="loop">↩ ${loops}</div>` : ''}
      </div>
    </div>`;
  }).join('');

  const segs = order.map(n => {
    const st = (d.phases[n]||{}).status;
    return `<i class="seg ${st==='done'||st==='skipped'?'done':(st==='active'?'current':'')}"></i>`;
  }).join('');

  return `<p class="label">Workflow</p><div class="spine">${rows}</div>
    <div class="strip" aria-hidden="true">${segs}</div>`;
}

/* ---------- tasks ---------- */
const STATUSES = ['todo','doing','blocked','done'];
const MARK = { done:'✓', doing:'●', blocked:'!', todo:'○' };

function checklist(d){
  const phase = d.current_phase;
  const own = (d.tasks || []).filter(t => t.phase === phase);
  const order = Object.keys(d.phases || {});
  const prev = order[order.indexOf(phase) - 1];
  const prevTasks = prev ? (d.tasks || []).filter(t => t.phase === prev) : [];
  const manual = (d.manual_phases || []).includes(phase);

  // At an approval gate the useful list is what you are approving, not an empty
  // checklist for the phase you are standing in.
  const showPrev = manual && !own.length && prevTasks.length;
  const list = showPrev ? prevTasks : own;
  const heading = showPrev ? `What you're approving · ${esc(prev)}` : `Tasks · ${esc(phase)}`;

  const rows = list.map(t => `<li data-task="${esc(t.id)}">
      <span class="ico ${esc(t.status)}">${MARK[t.status] || '○'}</span>
      <span class="body">
        <span class="t ${t.status==='done'?'done':''}">${esc(t.title)}</span>
        ${t.notes ? `<span class="sub">${esc(t.notes)}</span>${
          t.notes.length > 180 ? '<button type="button" class="more">show all</button>' : ''}` : ''}
      </span>
      <span class="pri ${esc(String(t.priority||'').toLowerCase())}">${esc(t.priority||'—')}</span>
      <select class="st" aria-label="Status for ${esc(t.title)}">
        ${STATUSES.map(s => `<option value="${s}"${s===t.status?' selected':''}>${s}</option>`).join('')}
      </select>
    </li>`).join('');

  const empty = showPrev
    ? `<p class="label" style="margin:20px 0 10px">${esc(phase)}</p>
       <div class="empty">No tasks in this phase. Approval is a judgement, not a checklist —
         the button above is the whole of it.</div>`
    : (!list.length
        ? `<div class="empty">Nothing written for this phase yet. Add one below, or let a
             planning run work it out.</div>` : '');

  return `<p class="label">${heading}</p>
    ${rows ? `<ul class="check">${rows}</ul>` : ''}
    ${empty}
    <div class="addrow">
      <input type="text" id="nt" placeholder="New task…" aria-label="Task title">
      <select id="np" aria-label="Priority">${['P0','P1','P2','P3','P4']
        .map(p => `<option${p==='P2'?' selected':''}>${p}</option>`).join('')}</select>
      <select id="nf" aria-label="Phase">${order
        .map(p => `<option${p===phase?' selected':''}>${esc(p)}</option>`).join('')}</select>
      <button class="btn" id="add">Add</button>
    </div>`;
}

/* ---------- run + log ---------- */
function runPanel(d){
  const runs = d.runs || [], run = d.run || {};
  if (!runs.length){
    return `<p class="label">Agent runs</p>
      <div class="empty">No runs yet. ▶ starts an agent in this repo for the current phase.</div>`;
  }
  const cur = logView.run || runs[0].id;
  const tone = { done:'green', failed:'red', killed:'red', running:'teal', interrupted:'amber' }[run.status] || '';
  const mem = run.memory_write === 'off' ? 'memory: off'
    : `memory: ${run.memory_count||0} injected${run.memory_write ? ' · write ' + esc(run.memory_write) : ''}`;
  return `<div class="logbar">
      <p class="label" style="margin:0">Agent runs</p>
      <span class="pill ${tone} ${run.status==='running'?'live':''} mono">
        ${run.status==='running'?'<span class="dot"></span>':''}${esc(run.status||'')}${
        run.exit_code!=null?' · exit '+run.exit_code:''}</span>
      ${run.mode ? `<span class="pill mono">${esc(run.mode)}</span>` : ''}
      <div class="spacer"></div>
      ${['all','messages','tools'].map(f =>
        `<button class="chip${logView.filter===f?' on':''}" data-filter="${f}">${f}</button>`).join('')}
      <button class="btn" id="relog">↻</button>
    </div>
    ${run.limit_hit ? `<div class="warn">The agent was refused (${esc(run.limit_hit)}) — this run did no
       work. ${run.resume_at ? 'Picking up again at ' + esc(run.resume_at) + '.'
                             : 'Start it again when the limit clears.'}</div>` : ''}
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
      ${runs.map(r => `<button class="chip${r.id===cur?' on':''}" data-run="${esc(r.id)}"
          title="${esc(r.status)}${r.chain_next?' · '+esc(r.chain_next):''}">${
          esc(r.id.replace('run-',''))} · ${esc(r.status)}${r.chained?' ⛓':''}</button>`).join('')}
    </div>
    <pre class="log" id="log">${esc(logView.text) || 'loading log…'}</pre>
    <p class="runmeta">${esc(mem)}${run.chain_next ? ' · chain: ' + esc(run.chain_next) : ''}</p>`;
}

function playbook(d){
  const book = (d.playbook || {})[d.current_phase] || {};
  const line = (k, arr) => `<div class="pb-line"><span class="k">${k}</span>${
    (arr && arr.length) ? arr.map(n => `<span class="pill mono">${esc(n)}</span>`).join('')
                        : '<span class="dim" style="font-size:12px">none</span>'}</div>`;
  return `<p class="label">Playbook · ${esc(d.current_phase)}</p>
    <div class="pb">${line('agents', book.agents)}${line('skills', book.skills)}${line('rules', book.rules)}</div>`;
}

function contextFiles(d){
  const ctx = d.context_files || [];
  if (!ctx.length) return '';
  const missing = ctx.filter(c => !c.exists).length;
  return `<p class="label">Context files ${missing ? `· ${missing} missing` : ''}</p>
    <div class="ctx">${ctx.map(c => `<div class="ctx-row ${c.exists?'':'gone'}">
      <span class="n">${esc(c.path.split('/').pop())}</span>
      <span class="p">${esc(c.path.replace(/\/[^/]*$/, ''))}</span>
      ${c.exists ? '' : '<span class="miss">missing</span>'}</div>`).join('')}</div>`;
}

function history(d){
  const log = (d.auto_advance_log || []).slice(-8).reverse();
  return `<details class="hist"><summary>Phase history</summary><div class="inner">
    ${log.length ? log.map(e => {
      const back = e.kind === 'rollback';
      return `<div class="hrow"><span class="k ${back?'back':''}">${back?'↩':'→'}</span>
        <span>${esc(e.from)} ${back?'←':'→'} ${esc(e.to)}</span>
        <span class="dim">${back ? 'moved back' : (e.tasks_completed||0) + ' task' + (e.tasks_completed===1?'':'s')}</span>
        <span class="when">${esc(ago(e.triggered_at))}</span></div>`;
    }).join('') : '<div class="empty">Nothing has moved by itself yet.</div>'}
  </div></details>`;
}

/* ---------- render ---------- */
function render(d){
  const hero = heroFor(d);
  $('#root').innerHTML = `
    <a class="back" href="/#architectures">← All architectures</a>
    <header class="head">
      <h1>${esc(d.title || d.id)}</h1>
      <span class="pill teal mono">${esc(d.template_id||'')}</span>
      <div class="meta">
        <span><b>repo</b> ${esc(d.repo||'')}</span>
        <span><b>scope</b> ${esc(d.project_id||'')}</span>
        <span><b>phase</b> ${Object.keys(d.phases||{}).indexOf(d.current_phase)+1}/${Object.keys(d.phases||{}).length}</span>
        <span><b>${pct(d.progress)}%</b> complete</span>
      </div>
    </header>

    <section class="card hero ${hero.tone}">
      <div class="left">
        <span class="pill ${hero.pill[0]} ${hero.pill[1]}">${hero.pill[1]==='live'?'<span class="dot"></span>':''}${esc(hero.pill[2])}</span>
        <h2>${hero.title}</h2>
        <p>${hero.body}</p>
      </div>
      <div class="actions">${hero.actions.map(a =>
        `<button class="btn ${a.cls} lg" data-act="${a.id}">${esc(a.text)}</button>`).join('')}</div>
    </section>

    <div class="controls">
      <label class="toggle"><input type="checkbox" id="auto"${d.auto_advance_disabled?'':' checked'}>
        <span class="track"></span><span class="name">auto-advance</span></label>
      <label class="toggle"><input type="checkbox" id="chain"${d.auto_chain?' checked':''}>
        <span class="track"></span><span class="name">auto-chain</span></label>
      ${d.run && d.run.status === 'running'
        ? '<button class="btn" data-act="kill">✕ Kill</button>'
        : `<button class="btn" data-act="run">${
            (d.manual_phases || []).includes(d.current_phase)
              ? '▶ Prepare approval' : '▶ Run'}</button>`}
      <div class="spacer"></div>
      <button class="btn danger" id="del">Delete</button>
    </div>
    <div class="confirm${confirmOpen?' open':''}" id="delc">
      <p>Delete <span class="mono">${esc(d.title||d.id)}</span> and its ${(d.counts||{}).total||0}
        task(s)? The run history goes with it. This cannot be undone.</p>
      <div class="row"><button class="btn danger" id="delyes">Delete it</button>
        <button class="btn" id="delno">Keep</button></div>
    </div>

    <div class="grid">
      <div class="col left-col"><section class="card">${spine(d)}</section></div>
      <div class="col">
        <section class="card">${checklist(d)}</section>
        <section class="card">${playbook(d)}</section>
        <section class="card">${runPanel(d)}</section>
        ${contextFiles(d) ? `<section class="card">${contextFiles(d)}</section>` : ''}
      </div>
    </div>
    ${history(d)}`;
  wire(d);
}

/* ---------- behaviour ---------- */
function phaseOrder(d){ return Object.keys(d.phases || {}); }

async function act(name){
  const d = doc, order = phaseOrder(d), i = order.indexOf(d.current_phase);
  try {
    if (name === 'run'){
      await api('/api/architecture/run', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:ID})});
      logView = { run:null, offset:0, text:'', filter:logView.filter };
      toast('run started');
    } else if (name === 'kill'){
      await api('/api/architecture/run/kill', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:ID})});
      toast('run stopped');
    } else if (name === 'advance' || name === 'back'){
      const target = name === 'advance' ? i + 1 : i - 1;
      if (target < 0 || target >= order.length) return;
      const phases = {};
      order.forEach((p, n) => phases[p] = { status: n < target ? 'done' : (n === target ? 'active' : 'pending') });
      await api('/api/architecture/workflow', {method:'PUT',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:ID, phases})});
      toast(`now on ${order[target]}`);
    }
    await load({quiet:true});
  } catch(e){ toast(e.message, true); }
}

function wire(d){
  $$('#root [data-act]').forEach(b => b.addEventListener('click', () => act(b.dataset.act)));

  $('#auto').addEventListener('change', async e => {
    try {
      await api('/api/architecture/' + (e.target.checked ? 'resume-auto' : 'pause-auto'),
        {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:ID})});
      toast('auto-advance ' + (e.target.checked ? 'on' : 'off'));
      await load({quiet:true});
    } catch(err){ toast(err.message, true); }
  });
  $('#chain').addEventListener('change', async e => {
    if (e.target.checked && !confirm('Let this architecture start its own runs?\n\n'
        + 'Each finished run launches the next, up to 6, stopping at a manual phase or a failure.')){
      e.target.checked = false; return;
    }
    try {
      await api('/api/architecture/chain', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({id:ID, on:e.target.checked})});
      toast('auto-chain ' + (e.target.checked ? 'on' : 'off'));
      await load({quiet:true});
    } catch(err){ toast(err.message, true); }
  });

  // Destructive confirmation lives inline, under the button that opened it.
  $('#del').addEventListener('click', () => {
    confirmOpen = $('#delc').classList.toggle('open');
    if (confirmOpen) $('#delyes').focus();
  });
  $('#delno').addEventListener('click', () => {
    confirmOpen = false; $('#delc').classList.remove('open'); $('#del').focus();
  });
  $('#delyes').addEventListener('click', async () => {
    try {
      await api('/api/architecture?id=' + encodeURIComponent(ID), {method:'DELETE'});
      stopTimers();
      $('#root').innerHTML = '<div class="loading">deleted — returning to the list…</div>';
      setTimeout(() => location.href = '/#architectures', 700);
    } catch(e){ toast(e.message, true); }
  });

  $$('#root .check .more').forEach(btn => btn.addEventListener('click', () => {
    const sub = btn.previousElementSibling;
    const open = sub.classList.toggle('open');
    btn.textContent = open ? 'show less' : 'show all';
  }));

  $$('#root .check .st').forEach(sel => sel.addEventListener('change', async () => {
    const id = sel.closest('li').dataset.task;
    try {
      const res = await api('/api/architecture/task', {method:'PATCH',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({arch:ID, id, status:sel.value})});
      if (res.auto_advanced) toast(`auto-advanced: ${res.auto_advanced.from} → ${res.auto_advanced.to}`);
      await load({quiet:true});
    } catch(e){ toast(e.message, true); await load({quiet:true}); }
  }));

  const add = async () => {
    const title = $('#nt').value.trim();
    if (!title) return toast('the task needs a title', true);
    try {
      await api('/api/architecture/task', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({arch:ID, phase:$('#nf').value, title, priority:$('#np').value})});
      toast('added');
      await load({quiet:true});
    } catch(e){ toast(e.message, true); }
  };
  if ($('#add')) $('#add').addEventListener('click', add);
  if ($('#nt')) $('#nt').addEventListener('keydown', e => { if (e.key === 'Enter') add(); });

  $$('#root .chip[data-run]').forEach(b => b.addEventListener('click', async () => {
    logView = { run:b.dataset.run, offset:0, text:'', filter:logView.filter };
    await tail();
  }));
  $$('#root .chip[data-filter]').forEach(b => b.addEventListener('click', () => {
    logView.filter = b.dataset.filter;
    $$('#root .chip[data-filter]').forEach(c => c.classList.toggle('on', c === b));
    paintLog();
  }));
  if ($('#relog')) $('#relog').addEventListener('click', async () => {
    logView.offset = 0; logView.text = ''; await tail();
  });

  // Clicking a phase scrolls its tasks into view — the spine is a map, not a label.
  $$('#root .step').forEach(s => s.addEventListener('click', () => {
    const el = $('#root .check') || $('#root .empty');
    if (el) el.scrollIntoView({behavior:'smooth', block:'center'});
  }));

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && confirmOpen){
      confirmOpen = false; $('#delc').classList.remove('open');
    }
  }, {once:true});
}

/* ---------- log tail ---------- */
function paintLog(){
  const pre = $('#log');
  if (!pre) return;
  const lines = logView.text.split('\n');
  const keep = logView.filter === 'all' ? lines
    : lines.filter(l => logView.filter === 'tools' ? l.startsWith('·') : !l.startsWith('·'));
  pre.innerHTML = keep.map(l =>
    l.startsWith('·') ? `<span class="t">${esc(l)}</span>` : esc(l)).join('\n')
    || esc(waiting());
}
function waiting(){
  const run = (doc && doc.run) || {};
  if (run.status !== 'running') return '(this run produced no output)';
  const s = run.started_at ? (Date.now() - Date.parse(run.started_at))/1000 : 0;
  const m = Math.floor(s/60), sec = Math.floor(s%60);
  return `running for ${m?m+'m ':''}${sec}s — waiting for the agent's first message`;
}
async function tail(){
  if (!doc || !(doc.runs||[]).length) return;
  const run = logView.run || doc.runs[0].id;
  logView.run = run;
  try {
    const data = await api(`/api/architecture/run/log?id=${encodeURIComponent(ID)}`
      + `&run=${encodeURIComponent(run)}&offset=${logView.offset}`);
    if (data.offset < logView.offset) logView.text = '';
    logView.text += data.text || '';
    logView.offset = data.offset;
    const box = $('#log');
    if (box){
      const bottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      paintLog();
      if (bottom && data.text) box.scrollTop = box.scrollHeight;
    }
  } catch(e){ /* a blip keeps what we have */ }
}

/* ---------- load + poll ---------- */
async function load(opts={}){
  if (!ID){
    $('#root').innerHTML = '<div class="empty">No architecture id in the URL. '
      + 'Try <span class="mono">/architecture?id=&lt;id&gt;</span>.</div>';
    return;
  }
  try {
    doc = await api('/api/architecture?id=' + encodeURIComponent(ID));
  } catch(e){
    if (opts.quiet) return;                       // a blip must not wipe the page
    $('#root').innerHTML = `<div class="empty">Could not load ${esc(ID)}: ${esc(e.message)}</div>`;
    return;
  }
  document.title = (doc.title || doc.id) + ' · architecture';
  const y = window.scrollY;
  render(doc);
  if (opts.quiet) window.scrollTo(0, y);
  await tail();
  schedule();
}
function schedule(){
  stopTimers();
  const running = doc && doc.run && doc.run.status === 'running';
  timers.push(setInterval(() => load({quiet:true}), running ? POLL_RUNNING : POLL_IDLE));
  if (running) timers.push(setInterval(tail, LOG_POLL));
}
load();
</script>
</body>
</html>
"""
