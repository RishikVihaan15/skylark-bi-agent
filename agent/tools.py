"""
Tools the agent can call. Two design choices worth calling out (also in the
Decision Log):

1. Instead of pre-writing a fixed function for every possible business
   question (get_revenue_by_sector, get_overdue_orders, ...), we expose one
   constrained `run_analysis` tool that lets the agent write a short pandas
   snippet against pre-loaded, pre-cleaned DataFrames. Founder questions are
   open-ended by nature ("how's pipeline looking for energy this quarter"),
   so a fixed query API would always be one step behind the actual question.
   The trade-off: less guardrail-by-construction than a narrow API, so we
   sandbox execution (no builtins, no imports, no file/network access) and
   this is read-only over data that's not sensitive/PII.

2. `get_distinct_values` exists specifically so the agent can *see* messy
   naming variants (e.g. "Energy", "energy ", "ENERGY SECTOR") before
   filtering, instead of silently missing rows due to inconsistent text.
"""
from __future__ import annotations
import os
import io
import contextlib
import pandas as pd

from .monday_client import MondayClient, MondayAPIError
from .data_normalizer import items_to_dataframe, auto_clean, data_quality_report, distinct_values


class DataStore:
    """Lazily fetches + cleans both boards once per process, then caches."""

    def __init__(self):
        self._client = None
        self._work_orders_df = None
        self._deals_df = None
        self._errors = {}

    @property
    def client(self):
        if self._client is None:
            self._client = MondayClient()
        return self._client

    def _load(self, board_id: str, label: str) -> pd.DataFrame:
        # Read board IDs fresh at call time so env vars set after import are honoured
        bid = board_id or os.environ.get(f"MONDAY_{label.upper()}_BOARD_ID")
        if not bid:
            raise MondayAPIError(
                f"MONDAY_{label.upper()}_BOARD_ID is not set. Add it as an environment variable."
            )
        items = self.client.get_board_items(bid)
        df = items_to_dataframe(items)
        return auto_clean(df)

    @property
    def work_orders_df(self) -> pd.DataFrame:
        if self._work_orders_df is None:
            self._work_orders_df = self._load(
                os.environ.get("MONDAY_WORK_ORDERS_BOARD_ID", ""), "work_orders"
            )
        return self._work_orders_df

    @property
    def deals_df(self) -> pd.DataFrame:
        if self._deals_df is None:
            self._deals_df = self._load(
                os.environ.get("MONDAY_DEALS_BOARD_ID", ""), "deals"
            )
        return self._deals_df

    def refresh(self):
        self._work_orders_df = None
        self._deals_df = None


# Module-level singleton so both the tool functions and Streamlit share one cache per session.
# Created lazily on first access so env vars are guaranteed to be set by then.
_store: DataStore | None = None

def get_store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store

# Convenience alias kept for any external callers that used the old name
def _get_legacy_store():
    return get_store()


def tool_get_schema_overview() -> dict:
    """Column names + row count only (no sample rows) to keep token usage low."""
    s = get_store()
    out = {}
    for label, df in (("work_orders", s.work_orders_df), ("deals", s.deals_df)):
        cols = [c for c in df.columns if not c.endswith(("__raw", "__norm"))]
        out[label] = {"columns": cols, "row_count": len(df)}
    return out


def tool_get_data_quality_report(board: str) -> dict:
    s = get_store()
    df = s.work_orders_df if board == "work_orders" else s.deals_df
    return data_quality_report(df)


def tool_get_distinct_values(board: str, column: str, limit: int = 30) -> list[str]:
    s = get_store()
    df = s.work_orders_df if board == "work_orders" else s.deals_df
    return distinct_values(df, column, limit)


def tool_run_analysis(code: str) -> str:
    """
    Execute a short pandas snippet in a restricted namespace. The snippet
    must assign its answer to a variable called `result`. work_orders_df and
    deals_df are pre-loaded and pre-cleaned (dates parsed, numbers parsed,
    text stripped with a __norm lowercase/whitespace-normalized sibling
    column for fuzzy matching on messy sector/status names).
    """
    s = get_store()
    safe_builtins = {
        "len": len, "sum": sum, "min": min, "max": max, "round": round,
        "sorted": sorted, "list": list, "dict": dict, "set": set,
        "str": str, "float": float, "int": int, "abs": abs, "range": range,
        "enumerate": enumerate, "zip": zip, "True": True, "False": False, "None": None,
    }
    local_ns = {
        "pd": pd,
        "work_orders_df": s.work_orders_df,
        "deals_df": s.deals_df,
    }
    global_ns = {"__builtins__": safe_builtins}

    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, global_ns, local_ns)
    except Exception as e:  # noqa: BLE001 - deliberately broad; surfaced to the agent, not raised
        return f"ERROR running analysis code: {e}\nCaptured stdout: {stdout.getvalue()}"

    result = local_ns.get("result", None)
    printed = stdout.getvalue()
    if isinstance(result, pd.DataFrame):
        result_str = result.to_string(max_rows=20)
    elif result is None:
        result_str = "(no `result` variable was set by the code)"
    else:
        result_str = str(result)[:2000]  # cap plain text results

    output = f"result:\n{result_str}"
    if printed:
        output += f"\nstdout:\n{printed[:500]}"
    return output


TOOL_DEFINITIONS = [
    {
        "name": "get_schema_overview",
        "description": "Get the column names, row counts, and a few sample rows for both boards (work_orders and deals). Call this first, before writing any analysis code, to see what's actually available.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_data_quality_report",
        "description": "Get missing-value counts/percentages per column for a board. Use this to caveat answers when data is incomplete.",
        "input_schema": {
            "type": "object",
            "properties": {"board": {"type": "string", "enum": ["work_orders", "deals"]}},
            "required": ["board"],
        },
    },
    {
        "name": "get_distinct_values",
        "description": "List the distinct raw values in a text column (e.g. a 'Sector' or 'Status' column) so you can see messy naming variants (casing, whitespace, synonyms) BEFORE filtering on that column.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "enum": ["work_orders", "deals"]},
                "column": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["board", "column"],
        },
    },
    {
        "name": "run_analysis",
        "description": (
            "Execute a short pandas snippet to answer a business question. "
            "`work_orders_df` and `deals_df` are pre-loaded, cleaned pandas DataFrames "
            "(dates parsed to datetime, currency/number columns parsed to float, text "
            "columns stripped, plus a '<col>__norm' lowercase/whitespace-normalized sibling "
            "for each text column to match messy naming variants). "
            "You MUST assign your final answer to a variable named `result`. "
            "No imports, no file/network access, no builtins beyond basic Python. "
            "Example: result = deals_df[deals_df['Deal Status__norm'].str.contains('open')]['Masked Deal value'].sum()"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python/pandas code, must set `result`."}},
            "required": ["code"],
        },
    },
]


def dispatch_tool(name: str, tool_input: dict) -> str:
    try:
        if name == "get_schema_overview":
            return str(tool_get_schema_overview())
        if name == "get_data_quality_report":
            return str(tool_get_data_quality_report(tool_input["board"]))
        if name == "get_distinct_values":
            return str(tool_get_distinct_values(tool_input["board"], tool_input["column"], tool_input.get("limit", 30)))
        if name == "run_analysis":
            return tool_run_analysis(tool_input["code"])
        return f"Unknown tool: {name}"
    except MondayAPIError as e:
        return f"monday.com API error: {e}"
    except Exception as e:  # noqa: BLE001 - surfaced to the agent as a tool result, not a crash
        return f"Tool error: {e}"
