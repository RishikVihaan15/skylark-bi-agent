# Skylark Drones — monday.com Business Intelligence Agent

A conversational agent that answers founder-level business questions by querying two live
monday.com boards (Work Orders, Deals) — no hardcoded data, no cached CSVs.

## Architecture

```
User (Streamlit chat)
      │
      ▼
Gemini 2.0 Flash (tool-use loop — agent/agent.py)
      │  calls tools as needed:
      ├─ get_schema_overview        → confirm real column names before querying
      ├─ get_distinct_values        → inspect messy text values before filtering
      ├─ get_data_quality_report    → check completeness to caveat answers honestly
      └─ run_analysis               → sandboxed pandas snippet over cleaned DataFrames
              │
              ▼
      DataStore (agent/tools.py)  — lazy-loaded, session-cached cleaned DataFrames
              │
              ▼
      data_normalizer.py — date parsing, numeric parsing, text normalization,
                           header-row deduplication, data quality reporting
              │
              ▼
      monday_client.py — GraphQL calls to api.monday.com/v2 (paginated, retried)
              │
              ▼
         monday.com boards (Work Orders ID: 5030843406, Deals ID: 5030843371)
```

**Why this shape:** founder questions are open-ended ("how's pipeline looking for mining this
quarter?"), so a fixed query API is always one question behind. The agent gets a sandboxed
`run_analysis` tool (pandas exec over pre-cleaned data) plus inspection tools so it can see
real column names and messy value variants before computing — not guessing. Full reasoning is
in `DECISION_LOG.md`.

## Live boards — what's in them

| Board | Rows | Key columns |
|---|---|---|
| Work Orders | 176 | Sector, Execution Status, Nature of Work, Date of PO/LOI, Probable Start/End Date, financial amounts in INR (masked) |
| Deals | 346 | Deal Status, Deal Stage, Sector/service, Masked Deal value, Tentative Close Date, Created Date |

**Known data caveats (surfaced by the agent automatically):**
- Deals `Close Date (A)` is 92.5% empty — agent uses `Tentative Close Date` for time filters.
- Deals `Masked Deal value` is 52.3% empty — pipeline totals are structurally undercounted.
- Work Orders `Data Delivery Date` is 67% empty.

## Setup

### 1. monday.com

The two boards are already live. To replicate from scratch:

1. Import `Work_Order_Tracker Data.xlsx` and `Deal funnel Data.xlsx` as two separate boards
   (File → Import). Use monday's auto-detected column types as a starting point.
2. Get an API token: avatar → **Admin** → **API** (or **Developers**) → generate a personal token.
3. Get each board's ID from the URL: `https://<you>.monday.com/boards/<ID>`.

### 2. Gemini (free, no billing required)

Get a free API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — sign
in with Google, click "Create API key". No credit card needed.

### 3. Local run

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # fill in your real keys/IDs

# Windows (PowerShell):
Get-Content .env | ForEach-Object { if ($_ -notmatch '^#' -and $_ -match '=') { $k,$v = $_ -split '=',2; [System.Environment]::SetEnvironmentVariable($k.Trim(),$v.Trim()) } }
# Mac/Linux:
export $(grep -v '^#' .env | xargs)

streamlit run ui/app.py
```

### 4. Deploy to Streamlit Community Cloud (free, public URL)

1. Push this repo to GitHub (public or private).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → point at this repo,
   main file path `ui/app.py`.
3. In the app's **Settings → Secrets**, add:
   ```toml
   GOOGLE_API_KEY = "AIza..."
   MONDAY_API_KEY = "eyJ..."
   MONDAY_WORK_ORDERS_BOARD_ID = "5030843406"
   MONDAY_DEALS_BOARD_ID = "5030843371"
   ```
4. Deploy. The app reads these via `os.environ` which Streamlit Cloud populates from Secrets.

## Repo layout

```
agent/
  monday_client.py     # GraphQL client (auth, pagination, retry)
  data_normalizer.py   # date/numeric/text cleaning; leaked-header dedup; quality report
  tools.py             # tool implementations + lazy DataStore cache
  agent.py             # Gemini tool-use loop + system prompt
ui/
  app.py               # Streamlit chat interface (session-scoped cache, refresh button)
DECISION_LOG.md        # assumptions, trade-offs, bugs fixed, what's next
README.md              # this file
requirements.txt
.env.example
```

## Known limitations

See `DECISION_LOG.md` — specifically: no TTL cache refresh, no cross-board join, sandbox uses
restricted builtins rather than full AST validation.
