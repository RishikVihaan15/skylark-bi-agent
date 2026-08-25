# Decision Log — Skylark Drones BI Agent

## Key assumptions

- **Board structure**: the two CSVs (Work Orders, Deals) were imported as two separate monday.com
  boards. The agent doesn't assume fixed column names — it calls `get_schema_overview` at the
  start of a session to read the real ones. All findings below are from probing the live boards
  directly, not the CSV files.
- **"This quarter" / relative time references**: interpreted relative to `pd.Timestamp.now()`
  at query time so the agent stays useful beyond the assignment window. When genuinely ambiguous,
  the agent asks rather than guessing.
- **"Pipeline health"**: open/active deals (Deal Status == 'Open') unless the question implies
  otherwise (e.g. "how much have we closed" clearly wants Won).
- **Read-only**: the agent never writes back to monday.com.

## What the real boards actually look like (verified via live API)

| Board | Rows | Key data issues found |
|---|---|---|
| Work Orders | 176 | `Quantity billed (till date)` has "date" in its name but is numeric — old regex mis-parsed it as dates. 67% of `Data Delivery Date` is missing. All financial amounts in INR (no currency symbols in the raw text — plain floats). |
| Deals | 346 | `Close Date (A)` is 92.5% missing — `Tentative Close Date` is the actionable date column. `Masked Deal value` is 52.3% missing, so pipeline totals structurally undercount. Deal Status / Sector/service / Deal Stage each contain a leaked header row (`'Deal Status'`, `'Sector/service'`, `'Deal Stage'`) from the CSV import — these must be filtered out before counting. |

## Bugs fixed during hardening

| Bug | Impact | Fix |
|---|---|---|
| `auto_clean` date regex matched "Quantity billed (till **date**)" | 7 numeric values silently parsed as NaT (missing), corrupting quantity analysis | Numeric detection now runs before date detection; date regex uses word-boundary `\bdate\b` and won't fire on columns that already matched a numeric keyword |
| Leaked header rows in Deals board (`'Deal Status'` etc. as cell values) | Status/sector filters over-count or produce junk totals | `items_to_dataframe` now drops any row whose `item_name` equals a column title — the monday.com import artifact pattern |
| `DataStore` was a module-level singleton instantiated at import time | If env vars weren't set yet (e.g. Streamlit Cloud secrets loaded after import), the store initialized with `None` board IDs and raised on first use | Replaced with a `get_store()` lazy factory; board IDs are read from `os.environ` at call time, not at module load |
| `run_analysis` tool description had a broken example | `df.groupby('Status')['Value']['__raw'].count()` — double indexing syntax error | Fixed to a working example using the real column names from the live boards |
| System prompt used invented column names | Agent would generate analysis code with wrong column names, causing `KeyError` in `run_analysis` | Prompt now contains the verified real column names, known status values, and data caveats for each board |

## Trade-offs

| Decision | Why | Trade-off accepted |
|---|---|---|
| GraphQL API instead of MCP | Faster to stand up correctly in the time available; monday's MCP server adds a hosting/auth layer without changing what data is accessible. | If Skylark's tooling standardizes on MCP, only `monday_client.py` needs re-plumbing — the agent/tool layer is provider-agnostic. |
| One general `run_analysis` (sandboxed pandas) instead of fixed query functions | Founder questions are open-ended; a fixed API is always one question behind. | Less guardrail-by-construction. Mitigated by sandboxing (no builtins/imports) and the data being read-only internal data. With more time: AST-based validator to block attribute access (`__class__`, etc). |
| Fuzzy text matching via LLM + `get_distinct_values`, not a synonym table | Messy variants aren't predictable in advance. Letting the agent see the real distinct values and reason about grouping is more robust. | Non-deterministic. Every answer states which values it treated as equivalent, so it's checkable. |
| Gemini 2.0 Flash, single tool-use loop | Free tier (no billing setup), correct tool-use support. The task is one loop: understand → inspect data → compute → explain. | For much larger boards, a separate data-processing stage would scale better. Only `agent.py` touches the LLM; swapping providers later is a one-file change. |
| Streamlit for UI + Streamlit Community Cloud for hosting | Fastest path to a publicly accessible conversational interface with session state built in. | Less customizable than a bespoke frontend; fine for a prototype where the evaluation is on the agent's reasoning. |

## How I interpreted "helping prepare data for leadership updates"

Same conversational agent, different behavior when triggered: the system prompt directs it to
produce a tight, skimmable summary — headline numbers first, then 2–4 notable trends or risks,
then only the data caveats worth flagging — formatted to paste directly into a status email or
slide. This keeps it consistent with everything else the agent does (same live data, same caveat
discipline) rather than a bolted-on separate feature.

## What I'd do differently / add with more time

- **Stricter sandboxing**: AST validation on `run_analysis` code before exec, not just a
  restricted builtins dict. Block `__class__`, `__globals__`, attribute traversal chains.
- **TTL cache**: boards are fetched once per Streamlit session. A 5-minute TTL would keep data
  fresh in long-running conversations without re-fetching on every message.
- **Cross-board join tool**: Work Orders and Deals share `Serial #` / `SDPLDEAL-*` identifiers.
  A fuzzy-join tool would answer questions that span both boards (e.g. "which won deals haven't
  started execution?") without relying on the LLM eyeballing two separate result sets.
- **monday.com column type hints**: `get_board_schema` already fetches each column's `type`
  field (date / numbers / status / text). Wiring this into `auto_clean` would make type
  detection completely schema-driven rather than name-based, eliminating the regex heuristics
  entirely.
- **Automated regression tests** against a small fixture dataset for the normalization logic —
  currently validated manually. Would become a pytest suite.

## AI tools used

Kiro (AI coding assistant) for pair-programming the architecture, hardening, and this log.
Gemini 2.0 Flash is the LLM powering the deployed agent itself (see trade-off table above).
