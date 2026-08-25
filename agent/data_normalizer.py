"""
Turns raw monday.com items into a clean pandas DataFrame, and reports on
data quality along the way so the agent can be honest with the user about
gaps/caveats instead of silently guessing.

Design choice (documented in Decision Log): we don't try to guess a fixed
schema. We build a DataFrame column-per-monday-column using the item's
column titles, then apply generic cleaning heuristics (date parsing,
currency/number parsing, whitespace/case normalization for text) that work
regardless of exactly how the CSVs were imported. Every cleaned column keeps
a "<col>__raw" sibling with the original text, so nothing is silently lost.
"""
from __future__ import annotations
import re
import pandas as pd
from dateutil import parser as dateparser

_CURRENCY_RE = re.compile(r"[^\d.\-]")


def _try_parse_date(val: str):
    if not val or not str(val).strip():
        return pd.NaT
    try:
        return dateparser.parse(str(val), fuzzy=True, dayfirst=False)
    except (ValueError, OverflowError, TypeError):
        return pd.NaT


def _try_parse_number(val: str):
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    cleaned = _CURRENCY_RE.sub("", s)
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def items_to_dataframe(items: list[dict]) -> pd.DataFrame:
    """
    Convert monday.com items (as returned by MondayClient.get_board_items)
    into a wide DataFrame: one row per item, one column per monday column
    title (using the human-readable `text` value), plus 'item_name'/'item_id'.

    Drops any row where item_name exactly matches a column title — a known
    monday.com CSV-import artifact where the header row leaks in as an item.
    """
    rows = []
    col_titles: set[str] = set()
    for item in items:
        row = {"item_id": item["id"], "item_name": item.get("name", "")}
        for cv in item.get("column_values", []):
            title = cv["column"]["title"] if cv.get("column") else cv["id"]
            col_titles.add(title)
            row[title] = cv.get("text") or ""
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty and col_titles:
        # Remove rows whose item_name is identical to any column title — leaked header rows
        leaked = df["item_name"].isin(col_titles)
        if leaked.any():
            df = df[~leaked].reset_index(drop=True)
    return df


def auto_clean(df: pd.DataFrame, date_cols: list[str] | None = None,
                numeric_cols: list[str] | None = None,
                text_cols: list[str] | None = None) -> pd.DataFrame:
    """
    Apply best-effort cleaning. Callers can pass explicit column lists (once
    they know the board schema); otherwise this makes a light-touch guess
    based on column names, which is good enough for BI purposes and always
    keeps the raw value alongside the cleaned one.

    Ordering matters: numeric detection runs before date detection so columns
    like "Quantity billed (till date)" aren't mis-parsed as dates.
    """
    df = df.copy()
    # Numeric first — columns with currency/quantity keywords, even if "date" appears in their name
    numeric_cols = numeric_cols or [
        c for c in df.columns
        if re.search(r"value|amount|revenue|cost|price|budget|\$|quantity|qty|rupees|billed|collected|receivable|balance", c, re.I)
    ]
    # Date: only columns whose *primary* purpose is a date — not ones already claimed by numeric
    date_cols = date_cols or [
        c for c in df.columns
        if c not in numeric_cols
        and re.search(r"\bdate\b|\bdeadline\b|\bclosed\b|\bstart\b|\bend\b", c, re.I)
    ]
    text_cols = text_cols or [
        c for c in df.columns
        if c not in date_cols and c not in numeric_cols and c not in ("item_id", "item_name")
    ]

    for c in date_cols:
        if c in df.columns:
            df[f"{c}__raw"] = df[c]
            df[c] = df[c].apply(_try_parse_date)

    for c in numeric_cols:
        if c in df.columns:
            df[f"{c}__raw"] = df[c]
            df[c] = df[c].apply(_try_parse_number)

    for c in text_cols:
        if c in df.columns:
            df[f"{c}__raw"] = df[c]
            df[c] = df[c].astype(str).str.strip()
            df[f"{c}__norm"] = df[c].str.lower().str.replace(r"[^a-z0-9]+", " ", regex=True).str.strip()

    return df


def data_quality_report(df: pd.DataFrame, key_cols: list[str] | None = None) -> dict:
    """
    Summarize completeness so the agent can surface caveats to the user
    rather than pretending the data is clean.
    """
    cols = key_cols or [c for c in df.columns if not c.endswith(("__raw", "__norm"))]
    report = {}
    n = len(df)
    for c in cols:
        if c not in df.columns:
            continue
        missing = df[c].isna().sum() if df[c].dtype != object else (df[c].astype(str).str.strip() == "").sum()
        report[c] = {
            "missing_count": int(missing),
            "missing_pct": round(100 * missing / n, 1) if n else 0.0,
        }
    return {"row_count": n, "columns": report}


def distinct_values(df: pd.DataFrame, column: str, limit: int = 30) -> list[str]:
    """Helps the agent inspect messy naming variants before filtering on a text field."""
    if column not in df.columns:
        return []
    return sorted(df[column].dropna().astype(str).str.strip().unique().tolist())[:limit]
