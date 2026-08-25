import os
import sys

import streamlit as st
from dotenv import load_dotenv

# Load .env from the project root (one level above ui/)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import get_client, new_chat, run_agent_turn
from agent.monday_client import MondayAPIError
from agent.tools import DataStore, get_store

st.set_page_config(page_title="Skylark BI Agent", page_icon="📊", layout="centered")
st.title("📊 Skylark Drones — Business Intelligence Agent")
st.caption(
    "Ask founder-level questions about work orders and deals. "
    "Every answer is pulled live from monday.com — no cached CSVs."
)

# ── Sidebar: status + controls ────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Setup status")
    monday_ok  = bool(os.environ.get("MONDAY_API_KEY"))
    boards_ok  = bool(os.environ.get("MONDAY_WORK_ORDERS_BOARD_ID")) and bool(os.environ.get("MONDAY_DEALS_BOARD_ID"))
    gemini_ok  = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    st.write("monday.com token:", "✅" if monday_ok  else "❌ missing MONDAY_API_KEY")
    st.write("Board IDs:",        "✅" if boards_ok  else "❌ missing MONDAY_WORK_ORDERS_BOARD_ID / MONDAY_DEALS_BOARD_ID")
    st.write("Gemini API key:",   "✅" if gemini_ok  else "❌ missing GEMINI_API_KEY")
    st.divider()

    st.markdown(
        "**Try asking:**\n"
        "- How's our pipeline looking for the mining sector this quarter?\n"
        "- Which work orders are overdue or at risk?\n"
        "- Summarize this for a leadership update.\n"
        "- What's our total open deal value by sector?"
    )
    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Reset chat"):
            st.session_state.pop("chat", None)
            st.session_state.pop("display_history", None)
            st.rerun()
    with col2:
        if st.button("Refresh data"):
            # Drop cached DataFrames so the next tool call re-fetches from monday.com
            get_store().refresh()
            st.session_state.pop("chat", None)
            st.session_state.pop("display_history", None)
            st.success("Data cache cleared.")
            st.rerun()

# ── Guard: refuse to start without required env vars ─────────────────────────
if not (monday_ok and boards_ok and gemini_ok):
    st.warning(
        "Missing required environment variables — see the sidebar. "
        "The agent can't run until **MONDAY_API_KEY**, **MONDAY_WORK_ORDERS_BOARD_ID**, "
        "**MONDAY_DEALS_BOARD_ID**, and **GEMINI_API_KEY** are all set."
    )
    st.stop()

# ── Session state: Gemini chat session ───────────────────────────────────────
if "chat" not in st.session_state:
    try:
        client = get_client()  # validates GEMINI_API_KEY is set
        st.session_state.chat = new_chat(client)
        st.session_state.display_history = []
    except Exception as e:
        st.error(f"Failed to start agent: {e}")
        st.stop()

# ── Render existing conversation ─────────────────────────────────────────────
for role, text in st.session_state.display_history:
    with st.chat_message(role):
        st.markdown(text)

# ── Handle new user input ─────────────────────────────────────────────────────
user_msg = st.chat_input("Ask a business question...")
if user_msg:
    st.session_state.display_history.append(("user", user_msg))
    with st.chat_message("user"):
        st.markdown(user_msg)

    with st.chat_message("assistant"):
        with st.spinner("Querying monday.com and thinking..."):
            try:
                reply = run_agent_turn(st.session_state.chat, user_msg)
            except (MondayAPIError, RuntimeError) as e:
                err = str(e)
                if "client has been closed" in err.lower() or "gemini api error" in err.lower():
                    try:
                        client = get_client()
                        st.session_state.chat = new_chat(client)
                        reply = run_agent_turn(st.session_state.chat, user_msg)
                    except Exception as retry_e:
                        reply = f"⚠️ **Gemini connection error:** {retry_e}\n\nTry clicking Reset chat and asking again."
                elif "monday" in err.lower():
                    reply = f"⚠️ **monday.com error:** {e}\n\nCheck that the API token and board IDs are still valid."
                else:
                    reply = f"⚠️ **Configuration error:** {e}"
            except Exception as e:  # noqa: BLE001 — surfaced to the user; app must not crash
                reply = f"⚠️ **Something went wrong:** {e}"
        st.markdown(reply)
    st.session_state.display_history.append(("assistant", reply))
