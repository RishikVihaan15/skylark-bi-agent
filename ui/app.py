import os
import sys

import streamlit as st
from dotenv import load_dotenv

# Load .env from the project root (one level above ui/)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import get_client, new_chat, run_agent_turn
from agent.monday_client import MondayAPIError
from agent.tools import get_store

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Skylark BI Agent",
    page_icon="🚁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Inter font */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* Dark header banner */
.header-banner {
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%);
    border-radius: 12px;
    padding: 28px 32px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 20px;
}
.header-logo { font-size: 2.8rem; }
.header-text h1 {
    color: #f1f5f9;
    font-size: 1.6rem;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.3px;
}
.header-text p {
    color: #94a3b8;
    font-size: 0.875rem;
    margin: 4px 0 0 0;
}
.header-badge {
    margin-left: auto;
    background: #10b981;
    color: white;
    font-size: 0.7rem;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 20px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}

/* Suggestion chips */
.chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 20px;
}
.chip {
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 20px;
    padding: 6px 14px;
    font-size: 0.8rem;
    color: #475569;
    cursor: pointer;
    transition: all 0.15s;
}
.chip:hover { background: #e2e8f0; color: #1e293b; }

/* Status pills */
.status-ok {
    display: inline-flex; align-items: center; gap: 5px;
    background: #f0fdf4; color: #16a34a;
    border: 1px solid #bbf7d0;
    border-radius: 6px; padding: 3px 10px;
    font-size: 0.78rem; font-weight: 500;
    margin-bottom: 4px; width: 100%;
}
.status-err {
    display: inline-flex; align-items: center; gap: 5px;
    background: #fef2f2; color: #dc2626;
    border: 1px solid #fecaca;
    border-radius: 6px; padding: 3px 10px;
    font-size: 0.78rem; font-weight: 500;
    margin-bottom: 4px; width: 100%;
}

/* Chat message styling */
[data-testid="stChatMessage"] {
    border-radius: 10px;
    padding: 4px 0;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: #f8fafc;
    border-right: 1px solid #e2e8f0;
}

/* Input box */
[data-testid="stChatInput"] textarea {
    border-radius: 10px !important;
    font-size: 0.9rem !important;
}

/* Spinner text */
.stSpinner > div { font-size: 0.85rem; color: #64748b; }

/* Section label */
.section-label {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #94a3b8;
    margin-bottom: 8px;
    margin-top: 16px;
}
</style>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="section-label">System Status</div>', unsafe_allow_html=True)

    monday_ok = bool(os.environ.get("MONDAY_API_KEY"))
    boards_ok = bool(os.environ.get("MONDAY_WORK_ORDERS_BOARD_ID")) and bool(os.environ.get("MONDAY_DEALS_BOARD_ID"))
    gemini_ok = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

    def status_pill(label, ok):
        cls = "status-ok" if ok else "status-err"
        icon = "●" if ok else "○"
        return f'<div class="{cls}">{icon} {label}</div>'

    st.markdown(
        status_pill("monday.com connected", monday_ok) +
        status_pill("Board IDs configured", boards_ok) +
        status_pill("Gemini API ready", gemini_ok),
        unsafe_allow_html=True
    )

    st.markdown('<div class="section-label">Data Sources</div>', unsafe_allow_html=True)
    st.markdown("""
    <div style="font-size:0.82rem; color:#475569; line-height:1.8;">
    📋 <b>Work Orders</b> — execution tracker<br>
    💼 <b>Deal Funnel</b> — sales pipeline
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-label">Actions</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 New Chat", use_container_width=True):
            st.session_state.pop("chat", None)
            st.session_state.pop("display_history", None)
            st.rerun()
    with col2:
        if st.button("⟳ Refresh Data", use_container_width=True):
            get_store().refresh()
            st.session_state.pop("chat", None)
            st.session_state.pop("display_history", None)
            st.toast("Live data cache cleared.", icon="✅")
            st.rerun()

    st.markdown('<div class="section-label">About</div>', unsafe_allow_html=True)
    st.markdown("""
    <div style="font-size:0.78rem; color:#94a3b8; line-height:1.7;">
    Every answer is pulled <b>live</b> from monday.com.<br>
    No cached CSVs. No hardcoded numbers.
    </div>
    """, unsafe_allow_html=True)

# ── Main header ───────────────────────────────────────────────────────────────
st.markdown("""
<div class="header-banner">
    <div class="header-logo">🚁</div>
    <div class="header-text">
        <h1>Skylark Drones — Intelligence Hub</h1>
        <p>Ask founder-level questions about your pipeline, work orders, and revenue.</p>
    </div>
    <div class="header-badge">Live Data</div>
</div>
""", unsafe_allow_html=True)

# ── Guard ─────────────────────────────────────────────────────────────────────
if not (monday_ok and boards_ok and gemini_ok):
    st.warning(
        "⚙️ **Setup incomplete** — check the sidebar. "
        "Set **MONDAY_API_KEY**, **MONDAY_WORK_ORDERS_BOARD_ID**, "
        "**MONDAY_DEALS_BOARD_ID**, and **GEMINI_API_KEY** in your `.env` file."
    )
    st.stop()

# ── Session init ──────────────────────────────────────────────────────────────
if "chat" not in st.session_state:
    try:
        client = get_client()
        st.session_state.chat = new_chat(client)
        st.session_state.display_history = []
    except Exception as e:
        st.error(f"Failed to start agent: {e}")
        st.stop()

# ── Suggestion chips (only when no history) ───────────────────────────────────
if not st.session_state.get("display_history"):
    st.markdown('<div class="section-label">Suggested Questions</div>', unsafe_allow_html=True)
    suggestions = [
        "How's our mining sector pipeline this quarter?",
        "Which work orders are overdue or at risk?",
        "What's our total open deal value by sector?",
        "Summarize pipeline for a leadership update.",
        "Which deals are furthest along in the pipeline?",
        "Show me all ongoing work orders.",
    ]
    cols = st.columns(3)
    for i, s in enumerate(suggestions):
        with cols[i % 3]:
            if st.button(s, key=f"chip_{i}", use_container_width=True):
                st.session_state["prefill"] = s
                st.rerun()
    st.markdown("---")

# ── Render conversation ───────────────────────────────────────────────────────
for role, text in st.session_state.display_history:
    with st.chat_message(role):
        st.markdown(text)

# ── Handle prefilled chip input ───────────────────────────────────────────────
prefill = st.session_state.pop("prefill", None)

# ── Chat input ────────────────────────────────────────────────────────────────
user_msg = st.chat_input("Ask anything about your deals, pipeline, or work orders…")
if prefill:
    user_msg = prefill

if user_msg:
    st.session_state.display_history.append(("user", user_msg))
    with st.chat_message("user"):
        st.markdown(user_msg)

    with st.chat_message("assistant"):
        with st.spinner("Fetching live data and analysing…"):
            try:
                reply = run_agent_turn(st.session_state.chat, user_msg)
            except (MondayAPIError, RuntimeError) as e:
                err = str(e)
                if "gemini api error" in err.lower() or "client has been closed" in err.lower():
                    try:
                        client = get_client()
                        st.session_state.chat = new_chat(client)
                        reply = run_agent_turn(st.session_state.chat, user_msg)
                    except Exception as retry_e:
                        reply = f"⚠️ **Connection error:** {retry_e}\n\nTry clicking **New Chat** and asking again."
                elif "monday" in err.lower():
                    reply = f"⚠️ **monday.com error:** {e}\n\nCheck that your API token and board IDs are valid."
                else:
                    reply = f"⚠️ **Error:** {e}"
            except Exception as e:
                reply = f"⚠️ **Something went wrong:** {e}"
        st.markdown(reply)

    st.session_state.display_history.append(("assistant", reply))
