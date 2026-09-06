"""
demo/app.py — DID-UI: DeployD Investigation Demo

Visualises one incident investigation end-to-end:
  event stream → FSM → IncidentGraph → CausalEngine → Retrieval → Orchestrator → Diagnosis

Run with:
    streamlit run demo/app.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run from the repo root
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402
from demo.scenarios import SCENARIOS, ScenarioSnapshot, StubAgent  # noqa: E402
from deployd.application.orchestrators.investigation_orchestrator import (  # noqa: E402
    InvestigationOrchestrator,
)

# ---------------------------------------------------------------------------
# Page config — must be the very first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="DeployD — Investigation Demo",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Styling — CSS custom properties handle dark/light switching automatically
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

    /* ── Design tokens: Dynamic (Auto Light/Dark) ─────────────────────────
       By relying on Streamlit's native --text-color and --background-color
       with color-mix(), the UI flawlessly adapts to Streamlit's manual
       theme toggle without relying on unreliable DOM selectors.
    ─────────────────────────────────────────────────────────────────────── */
    :root {
        --dp-bg:          var(--secondary-background-color);
        --dp-surface:     var(--background-color);
        --dp-surface2:    rgba(128, 128, 128, 0.1);
        --dp-border:      rgba(128, 128, 128, 0.2);

        --dp-txt1:        var(--text-color);
        --dp-txt2:        var(--text-color);
        --dp-txt3:        var(--text-color);
        --dp-txt4:        var(--text-color);
        --dp-txt5:        var(--text-color);

        --dp-code-bg:     rgba(128, 128, 128, 0.1);
        --dp-code-txt:    var(--primary-color, #0ea5e9);

        --dp-accent:      #4f46e5;
    }

    html, body, [class*="css"], .stApp { font-family: 'Inter', sans-serif !important; }

    /* ── Sidebar ─────────────────────────────────────────────────────────── */
    section[data-testid="stSidebar"] {
        background: #0F172A !important;
        border-right: 1px solid rgba(255,255,255,0.05) !important;
    }
    section[data-testid="stSidebar"] > div {
        background: transparent !important;
    }
    /* Force sidebar text to be light in both themes */
    section[data-testid="stSidebar"] * {
        color: #e2e8f0;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        color: #f8fafc !important;
    }
    section[data-testid="stSidebar"] .stSelectbox label { color: #94a3b8 !important; }
    section[data-testid="stSidebar"] .stSelectbox > div > div {
        background: #1e293b !important;
        border-color: #334155 !important;
        color: #f8fafc !important;
        border-radius: 8px !important;
    }
    section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: #4f46e5 !important;
        border: none !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
        color: white !important;
        transition: all 0.2s ease !important;
        width: 100%;
    }
    section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
        background: #4338ca !important;
    }

    /* ── Tier badges ─────────────────────────────────────────────────────── */
    .tier-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.3rem 0.8rem;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        white-space: nowrap;
    }
    .tier-inconclusive {
        background: color-mix(in srgb, #64748b 10%, transparent);
        color: #64748b;
        border: 1px solid color-mix(in srgb, #64748b 30%, transparent);
    }
    .tier-chain-only {
        background: color-mix(in srgb, #f59e0b 10%, transparent);
        color: color-mix(in srgb, #f59e0b 95%, #000);
        border: 1px solid color-mix(in srgb, #f59e0b 30%, transparent);
    }
    .tier-full {
        background: color-mix(in srgb, #10b981 10%, transparent);
        color: color-mix(in srgb, #10b981 95%, #000);
        border: 1px solid color-mix(in srgb, #10b981 30%, transparent);
    }

    /* ── Tier result cards (Sidebar) ─────────────────────────────────────── */
    .result-inconclusive {
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-left: 3px solid #64748b;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        box-shadow: 0 4px 6px -1px rgba(128, 128, 128, 0.1), 0 2px 4px -2px rgba(128, 128, 128, 0.05);
    }
    .result-chain-only {
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-left: 3px solid #f59e0b;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        box-shadow: 0 4px 6px -1px rgba(128, 128, 128, 0.1), 0 2px 4px -2px rgba(128, 128, 128, 0.05);
    }
    .result-full {
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-left: 3px solid #10b981;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        box-shadow: 0 4px 6px -1px rgba(128, 128, 128, 0.1), 0 2px 4px -2px rgba(128, 128, 128, 0.05);
    }

    /* ── FSM badges ──────────────────────────────────────────────────────── */
    .fsm-badge-base { display:inline-flex; align-items:center; gap:0.3rem; padding:4px 12px; border-radius:999px; font-weight:700; font-size:0.75rem; letter-spacing:0.03em; }
    .fsm-healthy    { background:color-mix(in srgb, #10b981 10%, transparent); color:color-mix(in srgb, #10b981 95%, #000); border:1px solid color-mix(in srgb, #10b981 30%, transparent); display:inline-flex; align-items:center; gap:0.3rem; padding:4px 12px; border-radius:4px; font-weight:700; font-size:0.75rem; }
    .fsm-degraded   { background:color-mix(in srgb, #f59e0b 10%, transparent); color:color-mix(in srgb, #f59e0b 95%, #000); border:1px solid color-mix(in srgb, #f59e0b 30%, transparent); display:inline-flex; align-items:center; gap:0.3rem; padding:4px 12px; border-radius:4px; font-weight:700; font-size:0.75rem; }
    .fsm-crashing   { background:color-mix(in srgb, #ef4444 10%, transparent); color:color-mix(in srgb, #ef4444 95%, #000); border:1px solid color-mix(in srgb, #ef4444 30%, transparent); display:inline-flex; align-items:center; gap:0.3rem; padding:4px 12px; border-radius:4px; font-weight:700; font-size:0.75rem; }
    .fsm-restarting { background:color-mix(in srgb, #3b82f6 10%, transparent); color:color-mix(in srgb, #3b82f6 95%, #000); border:1px solid color-mix(in srgb, #3b82f6 30%, transparent); display:inline-flex; align-items:center; gap:0.3rem; padding:4px 12px; border-radius:4px; font-weight:700; font-size:0.75rem; }
    .fsm-crash-loop { background:color-mix(in srgb, #a855f7 10%, transparent); color:color-mix(in srgb, #a855f7 95%, #000); border:1px solid color-mix(in srgb, #a855f7 30%, transparent); display:inline-flex; align-items:center; gap:0.3rem; padding:4px 12px; border-radius:4px; font-weight:700; font-size:0.75rem; }

    /* ── Severity pills ──────────────────────────────────────────────────── */
    .sev-info     { display:inline-block; padding: 2px 6px; border-radius: 4px; background: color-mix(in srgb, #3b82f6 10%, transparent); color: #3b82f6; font-weight:700; font-size:0.65rem; letter-spacing:0.04em; border: 1px solid color-mix(in srgb, #3b82f6 20%, transparent); }
    .sev-warning  { display:inline-block; padding: 2px 6px; border-radius: 4px; background: color-mix(in srgb, #f59e0b 10%, transparent); color: #f59e0b; font-weight:700; font-size:0.65rem; letter-spacing:0.04em; border: 1px solid color-mix(in srgb, #f59e0b 20%, transparent); }
    .sev-error    { display:inline-block; padding: 2px 6px; border-radius: 4px; background: color-mix(in srgb, #ef4444 10%, transparent); color: #ef4444; font-weight:700; font-size:0.65rem; letter-spacing:0.04em; border: 1px solid color-mix(in srgb, #ef4444 20%, transparent); }
    .sev-critical { display:inline-block; padding: 2px 6px; border-radius: 4px; background: color-mix(in srgb, #ef4444 10%, transparent); color: #ef4444; font-weight:700; font-size:0.65rem; letter-spacing:0.04em; border: 1px solid color-mix(in srgb, #ef4444 20%, transparent); }

    /* ── Panel title & Badges ────────────────────────────────────────────── */
    .panel-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1.2rem;
    }
    .panel-title {
        font-size: 0.8rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        color: var(--dp-txt1);
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .panel-title-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
    }
    .panel-badge {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.7rem;
        color: var(--dp-txt3);
        border: 1px solid var(--dp-border);
        padding: 0.2rem 0.6rem;
        border-radius: 4px;
    }

    /* ── Layout ──────────────────────────────────────────────────────────── */
    .stApp, [data-testid="stAppViewContainer"], .main {
        background-color: var(--dp-bg) !important;
    }
    [data-testid="stHeader"] {
        background-color: var(--dp-bg) !important;
        border-bottom: 1px solid var(--dp-border);
    }
    .main .block-container {
        padding-top: 1rem !important;
        max-width: 1300px !important;
    }
    /* ── Stat numbers in JetBrains Mono ─────────────────────────────────── */
    .mono { font-family: 'JetBrains Mono', monospace !important; }
    /* ── Card containers ─────────────────────────────────────────────────── */
    .dp-card {
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-radius: 12px;
        padding: 1.5rem;
        position: relative;
        overflow: hidden;
        box-shadow: 0 4px 6px -1px color-mix(in srgb, var(--text-color) 6%, transparent), 0 2px 4px -2px color-mix(in srgb, var(--text-color) 4%, transparent);
    }

    .dp-panel {
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-radius: 10px;
        padding: 1rem 1.25rem;
        box-shadow: 0 4px 6px -1px color-mix(in srgb, var(--text-color) 6%, transparent), 0 2px 4px -2px color-mix(in srgb, var(--text-color) 4%, transparent);
    }

    div[style*="var(--dp-border)"] {
        box-shadow: 0 4px 6px -1px color-mix(in srgb, var(--text-color) 6%, transparent), 0 2px 4px -2px color-mix(in srgb, var(--text-color) 4%, transparent);
    }

    /* ── Active Scenario Top Bar ─────────────────────────────────────────── */
    .scenario-nav-bar {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.75rem;
        color: var(--dp-txt3);
        margin-bottom: 1rem;
        font-weight: 500;
        letter-spacing: 0.03em;
    }
    .scenario-nav-btn {
        background: transparent;
        border: 1px solid var(--dp-border);
        color: var(--dp-txt2);
        padding: 0.4rem 0.8rem;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        cursor: pointer;
    }
    .scenario-nav-btn.primary {
        background: #4f46e5;
        border-color: #4f46e5;
        color: white;
    }

    .pipeline-steps {
        display: flex;
        align-items: center;
        gap: 0.4rem;
        background: var(--dp-surface2);
        padding: 0.4rem 0.8rem;
        border-radius: 6px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.7rem;
        color: var(--dp-txt3);
    }
    .pipeline-steps .active {
        color: var(--dp-accent);
        font-weight: 600;
    }

    /* Utility */
    .text-sm { font-size: 0.85rem; color: var(--dp-txt2); }
    .text-xs { font-size: 0.75rem; color: var(--dp-txt3); }
    .mt-4 { margin-top: 1rem; }
    .mb-4 { margin-bottom: 1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FSM_DOTS = {
    "HEALTHY": "●",
    "DEGRADED": "◆",
    "CRASHING": "▲",
    "RESTARTING": "↻",
    "CRASH_LOOP": "⚡",
}

_FSM_CSS = {
    "HEALTHY": "fsm-healthy",
    "DEGRADED": "fsm-degraded",
    "CRASHING": "fsm-crashing",
    "RESTARTING": "fsm-restarting",
    "CRASH_LOOP": "fsm-crash-loop",
}

_SEV_CSS = {
    "INFO": "sev-info",
    "WARNING": "sev-warning",
    "ERROR": "sev-error",
    "CRITICAL": "sev-critical",
}

_TIER_BADGE_CSS = {
    "INCONCLUSIVE": "tier-inconclusive",
    "CHAIN_ONLY": "tier-chain-only",
    "FULL": "tier-full",
}

_TIER_RESULT_CSS = {
    "INCONCLUSIVE": "result-inconclusive",
    "CHAIN_ONLY": "result-chain-only",
    "FULL": "result-full",
}

_TIER_ICONS = {
    "INCONCLUSIVE": "🔍",
    "CHAIN_ONLY": "⚠️",
    "FULL": "✅",
}

_TIER_LABELS = {
    "INCONCLUSIVE": "INCONCLUSIVE",
    "CHAIN_ONLY": "CHAIN ONLY",
    "FULL": "FULL DIAGNOSIS",
}


def _fsm_badge(state: str) -> str:
    css = _FSM_CSS.get(state, "fsm-healthy")
    dot = _FSM_DOTS.get(state, "●")
    return f'<span class="{css}">{dot} {state}</span>'


def _sev_badge(sev: str) -> str:
    css = _SEV_CSS.get(sev, "sev-info")
    return f'<span class="{css}">{sev}</span>'


def _score_bar(score: float, threshold: float) -> str:
    pct = int(score * 100)
    threshold_pct = int(threshold * 100)
    if score >= threshold:
        fill_color = "#10b981"  # green: above threshold
    elif score >= threshold * 0.6:
        fill_color = "#f59e0b"  # amber: getting closer
    else:
        fill_color = "#64748b"  # grey: far below
    return (
        f'<div style="position:relative;background:var(--dp-surface2);border-radius:6px;height:10px;width:100%;">'
        f'<div style="width:{pct}%;height:10px;border-radius:6px;background:{fill_color};transition:width 0.4s;"></div>'
        f'<div style="position:absolute;top:-4px;left:{threshold_pct}%;width:2px;height:18px;'
        f'background:#f59e0b;border-radius:1px;" title="Confidence threshold {threshold:.0%}"></div>'
        f"</div>"
    )


def _build_pyvis_html(snap: ScenarioSnapshot) -> str:
    """Generate pyvis graph HTML for the incident graph."""
    try:
        from pyvis.network import Network
    except ImportError:
        return "<p style='color:#f87171'>pyvis not installed — run: pip install pyvis</p>"

    net = Network(
        height="380px",
        width="100%",
        bgcolor="transparent",
        font_color="#64748b",
        directed=True,
    )
    net.set_options("""
    {
      "nodes": {
        "borderWidth": 2,
        "shadow": true,
        "font": {"size": 12, "face": "Inter, sans-serif"}
      },
      "edges": {
        "arrows": {"to": {"enabled": true, "scaleFactor": 0.7}},
        "color": {"color": "#94a3b8", "highlight": "#f59e0b"},
        "smooth": {"type": "curvedCW", "roundness": 0.2},
        "shadow": false,
        "width": 2
      },
      "physics": {
        "solver": "hierarchicalRepulsion",
        "hierarchicalRepulsion": {"nodeDistance": 140, "springLength": 120},
        "stabilization": {"iterations": 200}
      },
      "layout": {"hierarchical": {"enabled": true, "direction": "LR", "sortMethod": "directed"}}
    }
    """)

    _SEV_COLORS = {
        "INFO": "#3b82f6",
        "WARNING": "#f59e0b",
        "ERROR": "#ef4444",
        "CRITICAL": "#a855f7",
    }

    for node in snap.graph.nodes:
        ev = node.event
        color = _SEV_COLORS.get(ev.severity.value, "#64748b")
        label = f"{ev.event_type.value}\n{ev.timestamp.strftime('%H:%M:%S')}"
        desc = ev.description
        title = (
            f"<b>{ev.event_type.value}</b><br>"
            f"Severity: {ev.severity.value}<br>"
            f"Time: {ev.timestamp.strftime('%H:%M:%S UTC')}<br>"
            f"<i>{desc[:80]}...</i>"
            if len(desc) > 80
            else f"<b>{ev.event_type.value}</b><br>Severity: {ev.severity.value}<br>"
            f"Time: {ev.timestamp.strftime('%H:%M:%S UTC')}<br><i>{desc}</i>"
        )
        net.add_node(
            str(node.node_id),
            label=label,
            title=title,
            color={"background": color, "border": "#ffffff", "highlight": {"background": color}},
            shape="dot",
            size=22,
        )

    for edge in snap.graph.edges:
        net.add_edge(
            str(edge.source),
            str(edge.target),
            title=f"Rule: {edge.rule_id or 'N/A'} | Confidence: {edge.confidence:.0%}",
            label=f"{edge.confidence:.0%}",
        )

    return str(net.generate_html())


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown(
        """
        <div style="display:flex; align-items:center; gap:0.6rem; padding:1.5rem 0.5rem 1rem;">
          <div style="background:#4f46e5; color:white; width:32px; height:32px; border-radius:8px;
                      display:flex; align-items:center; justify-content:center; font-weight:800; font-size:1.1rem;">
            D
          </div>
          <div>
            <h2 style="color:#f8fafc; margin:0; font-size:1.15rem; font-weight:700; line-height:1;">DeployD</h2>
            <p style="color:#94a3b8; font-size:0.7rem; margin:0; margin-top:0.2rem;">
              Investigation Pipeline Demo
            </p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    selected_key = st.selectbox(
        "Select incident scenario",
        options=list(SCENARIOS.keys()),
        index=0,
        help="Each scenario exercises a different orchestrator tier.",
    )

    st.markdown("<br>", unsafe_allow_html=True)
    run_clicked = st.button("▶ Run Investigation", use_container_width=True, type="primary")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        """
        <div style="background:rgba(0,0,0,0.25); border-radius:8px; padding:0.75rem;
                    border:1px solid #334155;">
          <p style="color:#94a3b8; font-size:0.7rem; margin:0; line-height:1.6;">
            <b style="color:#e2e8f0;">Tier 1</b> — INCONCLUSIVE<br>
            No observable evidence.<br><br>
            <b style="color:#e2e8f0;">Tier 2</b> — CHAIN ONLY<br>
            Chain found, no runbook match.<br><br>
            <b style="color:#e2e8f0;">Tier 3</b> — FULL DIAGNOSIS<br>
            Chain + runbook + AI grounding.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Main header
# ---------------------------------------------------------------------------

st.markdown(
    """
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1.5rem;">
      <div style="font-size:0.75rem; font-weight:600; color:var(--dp-txt3); letter-spacing:0.04em;">
        WORKSPACE <span style="margin:0 0.4rem; color:var(--dp-border);">/</span>
        Incident Diagnostics <span style="margin:0 0.4rem; color:var(--dp-border);">/</span>
        <span style="color:var(--dp-accent);">INC-84920</span>
      </div>
      <div style="display:flex; gap:0.5rem;">
        <button class="scenario-nav-btn">↻ Re-evaluate</button>
        <button class="scenario-nav-btn primary">Export Report</button>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Run investigation on button click
# ---------------------------------------------------------------------------

if "snap" not in st.session_state:
    st.session_state.snap = None
    st.session_state.result = None

if run_clicked:
    factory = SCENARIOS[selected_key]
    snap: ScenarioSnapshot = factory()
    orchestrator = InvestigationOrchestrator(agent=StubAgent())
    result = orchestrator.run(snap.request)
    st.session_state.snap = snap
    st.session_state.result = result

snap = st.session_state.snap  # type: ignore[assignment]
result = st.session_state.result

# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------

if snap is None:
    st.markdown(
        """
        <div style="text-align:center; padding:5rem 2rem; color:var(--dp-txt5);">
          <h3 style="color:var(--dp-txt4); font-weight:600;">No investigation running</h3>
          <p style="color:var(--dp-txt5); font-size:0.9rem;">
            Select a scenario in the sidebar and click <b>&#9654; Run Investigation</b>
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# ---------------------------------------------------------------------------
# Scenario banner
# ---------------------------------------------------------------------------

tier_val = result.tier.value
tier_badge_css = _TIER_BADGE_CSS[tier_val]
tier_icon = _TIER_ICONS[tier_val]
tier_label = _TIER_LABELS[tier_val]

# Determine active step for the pipeline UI
active_step_html = ""
if tier_val == "INCONCLUSIVE":
    active_step_html = '<span class="active">1. Event</span> <span style="color:var(--dp-border); margin:0 2px;">→</span> 2. Analysis <span style="color:var(--dp-border); margin:0 2px;">→</span> 3. Retrieval <span style="color:var(--dp-border); margin:0 2px;">→</span> 4. Grounded AI'
elif tier_val == "CHAIN_ONLY":
    active_step_html = '<span style="color:#10b981;">1. Event</span> <span style="color:var(--dp-border); margin:0 2px;">→</span> <span style="color:#10b981;">2. Analysis</span> <span style="color:var(--dp-border); margin:0 2px;">→</span> <span class="active" style="color:#f59e0b;">3. Retrieval</span> <span style="color:var(--dp-border); margin:0 2px;">→</span> 4. Grounded AI'
else:
    active_step_html = '<span style="color:#10b981;">1. Event</span> <span style="color:var(--dp-border); margin:0 2px;">→</span> <span style="color:#10b981;">2. Analysis</span> <span style="color:var(--dp-border); margin:0 2px;">→</span> <span style="color:#10b981;">3. Retrieval</span> <span style="color:var(--dp-border); margin:0 2px;">→</span> <span class="active" style="color:#a855f7;">4. Grounded AI</span>'

st.markdown(
    f"""
    <div style="background:var(--dp-surface); border:1px solid var(--dp-border);
                border-radius:12px; padding:1.5rem; margin-bottom:1.5rem; position:relative;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:0.75rem;">
        <div style="color:var(--dp-txt4); font-size:0.75rem; font-weight:700;
                    text-transform:uppercase; letter-spacing:0.08em; display:flex; align-items:center; gap:0.5rem;">
          ACTIVE SCENARIO <span style="width:4px;height:4px;border-radius:50%;background:var(--dp-txt4);"></span>
          <span style="color:var(--dp-accent);">{snap.component}</span>
        </div>
        <span class="tier-badge {tier_badge_css}">{tier_icon} {tier_label}</span>
      </div>

      <div style="color:var(--dp-txt1); font-size:1.5rem; font-weight:700; margin-bottom:0.75rem; display:flex; align-items:baseline; gap:0.5rem;">
        {snap.name.split(' (')[0]}
        <span style="color:var(--dp-txt4); font-size:1.1rem; font-weight:500;">
          ({snap.name.split(' (')[1] if ' (' in snap.name else ''}
        </span>
      </div>

      <div style="display:flex; justify-content:space-between; align-items:flex-end;">
        <div style="color:var(--dp-txt2); font-size:0.9rem; line-height:1.6; max-width:65%;">
          {snap.description}
        </div>
        <div class="pipeline-steps">
          {active_step_html}
        </div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Panel row 1: Events Feed | Graph
# ---------------------------------------------------------------------------

col_events, col_graph = st.columns([1, 1.6], gap="medium")

with col_events:
    h = ['<div class="dp-panel" style="height:100%;">']
    h.append(f"""
        <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
          <div><span style="color:#10b981; margin-right:4px;">●</span> INCOMING EVENTS FEED</div>
          <div style="font-size:0.7rem; font-weight:normal; background:var(--dp-surface2); padding:2px 8px; border-radius:12px; color:var(--dp-txt3); text-transform:none; letter-spacing:normal;">{len(snap.events)} events captured</div>
        </div>
    """)
    if not snap.events:
        h.append(
            '<div style="color:var(--dp-txt5); font-style:italic; padding:1rem 0;">No events observed in query window.</div>'
        )
    else:
        for ev in snap.events:
            sev_html = _sev_badge(ev.severity.value)
            delta = int((ev.timestamp - snap.events[0].timestamp).total_seconds())
            h.append(f"""
                <div style="display:flex; gap:0.75rem; align-items:flex-start;
                            padding:0.5rem 0; border-bottom:1px solid var(--dp-border);">
                  <div style="color:var(--dp-txt5); font-size:0.75rem; width:2rem;
                              flex-shrink:0; padding-top:2px;">T+{delta}s</div>
                  <div style="flex:1;">
                    <div style="display:flex; align-items:center; gap:0.5rem;">
                      {sev_html}
                      <span style="color:var(--dp-txt2); font-size:0.82rem; font-weight:500;">
                        {ev.event_type.value}
                      </span>
                    </div>
                    <div style="color:var(--dp-txt4); font-size:0.75rem; margin-top:2px;
                                line-height:1.4;">
                      {ev.description[:90]}{"…" if len(ev.description) > 90 else ""}
                    </div>
                  </div>
                </div>
            """)
    h.append("</div>")
    st.markdown("".join(h), unsafe_allow_html=True)

with col_graph:
    h = ['<div class="dp-panel" style="height:100%;">']
    h.append("""
        <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
          <div><span style="color:#10b981; margin-right:4px;">●</span> INCIDENT CAUSAL GRAPH</div>
          <div style="font-size:0.7rem; font-weight:normal; background:var(--dp-surface2); padding:2px 8px; border-radius:12px; color:var(--dp-txt3); text-transform:none; letter-spacing:normal;">DAG deterministic link</div>
        </div>
    """)
    if not snap.graph.nodes:
        h.append(
            '<div style="background:var(--dp-empty-bg); border:1px solid var(--dp-empty-bd); border-radius:10px; padding:3rem; text-align:center; color:var(--dp-empty-tx); font-style:italic;">Graph is empty — no events to visualise.</div>'
        )
    else:
        graph_html = _build_pyvis_html(snap)
        b64 = base64.b64encode(graph_html.encode()).decode()
        h.append(
            f'<iframe src="data:text/html;base64,{b64}" style="width:100%;height:390px;border:none;border-radius:12px;background:transparent;" scrolling="no"></iframe>'
        )
    h.append("</div>")
    st.markdown("".join(h), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Panel row 2: FSM state | Confidence
# ---------------------------------------------------------------------------

col_fsm, col_conf = st.columns([1, 1], gap="medium")

with col_fsm:
    st.markdown(
        """
        <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center;">
          <div><span style="color:#10b981; margin-right:4px;">●</span> PROCESSHEALTHFSM STATUS</div>
          <div style="font-size:0.7rem; font-weight:normal; background:var(--dp-surface2); padding:2px 8px; border-radius:12px; color:var(--dp-txt3); text-transform:none; letter-spacing:normal;">Deterministic Engine</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    fsm_state_str = snap.fsm_final_state.value
    badge_html = _fsm_badge(fsm_state_str)
    st.markdown(
        f"""
        <div style="background:var(--dp-surface); border:1px solid var(--dp-border);
                    border-radius:10px; padding:1rem 1.25rem;">
          <div style="color:var(--dp-txt3); font-size:0.75rem; margin-bottom:0.5rem;">
            Final State
          </div>
          <div style="font-size:1.5rem; margin-bottom:0.75rem;">{badge_html}</div>
          <div style="color:var(--dp-txt4); font-size:0.75rem; font-weight:600;
                      text-transform:uppercase; letter-spacing:0.06em; margin-bottom:0.4rem;">
            Transition History
          </div>
        """,
        unsafe_allow_html=True,
    )
    if not snap.fsm_transitions:
        st.markdown(
            '<div style="color:var(--dp-txt5); font-style:italic; font-size:0.8rem;'
            'padding:0.25rem 0;">No transitions — FSM stayed HEALTHY.</div>',
            unsafe_allow_html=True,
        )
    else:
        for t in snap.fsm_transitions:
            st.markdown(
                f"""
                <div style="display:flex; align-items:center; gap:0.5rem;
                            padding:0.3rem 0; font-size:0.78rem; color:var(--dp-txt2);
                            border-bottom:1px solid var(--dp-border2);">
                  <span style="color:var(--dp-txt5); width:5rem; flex-shrink:0;">
                    {t.timestamp.strftime('%H:%M:%S')}
                  </span>
                  {_fsm_badge(t.from_state.value)}
                  <span style="color:var(--dp-txt4);">→</span>
                  {_fsm_badge(t.to_state.value)}
                </div>
                """,
                unsafe_allow_html=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)

with col_conf:
    st.markdown(
        """
        <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center;">
          <div><span style="color:#a855f7; margin-right:4px;">●</span> CONFIDENCE & EVIDENCE COVERAGE</div>
          <div style="font-size:0.7rem; font-weight:normal; background:var(--dp-surface2); padding:2px 8px; border-radius:12px; color:var(--dp-txt3); text-transform:none; letter-spacing:normal;">Vector Retrieval</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    threshold = snap.retrieval_result.confidence_threshold
    best_score = (
        max(c.score for c in snap.retrieval_result.candidates)
        if snap.retrieval_result.candidates
        else 0.0
    )
    n_strong = len(snap.retrieval_result.strong_candidates)
    n_total = len(snap.retrieval_result.candidates)
    strong_color = "#059669" if n_strong > 0 else "#ef4444"

    missing_banner = (
        '<div style="background:var(--dp-approval-bg); border:1px solid var(--dp-approval-bd);'
        "border-radius:6px; padding:0.5rem 0.75rem; margin-top:0.75rem;"
        'color:var(--dp-approval-tx); font-size:0.78rem;">'
        "⚠ Missing evidence: no CoreEvents recorded for this component in the query window."
        "</div>"
        if not snap.events
        else ""
    )

    st.markdown(
        f"""
        <div style="background:var(--dp-surface); border:1px solid var(--dp-border);
                    border-radius:10px; padding:1rem 1.25rem;">
          <div style="display:flex; justify-content:space-between; margin-bottom:0.3rem;">
            <span style="color:var(--dp-txt3); font-size:0.8rem;">Best retrieval score</span>
            <span style="color:var(--dp-txt1); font-weight:700; font-size:1.1rem;">
              {best_score:.0%}
            </span>
          </div>
          {_score_bar(best_score, threshold)}
          <div style="display:flex; justify-content:space-between; margin-top:0.3rem;">
            <span style="color:var(--dp-txt5); font-size:0.7rem;">0%</span>
            <span style="color:#f59e0b; font-size:0.7rem;">▲ threshold {threshold:.0%}</span>
            <span style="color:var(--dp-txt5); font-size:0.7rem;">100%</span>
          </div>

          <div style="margin-top:1rem; border-top:1px solid var(--dp-border2); padding-top:0.75rem;">
            <div style="color:var(--dp-txt4); font-size:0.75rem; font-weight:600;
                        text-transform:uppercase; letter-spacing:0.06em; margin-bottom:0.5rem;">
              Evidence Coverage
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.5rem;">
              <div style="background:var(--dp-surface2); border-radius:8px; padding:0.5rem 0.75rem;">
                <div style="color:var(--dp-txt3); font-size:0.7rem;">Events observed</div>
                <div style="color:var(--dp-txt1); font-size:1.3rem; font-weight:700;">
                  {len(snap.events)}
                </div>
              </div>
              <div style="background:var(--dp-surface2); border-radius:8px; padding:0.5rem 0.75rem;">
                <div style="color:var(--dp-txt3); font-size:0.7rem;">Graph nodes</div>
                <div style="color:var(--dp-txt1); font-size:1.3rem; font-weight:700;">
                  {len(snap.graph.nodes)}
                </div>
              </div>
              <div style="background:var(--dp-surface2); border-radius:8px; padding:0.5rem 0.75rem;">
                <div style="color:var(--dp-txt3); font-size:0.7rem;">Runbooks retrieved</div>
                <div style="color:var(--dp-txt1); font-size:1.3rem; font-weight:700;">{n_total}</div>
              </div>
              <div style="background:var(--dp-surface2); border-radius:8px; padding:0.5rem 0.75rem;">
                <div style="color:var(--dp-txt3); font-size:0.7rem;">Strong matches</div>
                <div style="color:{strong_color}; font-size:1.3rem; font-weight:700;">{n_strong}</div>
              </div>
            </div>
          </div>
          {missing_banner}
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Panel 5: Causal Chain
# ---------------------------------------------------------------------------

h_p5 = ['<div class="dp-panel" style="margin-bottom: 1.5rem;">']
h_p5.append("""
    <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
      <div><span style="color:#f59e0b; margin-right:4px;">●</span> DETECTED CAUSAL CHAIN</div>
      <div style="font-size:0.7rem; font-weight:normal; background:var(--dp-surface2); padding:2px 8px; border-radius:12px; color:var(--dp-txt3); text-transform:none; letter-spacing:normal;">DID-8</div>
    </div>
""")
causal_chains = result.causal_chains

if not causal_chains:
    h_p5.append(
        '<div style="background:var(--dp-surface); border:1px solid var(--dp-border); border-radius:10px; padding:1.25rem; color:var(--dp-txt5); font-style:italic;">No causal chain — the incident graph is empty or no causal edges were found.</div>'
    )
else:
    for chain_idx, chain in enumerate(causal_chains):
        chain_parts = []
        for node in chain:
            ev = node.event
            sev_color = {
                "INFO": "#3b82f6",
                "WARNING": "#f59e0b",
                "ERROR": "#ef4444",
                "CRITICAL": "#a855f7",
            }.get(ev.severity.value, "#64748b")
            chain_parts.append(
                f'<span style="background:var(--dp-surface2); border:1px solid {sev_color};'
                f'border-radius:6px; padding:4px 10px; font-size:0.78rem; color:var(--dp-txt1);'
                f'white-space:nowrap;" title="{ev.description}">'
                f'<span style="color:{sev_color};">●</span> {ev.event_type.value}'
                f'<span style="color:var(--dp-txt5); margin-left:6px;">'
                f'{ev.timestamp.strftime("%H:%M:%S")}</span></span>'
            )
        arrow = '<span style="color:var(--dp-txt4); font-size:1.1rem; flex-shrink:0;">→</span>'
        chain_html = f" {arrow} ".join(chain_parts)
        h_p5.append(f"""
            <div style="background:var(--dp-surface); border:1px solid var(--dp-border);
                        border-radius:10px; padding:0.9rem 1.25rem; margin-bottom:0.5rem;">
              <div style="color:var(--dp-txt4); font-size:0.7rem; font-weight:600;
                          text-transform:uppercase; margin-bottom:0.5rem;">
                Chain {chain_idx + 1} — {len(chain)} nodes
              </div>
              <div style="display:flex; flex-wrap:wrap; align-items:center; gap:0.4rem;">
                {chain_html}
              </div>
            </div>
        """)
h_p5.append("</div>")
st.markdown("".join(h_p5), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Panel 6: Retrieved Runbooks
# ---------------------------------------------------------------------------

h_p6 = ['<div class="dp-panel" style="margin-bottom: 1.5rem;">']
h_p6.append("""
    <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
      <div><span style="color:#3b82f6; margin-right:4px;">●</span> RETRIEVED RUNBOOKS</div>
      <div style="font-size:0.7rem; font-weight:normal; background:var(--dp-surface2); padding:2px 8px; border-radius:12px; color:var(--dp-txt3); text-transform:none; letter-spacing:normal;">DID-7</div>
    </div>
""")

if not snap.runbook_details:
    h_p6.append(
        '<div style="background:var(--dp-surface); border:1px solid var(--dp-border); border-radius:10px; padding:1.25rem; color:var(--dp-txt5); font-style:italic;">No runbooks retrieved — retrieval was not performed (empty evidence graph).</div>'
    )
else:
    threshold = snap.retrieval_result.confidence_threshold
    for rb in snap.runbook_details:
        is_strong = rb.score >= threshold
        border_color = "#166534" if is_strong else "var(--dp-border)"
        badge_color = "#059669" if is_strong else "var(--dp-txt4)"
        badge_label = "STRONG MATCH" if is_strong else "BELOW THRESHOLD"

        tags_html = " ".join(
            f'<span style="background:var(--dp-surface2); border:1px solid var(--dp-border);'
            f'border-radius:4px; padding:2px 7px; font-size:0.68rem; color:var(--dp-txt4);">'
            f"{tag}</span>"
            for tag in rb.tags
        )
        commands_html = "".join(
            f'<code style="display:block; background:var(--dp-code-bg); border-radius:4px;'
            f'padding:4px 8px; font-size:0.75rem; color:var(--dp-code-txt); margin-top:4px;">'
            f"{cmd}</code>"
            for cmd in rb.fix_commands
        )

        h_p6.append(f"""
            <div style="background:var(--dp-surface); border:1px solid {border_color};
                        border-radius:10px; padding:1rem 1.25rem; margin-bottom:0.6rem;">
              <div style="display:flex; align-items:center; gap:0.75rem; margin-bottom:0.5rem;">
                <span style="color:var(--dp-txt1); font-weight:700; font-size:0.9rem;">
                  {rb.runbook_id}
                </span>
                <span style="background:transparent; border:1px solid {badge_color};
                             color:{badge_color}; border-radius:999px; font-size:0.65rem;
                             font-weight:700; padding:2px 8px; letter-spacing:0.06em;">
                  {badge_label}
                </span>
                <span style="margin-left:auto; color:var(--dp-txt1); font-weight:700;">
                  {rb.score:.0%}
                </span>
              </div>
              {_score_bar(rb.score, threshold)}
              <div style="margin-top:0.75rem; color:var(--dp-txt3); font-size:0.8rem;">
                {rb.summary}
              </div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.75rem;
                          margin-top:0.75rem;">
                <div>
                  <div style="color:var(--dp-txt4); font-size:0.7rem; font-weight:600;
                              text-transform:uppercase; margin-bottom:0.25rem;">
                    Root Cause
                  </div>
                  <div style="color:var(--dp-txt2); font-size:0.78rem;">{rb.root_cause}</div>
                </div>
                <div>
                  <div style="color:var(--dp-txt4); font-size:0.7rem; font-weight:600;
                              text-transform:uppercase; margin-bottom:0.25rem;">
                    Historical Fix
                  </div>
                  <div style="color:var(--dp-txt2); font-size:0.78rem;">{rb.fix}</div>
                  {commands_html}
                </div>
              </div>
              <div style="margin-top:0.6rem;">{tags_html}</div>
            </div>
        """)
h_p6.append("</div>")
st.markdown("".join(h_p6), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Panel 7 (+8): Final Diagnosis
# ---------------------------------------------------------------------------

h_p7 = ['<div class="dp-panel" style="margin-bottom: 2rem;">']
h_p7.append("""
    <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
      <div><span style="color:#ef4444; margin-right:4px;">●</span> FINAL DIAGNOSIS</div>
      <div style="font-size:0.7rem; font-weight:normal; background:var(--dp-surface2); padding:2px 8px; border-radius:12px; color:var(--dp-txt3); text-transform:none; letter-spacing:normal;">DID-11/12</div>
    </div>
""")

result_css = _TIER_RESULT_CSS[tier_val]
_TIER_INTRO = {
    "INCONCLUSIVE": (
        "The investigation is <strong>inconclusive</strong>. No observable evidence was "
        "present in the query window. The AI agent was <strong>not called</strong>. "
        "Zero token cost, zero hallucination risk."
    ),
    "CHAIN_ONLY": (
        "A causal chain was detected, but <strong>no historical runbook matched</strong> "
        "above the confidence threshold. The AI agent was <strong>not called</strong>. "
        "Human review of the chain is mandatory before any action."
    ),
    "FULL": (
        "A causal chain and a strong historical match were both present. "
        "The AI agent was called <strong>exactly once</strong> with the chain and "
        "retrieved runbooks as grounding context."
    ),
}

h_p7.append(f"""
    <div class="{result_css}" style="margin-bottom:0; box-shadow:none;">
      <div style="display:flex; align-items:center; gap:0.75rem; margin-bottom:0.75rem;">
        <span class="tier-badge {tier_badge_css}" style="font-size:1rem; padding:0.4rem 1rem;">
          {tier_icon} {tier_label}
        </span>
        <span style="color:var(--dp-txt4); font-size:0.8rem;">
          FSM state: {_fsm_badge(result.fsm_state.value)}
        </span>
      </div>
      <p style="color:var(--dp-txt3); font-size:0.82rem; margin:0 0 0.75rem;">
        {_TIER_INTRO[tier_val]}
      </p>
      <div style="background:var(--dp-surface2); border-radius:8px; padding:1rem;
                  color:var(--dp-txt2); font-size:0.85rem; line-height:1.65;
                  white-space:pre-wrap;">{result.remediation.summary}</div>
    </div>
""")

if result.remediation.requires_human_approval:
    h_p7.append(
        '<div class="approval-banner" style="margin-top:1rem;">'
        "⚠️ <strong>Human approval required</strong> — no remediation action will "
        "be executed without explicit engineer sign-off."
        "</div>"
    )

if result.remediation.evidence_references:
    refs_html = " ".join(
        f'<span style="background:var(--dp-surface2); border:1px solid #166534;'
        f'border-radius:4px; padding:3px 9px; font-size:0.75rem; color:#059669;">{ref}</span>'
        for ref in result.remediation.evidence_references
    )
    h_p7.append(f"""
        <div style="margin-top:1rem;">
          <span style="color:var(--dp-txt4); font-size:0.75rem; font-weight:600;
                       text-transform:uppercase; letter-spacing:0.06em;">
            Grounding references:
          </span>
          <div style="margin-top:0.4rem; display:flex; flex-wrap:wrap; gap:0.4rem;">
            {refs_html}
          </div>
        </div>
    """)

h_p7.append("</div>")
st.markdown("".join(h_p7), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown(
    """
    <div style="margin-top:2rem; padding-top:1rem; border-top:1px solid var(--dp-border);
                text-align:center; color:var(--dp-txt5); font-size:0.72rem;">
      DeployD Demo UI — event → deterministic analysis → retrieval → AI explanation
      &nbsp;|&nbsp; No auth · No websockets · No separate build step
    </div>
    """,
    unsafe_allow_html=True,
)
