"""
API client
    POST /api/v1/events   one raw telemetry event per request
    GET  /api/v1/state    polled every 2 s (the graph iframe polls it directly)
    POST /api/v1/chat     multi-turn follow-up with the grounded agent session
    POST /api/v1/reset    closes the current incident
"""

from __future__ import annotations

import html
import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components
from ui_logic import choose_source, describe_events, parse_events, uncovered_chains
from ui_text import split_answer

API_URL = os.getenv("DEPLOYD_API_URL", "http://localhost:8000").rstrip("/")
PUBLIC_API_URL = os.getenv("DEPLOYD_PUBLIC_API_URL", API_URL).rstrip("/")
SEND_DELAY_S = 0.5
POLL_INTERVAL_S = 2.0
HISTORY_INTERVAL_S = 5.0
HISTORY_LIMIT = 20
TICK_S = 0.25
CHAT_TIMEOUT_S = 120.0
TYPEWRITER_DELAY_S = 0.018

_ROOT = Path(__file__).resolve().parent.parent

_SEV_COLORS = {
    "INFO": "#3b82f6",
    "WARNING": "#f59e0b",
    "ERROR": "#ef4444",
    "CRITICAL": "#a855f7",
}
_STATUS_STYLE = {
    "Healthy": ("#10b981", "●"),
    "Degrading": ("#f59e0b", "▲"),
    "Critical": ("#ef4444", "■"),
}
_TIER_STYLE = {
    "FULL": ("#10b981", "Tier 3 · FULL"),
    "CHAIN_ONLY": ("#f59e0b", "Tier 2 · CHAIN ONLY"),
    "INCONCLUSIVE": ("#64748b", "Tier 1 · INCONCLUSIVE"),
}
_DECISION_STYLE = {
    "ACCEPT": ("✓ Approved", "#10b981"),
    "AMEND": ("✎ Approved with changes", "#f59e0b"),
    "REJECT": ("✗ Rejected", "#ef4444"),
}
_CONFIDENCE_COLORS = {"high": "#10b981", "medium": "#f59e0b", "low": "#ef4444"}
_AVATAR_USER = ":material/person:"
_AVATAR_AGENT = ":material/smart_toy:"
_AVATAR_GATE = ":material/shield:"
_AVATAR_ENGINE = ":material/account_tree:"

_EXAMPLE = json.dumps(
    [
        {
            "timestamp": "2026-09-30T10:00:00Z",
            "source": "auth-service",
            "event_type": "DEPLOY_START",
            "metadata": {"version": "v2.1.0"},
            "description": "Deploy v2.1.0 started",
        },
        {
            "timestamp": "2026-09-30T10:00:40Z",
            "source": "auth-service",
            "event_type": "MEMORY_SAMPLE",
            "metadata": {"memory_percent": 97},
            "description": "Memory usage 97%",
        },
    ],
    indent=2,
)

st.set_page_config(page_title="DeployD — Live", page_icon="🔎", layout="wide")

st.markdown(
    """
    <style>
    .stApp, [data-testid="stHeader"] { background: #0F172A !important; }
    .stApp, .stApp p, .stApp label, .stApp span, .stApp li { color: #e2e8f0; }
    .stApp h1, .stApp h2, .stApp h3, .stApp h4 { color: #f8fafc !important; }
    .block-container { padding-top: 2.2rem !important; }
    .stApp textarea, .stApp [data-testid="stFileUploaderDropzone"] {
        background: #1e293b !important; color: #e2e8f0 !important;
        border-color: #334155 !important;
    }
    .stApp .stButton > button[kind="secondary"] {
        background: #1e293b !important; color: #e2e8f0 !important;
        border: 1px solid #334155 !important;
    }
    .stApp [data-testid="stPopoverButton"] {
        background: #1e293b !important; color: #e2e8f0 !important;
        border: 1px solid #334155 !important;
    }
    .stApp .stButton > button[kind="secondary"]:hover { border-color: #6366f1 !important; }
    .stApp .stButton > button[kind="secondary"]:disabled { color: #64748b !important; }
    .stApp [data-testid="stFileUploaderDropzone"] button {
        background: #334155 !important; color: #e2e8f0 !important;
    }
    .stApp [data-testid="stChatInput"], .stApp [data-testid="stChatInput"] > div {
        background: #1e293b !important; border-color: #334155 !important;
    }
    .stApp [data-testid="stChatInput"] textarea { background: transparent !important; }
    .stApp [data-testid="stChatMessage"] {
        background: #1e293b !important; border: 1px solid #263244; border-radius: 12px;
    }
    .stApp [data-testid="stChatMessage"] h1, .stApp [data-testid="stChatMessage"] h2,
    .stApp [data-testid="stChatMessage"] h3 { font-size: 1rem !important; margin: .4rem 0 .2rem; }
    .stApp [data-testid="stExpander"] details {
        background: #0f172a !important; border-color: #334155 !important;
    }
    .dp-card {
        background: #1e293b; border: 1px solid #334155; border-radius: 10px;
        padding: 0.8rem 1rem; margin-bottom: 0.6rem;
    }
    .dp-label {
        font-size: 0.7rem; font-weight: 700; letter-spacing: 0.08em;
        text-transform: uppercase; color: #94a3b8 !important;
    }
    .dp-badge {
        display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
        font-size: 0.72rem; font-weight: 700; letter-spacing: 0.04em; margin-right: .3rem;
    }
    .dp-chip {
        display: inline-block; padding: 0.1rem 0.5rem; border-radius: 6px; margin: .1rem .2rem 0 0;
        font-family: 'JetBrains Mono', monospace; font-size: 0.72rem;
        background: #0f172a; border: 1px solid #334155; color: #a5b4fc !important;
    }
    .dp-feed { max-height: 300px; overflow-y: auto; }
    .dp-ev {
        font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
        padding: 0.3rem 0.5rem; border-left: 3px solid; margin-bottom: 0.25rem;
        background: #0f172a; border-radius: 0 4px 4px 0;
    }
    .dp-muted { color: #64748b !important; font-size: 0.85rem; }
    .dp-ready {
        background: #10b98114; border: 1px solid #10b98155; border-radius: 8px;
        padding: .45rem .6rem; margin: .3rem 0; font-size: .82rem; color: #a7f3d0 !important;
    }
    .dp-warn {
        background: rgba(245,158,11,0.12); border: 1px solid #f59e0b;
        border-radius: 8px; padding: 0.5rem 0.8rem; font-size: 0.85rem; margin: .3rem 0;
    }
    .dp-event {
        text-align: center; font-size: 0.78rem; color: #94a3b8 !important;
        border-top: 1px dashed #334155; padding-top: .4rem; margin: .4rem 0 .6rem;
    }
    .dp-gate { border-left: 3px solid #f59e0b; padding-left: .6rem; }
    .dp-reco {
        background: rgba(99,102,241,0.10); border: 1px solid #4f46e5; border-radius: 8px;
        padding: .5rem .7rem; margin-top: .5rem; font-size: .9rem;
    }
    .dp-approve { color: #fbbf24 !important; font-size: .75rem; font-weight: 700; }
    .dp-typing i {
        display: inline-block; width: 7px; height: 7px; margin-right: 4px; border-radius: 50%;
        background: #94a3b8; animation: dp-blink 1.2s infinite ease-in-out;
    }
    .dp-typing i:nth-child(2) { animation-delay: .2s; }
    .dp-typing i:nth-child(3) { animation-delay: .4s; }
    @keyframes dp-blink { 0%, 80%, 100% { opacity: .2; } 40% { opacity: 1; } }
    .dp-trace-row {
        display: flex; justify-content: space-between; gap: .5rem; align-items: baseline;
        padding: .35rem 0; border-bottom: 1px solid #263244; font-size: .83rem;
    }
    .dp-trace-row:last-child { border-bottom: none; }
    .dp-trace-key { color: #94a3b8 !important; white-space: nowrap; }
    .dp-trace-val { text-align: right; }
    .dp-bar { position: relative; height: 8px; background: #0f172a; border-radius: 4px; margin: .35rem 0; }
    .dp-bar > div { height: 8px; border-radius: 4px; }
    .dp-bar > i { position: absolute; top: -3px; width: 2px; height: 14px; background: #f8fafc; }
    .dp-mini { font-family: 'JetBrains Mono', monospace; font-size: .72rem; color: #94a3b8 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

_DEFAULTS: dict[str, Any] = {
    "queue": [],
    "last_send": 0.0,
    "last_poll": 0.0,
    "sent": [],
    "state": None,
    "api_error": None,
    "scope_warning": None,
    "pending": None,
    "seen_messages": None,
    "pending_decision": None,
    "decision_error": None,
    "input_gen": 0,
    "last_sent": None,
    "history": [],
    "last_history": 0.0,
    "history_error": None,
    "viewing": None,
    "past": None,
    "engineer": "on-call engineer",
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = list(_v) if isinstance(_v, list) else _v


def _api(method: str, path: str, **kwargs: Any) -> Any:
    """Call the backend; returns the decoded JSON or raises httpx.HTTPError."""
    timeout = kwargs.pop("timeout", 10.0)
    resp = httpx.request(method, f"{API_URL}{path}", timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp.json()


def _poll_state() -> None:
    try:
        st.session_state.state = _api("GET", "/api/v1/state")
        st.session_state.api_error = None
    except httpx.HTTPError as exc:
        st.session_state.api_error = f"Cannot reach API at {API_URL}: {exc}"
    st.session_state.last_poll = time.monotonic()


def _send_event(event: dict[str, Any]) -> None:
    record: dict[str, Any] = {"event": event, "severity": None, "error": None}
    try:
        resp = _api("POST", "/api/v1/events", json=event)
        record["severity"] = resp.get("provisional_severity")
    except httpx.HTTPStatusError as exc:
        record["error"] = f"HTTP {exc.response.status_code}: {exc.response.text[:120]}"
    except httpx.HTTPError as exc:
        record["error"] = str(exc)
    st.session_state.sent.append(record)
    st.session_state.last_send = time.monotonic()


def _rebase_timestamps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shift timestamps so the last event is 'now', keeping the relative spacing.

    Correlation uses event time, so this is cosmetic: times and incident durations look current.
    """
    try:
        stamps = [
            datetime.fromisoformat(str(e["timestamp"]).replace("Z", "+00:00")) for e in events
        ]
    except ValueError:
        return events
    if not stamps:
        return events
    shift = datetime.now(timezone.utc) - max(stamps)
    return [
        {**e, "timestamp": (ts + shift).isoformat().replace("+00:00", "Z")}
        for e, ts in zip(events, stamps, strict=True)
    ]


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _badge(text: str, color: str) -> str:
    return (
        f'<span class="dp-badge" style="background:{color}22;color:{color};'
        f'border:1px solid {color}">{_esc(text)}</span>'
    )


def _tier_badge(tier: str | None) -> str:
    if not tier:
        return _badge("Gate not evaluated", "#64748b")
    color, label = _TIER_STYLE.get(tier, ("#64748b", tier))
    return _badge(label, color)


@st.cache_data
def _known_components() -> frozenset[str]:
    try:
        with (_ROOT / "data" / "components.json").open(encoding="utf-8") as f:
            return frozenset(json.load(f))
    except (OSError, json.JSONDecodeError):
        return frozenset()


def _scope_warning(prompt: str, investigated: str | None, incident: set[str]) -> str | None:
    """Warn when the question names a known component outside this investigation."""
    in_scope = {investigated} if investigated else incident
    if not in_scope:
        return None
    text = prompt.lower()
    foreign = sorted(c for c in _known_components() - in_scope if c.lower() in text)
    if not foreign:
        return None
    return (
        f"⚠️ This investigation covers <b>{_esc(', '.join(sorted(in_scope)))}</b>. "
        f"You asked about <b>{_esc(', '.join(foreign))}</b> — the agent stays on the "
        "investigated component; another component needs its own investigation."
    )


def _typewriter(text: str) -> Iterator[str]:
    for word in text.split(" "):
        yield word + " "
        time.sleep(TYPEWRITER_DELAY_S)


_GRAPH_HTML = """
<!doctype html><html><head><style>
  * { box-sizing: border-box; }
  html, body { margin: 0; background: #0F172A; font-family: Inter, system-ui, sans-serif; color: #e2e8f0; }
  #wrap { position: relative; height: 230px; overflow: auto; }
  #canvas { position: relative; }
  #wrap::-webkit-scrollbar { height: 6px; width: 6px; }
  #wrap::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  #wrap::-webkit-scrollbar-track { background: transparent; }
  svg { position: absolute; left: 0; top: 0; overflow: visible; }
  .node {
    position: absolute; width: 184px; padding: 9px 11px 9px 13px; border-radius: 10px;
    background: #1e293b; border: 1px solid #334155; border-left: 4px solid var(--sev);
    box-shadow: 0 4px 14px rgba(0,0,0,.25); cursor: default;
    transition: left .35s ease, top .35s ease;
  }
  .node.new { animation: pop .5s ease; }
  @keyframes pop { from { opacity: 0; transform: translateY(6px) scale(.97); } to { opacity: 1; } }
  .node.root { border-color: #f87171; border-left-color: #f87171; box-shadow: 0 0 0 1px #f87171, 0 4px 18px rgba(248,113,113,.25); }
  .node.isolated { opacity: .55; }
  .type { font-size: 13px; font-weight: 600; color: #f8fafc; }
  .meta { font-size: 11px; color: #94a3b8; margin-top: 2px; }
  .desc { font-size: 11px; color: #64748b; margin-top: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tags { display: flex; gap: 5px; margin-bottom: 5px; }
  .tag { font-size: 9px; font-weight: 700; letter-spacing: .06em; padding: 1px 6px; border-radius: 999px; }
  .edge-label {
    position: absolute; transform: translate(-50%, -50%); font-size: 10px; font-weight: 600;
    padding: 1px 7px; border-radius: 999px; background: #0F172A; border: 1px solid; white-space: nowrap;
  }
  #empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #64748b; font-size: 13px; }
  #legend { position: absolute; left: 0; right: 0; bottom: 0; display: flex; gap: 14px; justify-content: flex-end;
            padding: 4px 8px; font-size: 11px; color: #64748b; background: linear-gradient(transparent, #0F172A 40%); }
  #legend i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; }
  #err { position: absolute; left: 8px; bottom: 4px; color: #f87171; font-size: 11px; }
</style></head><body>
<div id="wrap"><div id="canvas"><svg id="edges"></svg></div></div>
<div id="empty">Graph is empty — send events to build it.</div>
<div id="legend">
  <span><i style="background:#3b82f6"></i>info</span><span><i style="background:#f59e0b"></i>warning</span>
  <span><i style="background:#ef4444"></i>error</span><span><i style="background:#a855f7"></i>critical</span>
  <span><i style="background:transparent;border:1px solid #f87171"></i>root cause</span>
  <span style="color:#f59e0b">━ causal</span><span>┄ temporal</span>
</div>
<div id="err"></div>
<script>
const API = "__API__";
const SNAPSHOT = __SNAPSHOT__;  // null = live: poll the API; else draw this incident once
const SEV = {INFO: "#3b82f6", WARNING: "#f59e0b", ERROR: "#ef4444", CRITICAL: "#a855f7"};
const TYPES = {
  STATE_CHANGE: "State change", RESOURCE_EXHAUSTION: "Resource exhaustion",
  DEPENDENCY_FAILURE: "Dependency failure", CONNECTIVITY_LOSS: "Connectivity loss",
  HEALTH_CHECK_FAIL: "Health check failed", HEALTH_CHECK_PASS: "Health check passed",
  PROCESS_CRASH: "Process crash", CONFIG_CHANGE: "Config change",
  DEPLOY_STARTED: "Deploy started", DEPLOY_COMPLETED: "Deploy completed", DEPLOY_FAILED: "Deploy failed",
};
const W = 184, H = 92, GAP_X = 82, GAP_Y = 22, PAD = 16;
const seen = new Set();
let last = "";

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const human = t => TYPES[t] || t.replace(/_/g, " ").toLowerCase().replace(/^./, c => c.toUpperCase());
function shortRule(rule) {
  const m = /^RULE-([0-9]+)/.exec(rule || "");
  return m ? "R" + m[1] : (rule || "").replace(/^explicit:/, "");
}

function layout(g) {
  // Longest-path layering over all edges (Kahn); nodes in a cycle fall back to column 0.
  const inDeg = {}, out = {}, col = {};
  g.nodes.forEach(n => { inDeg[n.id] = 0; out[n.id] = []; col[n.id] = 0; });
  g.edges.forEach(e => { if (e.source in out && e.target in inDeg) { out[e.source].push(e.target); inDeg[e.target]++; } });
  const queue = g.nodes.filter(n => inDeg[n.id] === 0).map(n => n.id);
  while (queue.length) {
    const id = queue.shift();
    out[id].forEach(t => { col[t] = Math.max(col[t], col[id] + 1); if (--inDeg[t] === 0) queue.push(t); });
  }
  const cols = {};
  [...g.nodes].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    .forEach(n => (cols[col[n.id]] = cols[col[n.id]] || []).push(n.id));
  const pos = {};
  let maxRows = 1;
  Object.entries(cols).forEach(([c, ids]) => {
    maxRows = Math.max(maxRows, ids.length);
    ids.forEach((id, i) => pos[id] = {x: PAD + c * (W + GAP_X), y: PAD + i * (H + GAP_Y)});
  });
  // Vertically centre short columns against the tallest one.
  Object.values(cols).forEach(ids => {
    const offset = (maxRows - ids.length) * (H + GAP_Y) / 2;
    ids.forEach(id => pos[id].y += offset);
  });
  const width = PAD * 2 + (Math.max(0, ...Object.keys(cols).map(Number)) + 1) * (W + GAP_X) - GAP_X;
  const height = PAD * 2 + maxRows * (H + GAP_Y) - GAP_Y;
  return {pos, width, height};
}

function render(g) {
  const canvas = document.getElementById("canvas"), svg = document.getElementById("edges");
  document.getElementById("empty").style.display = g.nodes.length ? "none" : "flex";
  canvas.querySelectorAll(".node, .edge-label").forEach(el => el.remove());
  if (!g.nodes.length) { svg.innerHTML = ""; seen.clear(); return; }

  const {pos, width, height} = layout(g);
  const wrap = document.getElementById("wrap");
  // Shrink wide graphs to fit (down to 70%), then centre; beyond that the view scrolls.
  const scale = Math.max(0.7, Math.min(1, wrap.clientWidth / width, (wrap.clientHeight - 24) / height));
  canvas.style.transform = `scale(${scale})`;
  // transform does not shrink the layout box: only allow scrolling when it truly overflows
  wrap.style.overflow = width * scale > wrap.clientWidth + 1 ? "auto" : "hidden";
  canvas.style.transformOrigin = "0 0";
  const dx = Math.max(0, (wrap.clientWidth / scale - width) / 2);
  const dy = Math.max(0, ((wrap.clientHeight - 24) / scale - height) / 2);

  const causal = g.edges.filter(e => e.relationship === "CAUSAL");
  const hasIn = new Set(causal.map(e => e.target)), hasOut = new Set(causal.map(e => e.source));
  const linked = new Set(g.edges.flatMap(e => [e.source, e.target]));

  let paths = `<defs>
    <marker id="a-c" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#f59e0b"/></marker>
    <marker id="a-t" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#64748b"/></marker></defs>`;
  g.edges.forEach(e => {
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) return;
    const isCausal = e.relationship === "CAUSAL";
    const x1 = a.x + W + dx, y1 = a.y + H / 2 + dy, x2 = b.x + dx - 2, y2 = b.y + H / 2 + dy;
    const cx = (x2 - x1) / 2;
    const color = isCausal ? "#f59e0b" : "#64748b";
    paths += `<path d="M${x1},${y1} C${x1 + cx},${y1} ${x2 - cx},${y2} ${x2},${y2}" fill="none"
      stroke="${color}" stroke-width="${isCausal ? 2.2 : 1.4}" ${isCausal ? "" : 'stroke-dasharray="5 4"'}
      marker-end="url(#${isCausal ? "a-c" : "a-t"})"/>`;
    const label = document.createElement("div");
    label.className = "edge-label";
    label.style.left = (x1 + x2) / 2 + "px";
    label.style.top = (y1 + y2) / 2 + "px";
    label.style.color = color; label.style.borderColor = color + "88";
    label.textContent = shortRule(e.rule_id) + " · " + Math.round(e.confidence * 100) + "%";
    label.title = (e.rule_id || e.relationship) + " — " + e.relationship.toLowerCase() + " edge, confidence " + Math.round(e.confidence * 100) + "%";
    canvas.appendChild(label);
  });
  canvas.style.width = Math.max(width, wrap.clientWidth / scale) + "px";
  canvas.style.height = Math.max(height, (wrap.clientHeight - 24) / scale) + "px";
  svg.setAttribute("width", canvas.style.width); svg.setAttribute("height", canvas.style.height);
  svg.innerHTML = paths;

  g.nodes.forEach(n => {
    const p = pos[n.id], sev = SEV[n.severity] || "#64748b";
    const isRoot = hasOut.has(n.id) && !hasIn.has(n.id);
    const el = document.createElement("div");
    el.className = "node" + (isRoot ? " root" : "") + (linked.has(n.id) ? "" : " isolated") + (seen.has(n.id) ? "" : " new");
    el.style.setProperty("--sev", sev);
    el.style.left = p.x + dx + "px"; el.style.top = p.y + dy + "px";
    el.title = n.severity + " · " + n.type + " — " + (n.label || "");
    const tags = [`<span class="tag" style="color:${sev};background:${sev}22">${esc(n.severity)}</span>`];
    if (isRoot) tags.unshift('<span class="tag" style="color:#fca5a5;background:#f8717122">ROOT CAUSE</span>');
    el.innerHTML = `<div class="tags">${tags.join("")}</div>
      <div class="type">${esc(human(n.type))}</div>
      <div class="meta">${esc(n.source || "unknown")} · ${esc(n.timestamp.slice(11, 19))}</div>
      <div class="desc">${esc(n.label)}</div>`;
    canvas.appendChild(el);
    seen.add(n.id);
  });
}

async function poll() {
  if (SNAPSHOT) {
    const key = JSON.stringify(SNAPSHOT);
    if (key !== last) { last = key; render(SNAPSHOT); }
    return;
  }
  try {
    const s = await (await fetch(API + "/api/v1/state")).json();
    document.getElementById("err").textContent = "";
    const g = s.graphs || {nodes: [], edges: []};
    const key = JSON.stringify(g);
    if (key !== last) { last = key; render(g); }
  } catch (err) {
    document.getElementById("err").textContent = "API unreachable at " + API;
  }
}
window.addEventListener("resize", () => { last = ""; poll(); });
poll(); if (!SNAPSHOT) setInterval(poll, 2000);
</script></body></html>
"""


def _render_graph(snapshot: dict[str, Any] | None = None) -> None:
    """The causal graph: live (the page polls the API) or a past incident's snapshot."""
    st.markdown('<div class="dp-label">Causal graph</div>', unsafe_allow_html=True)
    # "</" would end the <script> early: event text is untrusted.
    data = json.dumps(snapshot).replace("</", "<\\/") if snapshot is not None else "null"
    page = _GRAPH_HTML.replace("__API__", PUBLIC_API_URL).replace("__SNAPSHOT__", data)
    components.html(page, height=235)


def _trace_row(key: str, value: str) -> str:
    return (
        f'<div class="dp-trace-row"><span class="dp-trace-key">{key}</span>'
        f'<span class="dp-trace-val">{value}</span></div>'
    )


def _score_bar(score: float, threshold: float) -> str:
    color = "#10b981" if score >= threshold else "#ef4444"
    return (
        f'<div class="dp-bar"><div style="width:{min(score, 1) * 100:.0f}%;background:{color}">'
        f'</div><i style="left:{threshold * 100:.0f}%" title="threshold"></i></div>'
    )


def _render_trace(
    trace: dict[str, Any] | None, investigating: bool, status: str, past: bool = False
) -> None:
    st.markdown('<div class="dp-label">Decision trace</div>', unsafe_allow_html=True)
    if trace is None:
        if past:
            body = "No decision trace was recorded for this incident."
        elif investigating:
            body = "Running the three-tier gate…"
        elif status == "Critical":
            body = "Incident is CRITICAL — the gate is about to run."
        else:
            body = (
                "The gate runs automatically when the incident reaches CRITICAL "
                "(causal chain of 2+ hops, A → B → C). Until then: no LLM, zero tokens."
            )
        st.markdown(f'<div class="dp-card dp-muted">{body}</div>', unsafe_allow_html=True)
        return

    ok, ko = '<span style="color:#10b981">✓</span>', '<span style="color:#ef4444">✗</span>'
    hops = trace["causal_chain_length"]
    rules = " → ".join(_esc(r) for r in trace["rules_fired"]) or "—"
    rows = [
        _trace_row("Component", f"<b>{_esc(trace['component'])}</b>"),
        _trace_row("Causal chain", f"{ok if hops else ko} {hops} hops"),
        _trace_row("Rules fired", f'<span class="dp-mini">{rules}</span>'),
    ]

    best = trace.get("best_match")
    if best:
        b = best["score_breakdown"]
        mark = ok if best["above_threshold"] else ko
        margin = trace.get("margin_to_runner_up")
        runner = (
            f'<div class="dp-mini">margin to runner-up: {margin:+.2f}</div>'
            if margin is not None
            else ""
        )
        rows.append(
            _trace_row(
                "Best match",
                f'{mark} <span class="dp-chip">{_esc(best["runbook_id"])}</span>',
            )
            + _score_bar(best["score"], trace["threshold"])
            + f'<div class="dp-mini" style="text-align:right">'
            f"{best['score']:.2f} / {trace['threshold']:.2f} threshold<br>"
            f"Sem {b['semantic']:.2f} · BM25 {b['bm25']:.2f} · "
            f"Caus {b['causal']:.2f} · Comp {b['component']:.2f}</div>{runner}"
        )
    else:
        rows.append(_trace_row("Best match", f"{ko} none retrieved"))

    rows.append(_trace_row("Tier", _tier_badge(trace["tier"])))
    if trace["llm_called"] and not trace.get("llm_error"):
        tokens = trace.get("tokens_used")
        prompt = trace.get("prompt_version")
        model = trace.get("model")
        version = (f" · {_esc(model)}" if model else "") + (
            f" · prompt v{_esc(prompt)}" if prompt else ""
        )
        notes = []
        if trace.get("citations_removed"):
            removed = ", ".join(trace["citations_removed"])
            notes.append(f"validator removed invented citations: {removed}")
        if trace.get("answer_discarded"):
            notes.append(f"answer discarded: {trace['answer_discarded']}")
        rows.append(
            _trace_row("LLM called", f"{ok} {tokens or '?'} tokens{version}")
            + "".join(f'<div class="dp-mini">{_esc(n)}</div>' for n in notes)
        )
    else:
        error = trace.get("llm_error") or ""
        reason = (
            "agent failed (LLM provider rate limit)"
            if "rate_limit" in error
            else error[:120] or trace.get("reason_llm_skipped") or ""
        )
        rows.append(_trace_row("LLM called", ko) + f'<div class="dp-mini">{_esc(reason)}</div>')

    decided = trace.get("human_decision")
    if decided:
        label, color = _DECISION_STYLE.get(decided["decision"], (decided["decision"], "#64748b"))
        rows.append(
            _trace_row(
                "Human decision",
                f'<span style="color:{color}">{label}</span> · {_esc(decided["engineer"])} '
                f'<span class="dp-mini">v{decided["version"]}</span>',
            )
        )

    st.markdown(f'<div class="dp-card">{"".join(rows)}</div>', unsafe_allow_html=True)


def _render_message(msg: dict[str, str], animate: bool) -> None:
    kind = msg.get("kind") or ("user" if msg.get("role") == "user" else "followup")
    content = msg.get("content", "")

    if kind == "event":
        st.markdown(
            f'<div class="dp-event">{_esc(content).replace("**", "")}</div>', unsafe_allow_html=True
        )
        return
    if kind == "user":
        with st.chat_message("user", avatar=_AVATAR_USER):
            st.markdown(content)
        return
    if kind == "decision":
        label, color = _DECISION_STYLE.get(msg.get("decision", ""), ("Decision", "#64748b"))
        with st.chat_message("user", avatar=_AVATAR_USER):
            st.markdown(
                f"{_badge(label, color)}"
                f'<span class="dp-mini">answer v{_esc(msg.get("version", ""))}</span>',
                unsafe_allow_html=True,
            )
            st.markdown(content)
        return
    if kind in ("gate", "error"):
        with st.chat_message("assistant", avatar=_AVATAR_GATE):
            label = "Gate — LLM not called" if kind == "gate" else "Agent error"
            st.markdown(
                f'<div class="dp-gate"><div class="dp-label">{label}</div></div>',
                unsafe_allow_html=True,
            )
            st.markdown(content)
        return
    if kind == "deterministic":
        with st.chat_message("assistant", avatar=_AVATAR_ENGINE):
            st.markdown(
                f'{_tier_badge(msg.get("tier"))}<span class="dp-muted">deterministic result · '
                f"LLM not called</span>",
                unsafe_allow_html=True,
            )
            st.markdown(content)
            if msg.get("chain"):
                st.markdown(
                    f'<div class="dp-mini">{_esc(msg["chain"])}</div>', unsafe_allow_html=True
                )
            if msg.get("reason"):
                st.caption(f"Why no LLM answer: {msg['reason']}")
        return

    with st.chat_message("assistant", avatar=_AVATAR_AGENT):
        conf = msg.get("confidence", "")
        if conf:
            color = _CONFIDENCE_COLORS.get(conf.lower(), "#64748b")
            chips = "".join(
                f'<span class="dp-chip">{_esc(e)}</span>'
                for e in msg.get("evidence", "").split(",")
                if e
            )
            tier = _tier_badge("FULL") if kind == "diagnosis" else ""
            if kind == "revision":
                tier = _badge(f"Revised answer · v{msg.get('version', '')}", "#8b5cf6")
            st.markdown(
                f"{tier}{_badge(f'{conf} confidence', color)}"
                + (f'<div style="margin-top:.3rem">Grounded in {chips}</div>' if chips else ""),
                unsafe_allow_html=True,
            )
        if kind == "diagnosis":
            st.markdown("**Root cause**")
        preview, rest = split_answer(content)
        if animate:
            st.write_stream(_typewriter(preview))
        else:
            st.markdown(preview)
        if rest:
            with st.expander("Show more"):
                st.markdown(rest)
        if kind == "diagnosis":
            st.markdown(
                f'<div class="dp-reco"><b>Recommendation</b><br>{_esc(msg.get("recommendation", ""))}'
                f'<div class="dp-approve">⚠ Requires human approval — DeployD suggests, '
                f"engineers approve.</div></div>",
                unsafe_allow_html=True,
            )
            if msg.get("reasoning"):
                with st.expander("Reasoning"):
                    st.markdown(msg["reasoning"])
        meta = []
        if msg.get("turn"):
            meta.append(f"follow-up turn {msg['turn']}")
        if msg.get("tokens"):
            meta.append(f"{msg['tokens']} tokens")
        if meta:
            st.caption(" · ".join(meta))


def _suggestions(component: str) -> list[str]:
    return [
        "Why this root cause and not another?",
        "What should I check first?",
        f"We already rolled back {component} — does that change your diagnosis?",
    ]


def _render_review(review: dict[str, Any]) -> None:
    """DID-38: the engineer's verdict on the answer under review. Nothing is executed."""
    ss = st.session_state
    v = review["version"]
    st.markdown(
        f'<div class="dp-label">Your decision on answer v{v} · as {_esc(ss.engineer)}</div>',
        unsafe_allow_html=True,
    )
    approve, reject, amend = st.columns(3)
    if approve.button("✓ Approve", key=f"approve-{v}", use_container_width=True, type="primary"):
        ss.pending_decision = {"decision": "ACCEPT"}
    with reject.popover("✗ Reject", use_container_width=True, key=f"reject-box-{v}"):
        reason = st.text_input("Why is it wrong?", key=f"reason-{v}")
        if st.button("Send to the agent", key=f"reject-{v}", disabled=not reason.strip()):
            ss.pending_decision = {"decision": "REJECT", "comment": reason}
    with amend.popover("✎ Edit & approve", use_container_width=True, key=f"amend-box-{v}"):
        edited = st.text_area("Recommendation", value=review["recommendation"], key=f"amend-{v}")
        if st.button("Approve edited", key=f"amend-ok-{v}", disabled=not edited.strip()):
            ss.pending_decision = {"decision": "AMEND", "amended_recommendation": edited}
    if ss.pending_decision:
        st.rerun(scope="fragment")


def _send_decision(incident_id: str | None) -> None:
    ss = st.session_state
    revising = ss.pending_decision["decision"] == "REJECT"
    with st.chat_message("assistant", avatar=_AVATAR_AGENT):
        st.markdown(
            '<div class="dp-typing"><i></i><i></i><i></i><span class="dp-muted"> '
            + ("the agent is revising its answer…" if revising else "recording the decision…")
            + "</span></div>",
            unsafe_allow_html=True,
        )
    ss.decision_error = None
    try:
        _api(
            "POST",
            f"/api/v1/incidents/{incident_id}/decision",
            json={**ss.pending_decision, "engineer": ss.engineer},
            timeout=CHAT_TIMEOUT_S,
        )
    except httpx.HTTPStatusError as exc:
        ss.decision_error = f"Decision refused: {exc.response.json().get('detail', exc)}"
    except httpx.HTTPError as exc:
        ss.decision_error = f"Decision failed: {exc}"
    ss.pending_decision = None
    _poll_state()
    st.rerun(scope="fragment")


def _render_chat(state: dict[str, Any], incident_components: set[str]) -> None:
    ss = st.session_state
    chat: list[dict[str, str]] = state.get("chat_history") or []
    session = state.get("session")
    trace = state.get("decision_trace")
    status = state.get("tracker_status", "Healthy")

    if session:
        header = (
            f"<b>DeployD agent</b> · {_esc(session['component'])} · "
            f"session <span class='dp-mini'>{_esc(session['id'])}</span> · "
            f"turn {session['turn']}/{session['max_turns']}"
        )
    elif trace:
        header = f"<b>Deterministic investigation</b> · {_esc(trace['component'])}"
    else:
        header = "<b>Investigation</b> · waiting for trigger"
    st.markdown(
        f'<div class="dp-label">Investigation chat</div>'
        f'<div style="margin:.2rem 0 .4rem;font-size:.85rem">{header}</div>',
        unsafe_allow_html=True,
    )

    if ss.seen_messages is None:
        ss.seen_messages = len(chat)
    elif ss.seen_messages > len(chat):
        ss.seen_messages = 0

    box = st.container(height=440)
    with box:
        if not chat and not ss.pending:
            if status == "Healthy":
                msg = (
                    "Waiting for investigation trigger… No causal evidence yet — "
                    "the LLM is not called."
                )
            elif status == "Degrading":
                msg = (
                    "Anomalies detected, causal chain shorter than 2 hops. "
                    "Deterministic only until the incident is CRITICAL."
                )
            else:
                msg = "Incident is CRITICAL — investigation starting…"
            st.markdown(f'<div class="dp-muted">{msg}</div>', unsafe_allow_html=True)
        for i, m in enumerate(chat):
            animate = i >= ss.seen_messages and m.get("kind") in (
                "diagnosis",
                "followup",
                "revision",
            )
            _render_message(m, animate)
        ss.seen_messages = len(chat)

        if state.get("investigating") and not ss.pending:
            with st.chat_message("assistant", avatar=_AVATAR_AGENT):
                st.markdown(
                    '<div class="dp-typing"><i></i><i></i><i></i>'
                    '<span class="dp-muted"> running the gate and diagnosing…</span></div>',
                    unsafe_allow_html=True,
                )

        if ss.pending:
            prompt = ss.pending
            with st.chat_message("user", avatar=_AVATAR_USER):
                st.markdown(prompt)
            with st.chat_message("assistant", avatar=_AVATAR_AGENT):
                st.markdown(
                    '<div class="dp-typing"><i></i><i></i><i></i>'
                    '<span class="dp-muted"> thinking…</span></div>',
                    unsafe_allow_html=True,
                )
            try:
                _api("POST", "/api/v1/chat", json={"prompt": prompt}, timeout=CHAT_TIMEOUT_S)
            except httpx.HTTPError as exc:
                st.error(f"Chat failed: {exc}")
            ss.pending = None
            _poll_state()
            st.rerun(scope="fragment")

        if ss.pending_decision:
            _send_decision(state.get("incident_id"))

    if ss.decision_error:
        st.error(ss.decision_error)
    review = state.get("review")
    if review and review["status"] == "pending" and not ss.pending and not ss.pending_decision:
        _render_review(review)

    if ss.scope_warning:
        st.markdown(f'<div class="dp-warn">{ss.scope_warning}</div>', unsafe_allow_html=True)

    investigated = session["component"] if session else (trace or {}).get("component")
    can_follow_up = bool(session) and session["turn"] < session["max_turns"]
    if can_follow_up and not ss.pending:
        cols = st.columns(3)
        for col, text in zip(cols, _suggestions(session["component"]), strict=False):
            if col.button(text, use_container_width=True, key=f"sugg-{text}"):
                ss.scope_warning = _scope_warning(text, investigated, incident_components)
                ss.pending = text
                st.rerun(scope="fragment")

    placeholder = (
        "Ask the agent a follow-up about this incident…"
        if can_follow_up
        else "Ask about this incident (the gate decides whether the LLM answers)…"
    )
    busy = bool(ss.pending) or bool(state.get("investigating"))
    prompt = st.chat_input(placeholder, disabled=busy)
    if prompt:
        ss.scope_warning = _scope_warning(prompt, investigated, incident_components)
        ss.pending = prompt
        st.rerun(scope="fragment")


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "open"
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _fetch_history() -> None:
    ss = st.session_state
    try:
        ss.history = _api("GET", "/api/v1/incidents")
        ss.history_error = None
    except httpx.HTTPError as exc:
        ss.history_error = f"Cannot load past incidents: {exc}"
    ss.last_history = time.monotonic()


def _open_incident(incident_id: str) -> None:
    ss = st.session_state
    try:
        ss.past = _api("GET", f"/api/v1/incidents/{incident_id}")
        ss.viewing = incident_id
        ss.history_error = None
    except httpx.HTTPError as exc:
        ss.history_error = f"Cannot open the incident: {exc}"


def _return_to_live() -> None:
    ss = st.session_state
    ss.viewing, ss.past = None, None
    _poll_state()


def _render_history() -> None:
    """Past (resolved) incidents; opening one shows it read-only on the right."""
    ss = st.session_state
    st.markdown('<div class="dp-label">Past incidents</div>', unsafe_allow_html=True)
    if ss.history_error:
        st.caption(ss.history_error)
    past = [i for i in ss.history if i["status"] == "RESOLVED"][:HISTORY_LIMIT]
    if not past:
        st.markdown(
            '<div class="dp-muted">None yet: resetting an incident archives it here.</div>',
            unsafe_allow_html=True,
        )
        return
    with st.container(height=280):
        for inc in past:
            sev = inc["peak_severity"]
            color = "#6366f1" if inc["id"] == ss.viewing else _SEV_COLORS.get(sev, "#64748b")
            decision = _DECISION_STYLE.get(inc.get("last_decision") or "")
            summary = (inc.get("root_cause_summary") or "no investigation").strip()
            st.markdown(
                f'<div class="dp-ev" style="border-color:{color}">'
                f'<span style="color:{color}">{_esc(sev)}</span> · '
                f'{_esc(inc["opened_at"][:16].replace("T", " "))} · '
                f'{_fmt_duration(inc["duration_seconds"])}'
                f'{_tier_badge(inc.get("tier"))}'
                + (f' <span style="color:{decision[1]}">{decision[0]}</span>' if decision else "")
                + f'<br><span class="dp-muted">{_esc(summary[:110])}</span></div>',
                unsafe_allow_html=True,
            )
            if st.button("Open", key=f"open-{inc['id']}", use_container_width=True):
                _open_incident(inc["id"])
                st.rerun(scope="fragment")


def _render_uninvestigated(graph: dict[str, Any], trace: dict[str, Any] | None) -> None:
    """DeployD investigates one chain per incident: say so when the graph holds another."""
    if not trace or not trace.get("chain_components"):
        return
    left_out = uncovered_chains(graph, trace["chain_components"])
    if not left_out:
        return
    chains = "; ".join(" → ".join(chain) for chain in left_out)
    plural = "chains were" if len(left_out) > 1 else "chain was"
    st.markdown(
        f'<div class="dp-warn"><b>{len(left_out)} more causal {plural} not investigated:</b> '
        f"{_esc(chains)}.<br>DeployD runs one investigation per incident, on the first "
        f"CRITICAL chain ({_esc(trace['component'])}). Press <b>Reset incident</b> and send "
        f"the rest on its own to investigate it.</div>",
        unsafe_allow_html=True,
    )


def _render_past(detail: dict[str, Any]) -> None:
    """A past incident, read-only: its graph, decision trace and chat as they were."""
    banner, back = st.columns([8, 3], gap="small")
    banner.markdown(
        f'<div class="dp-warn">Viewing past incident <b>{_esc(detail["id"][:8])}</b> · '
        f'{_esc(detail["peak_severity"])} · read-only. The live incident keeps running in the '
        f"background.</div>",
        unsafe_allow_html=True,
    )
    if back.button("↩ Return to Live", type="primary", use_container_width=True):
        _return_to_live()
        st.rerun(scope="fragment")

    _render_graph(detail.get("graph_snapshot") or {"nodes": [], "edges": []})
    chat_col, trace_col = st.columns([64, 36], gap="medium")
    with trace_col:
        _render_trace(detail.get("decision_trace"), False, detail["peak_severity"], past=True)
    with chat_col:
        st.markdown(
            '<div class="dp-label">Investigation chat</div>'
            '<div style="margin:.2rem 0 .4rem;font-size:.85rem"><b>Past incident</b> · read-only</div>',
            unsafe_allow_html=True,
        )
        with st.container(height=440):
            chat = detail.get("chat_history") or []
            if not chat:
                st.markdown(
                    '<div class="dp-muted">No messages were recorded for this incident.</div>',
                    unsafe_allow_html=True,
                )
            for message in chat:
                _render_message(message, False)


st.markdown(
    '<div style="display:flex;align-items:baseline;gap:.6rem;margin-bottom:.6rem">'
    '<span style="font-size:1.6rem;font-weight:700;color:#f8fafc">DeployD</span>'
    '<span class="dp-muted" style="font-size:.95rem">live incident investigation</span></div>',
    unsafe_allow_html=True,
)


@st.fragment(run_every=TICK_S)
def live_console() -> None:
    ss = st.session_state
    now = time.monotonic()

    sent_now = False
    if ss.queue and now - ss.last_send >= SEND_DELAY_S:
        _send_event(ss.queue.pop(0))
        sent_now = True
    if sent_now or ss.state is None or now - ss.last_poll >= POLL_INTERVAL_S:
        _poll_state()
    if now - ss.last_history >= HISTORY_INTERVAL_S:
        _fetch_history()

    state: dict[str, Any] = ss.state or {}
    graph = state.get("graphs") or {"nodes": [], "edges": []}
    status = state.get("tracker_status", "Healthy")
    trace = state.get("decision_trace")
    incident_components = {n["source"] for n in graph["nodes"] if n.get("source")}

    if ss.api_error:
        st.error(ss.api_error)

    left, right = st.columns([3, 7], gap="medium")

    with left:
        color, icon = _STATUS_STYLE.get(status, ("#64748b", "●"))
        st.markdown(
            f'<div class="dp-card"><div class="dp-label">Incident status</div>'
            f'{_badge(f"{icon} {status.upper()}", color)}{_tier_badge((trace or {}).get("tier"))}'
            f'<div class="dp-muted" style="margin-top:.4rem">'
            f'{len(graph["nodes"])} nodes · {len(graph["edges"])} edges</div></div>',
            unsafe_allow_html=True,
        )

        st.text_input("Engineer on call", key="engineer", help="Recorded with every decision.")

        st.markdown('<div class="dp-label">Event ingestion</div>', unsafe_allow_html=True)
        # A new key empties both inputs after every send (a file would otherwise be sent again).
        upload = st.file_uploader(
            "Upload events JSON",
            type=["json"],
            label_visibility="collapsed",
            key=f"upload-{ss.input_gen}",
        )
        pasted = st.text_area(
            "Or paste events JSON", height=140, placeholder=_EXAMPLE, key=f"paste-{ss.input_gen}"
        )

        source = choose_source(
            upload.name if upload else None,
            upload.getvalue().decode("utf-8", "replace") if upload else None,
            pasted,
        )
        events: list[dict[str, Any]] = []
        if source is None:
            st.caption("Nothing to send yet: upload a file or paste events.")
        else:
            try:
                events = parse_events(source.raw)
                st.markdown(
                    f'<div class="dp-ready">✓ Ready to send <b>{_esc(source.label)}</b><br>'
                    f'<span class="dp-muted">{_esc(describe_events(events))}</span></div>',
                    unsafe_allow_html=True,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                st.error(f"{source.label}: {exc}")
            if source.ignored_paste:
                st.warning(
                    "A file and pasted text are both present: only the file is sent. "
                    "Remove one of them."
                )

        rebase = st.checkbox(
            "Replay: rebase timestamps to now",
            value=True,
            help="Keeps the spacing between events. Correlation works either way; "
            "this only makes times and incident durations look current.",
        )
        can_send = bool(events) and not ss.queue
        c1, c2 = st.columns(2)
        one_by_one = c1.button("Send one by one", use_container_width=True, disabled=not can_send)
        send_all = c2.button(
            "Send all", use_container_width=True, disabled=not can_send, type="primary"
        )

        if (one_by_one or send_all) and source is not None:
            batch = _rebase_timestamps(events) if rebase else events
            ss.last_sent = {"label": source.label, "count": len(batch)}
            if one_by_one:
                ss.queue = batch
            else:
                for ev in batch:
                    _send_event(ev)
                _poll_state()
            ss.input_gen += 1
            st.rerun(scope="fragment")

        if ss.last_sent:
            st.caption(f"Last sent: {ss.last_sent['label']} · {ss.last_sent['count']} event(s)")

        if ss.queue:
            st.caption(f"Sending… {len(ss.queue)} event(s) left")

        if st.button("Reset incident", use_container_width=True):
            try:
                _api("POST", "/api/v1/reset")
            except httpx.HTTPError as exc:
                st.error(f"Reset failed: {exc}")
            ss.queue, ss.sent, ss.scope_warning, ss.pending = [], [], None, None
            ss.viewing, ss.past = None, None
            ss.input_gen += 1
            ss.last_sent = None
            _poll_state()
            _fetch_history()
            st.rerun(scope="fragment")

        _render_history()

        st.markdown('<div class="dp-label">Live event feed</div>', unsafe_allow_html=True)
        rows = []
        for rec in reversed(ss.sent):
            ev = rec["event"]
            sev = rec["severity"] or "ERROR"
            c = "#64748b" if rec["error"] else _SEV_COLORS.get(sev, "#64748b")
            tag = f"REJECTED {rec['error']}" if rec["error"] else sev
            rows.append(
                f'<div class="dp-ev" style="border-color:{c}">'
                f'<span style="color:{c}">{_esc(tag)}</span> · '
                f'{_esc(ev["source"])} · {_esc(ev["event_type"])}'
                f'<br><span class="dp-muted">{_esc(ev.get("description", ""))}</span></div>'
            )
        feed = "".join(rows) or '<div class="dp-muted">No events sent yet.</div>'
        st.markdown(f'<div class="dp-feed">{feed}</div>', unsafe_allow_html=True)

    with right:
        if ss.past:
            _render_past(ss.past)
        else:
            _render_uninvestigated(graph, trace)
            _render_graph()
            chat_col, trace_col = st.columns([64, 36], gap="medium")
            with trace_col:
                _render_trace(trace, bool(state.get("investigating")), status)
            with chat_col:
                _render_chat(state, incident_components)


live_console()
