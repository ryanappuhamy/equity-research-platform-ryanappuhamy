# Equity Research Platform — Backend (v2)

A Python backend that turns free, official market data into institutional-style equity research — then layers portfolio tracking, risk analysis, alerts, and scheduled briefs on top.

**For recruiters:** this is a full-stack *research engine*, not a quote widget. It ingests prices, SEC filings, macro data, and earnings materials; separates objective facts from AI interpretation; and exposes everything through a REST API and automated weekly jobs.

**For developers:** clone, set API keys, run the CLI or FastAPI server, and you get JSON + markdown outputs suitable for a frontend or your own workflows.

---

## Why not just Yahoo Finance or Investing.com?

| | Yahoo Finance / Investing.com | This platform |
|---|---|---|
| **Data model** | Quotes, charts, news headlines | Structured pipeline: fundamentals, peer comps, SEC insider activity, earnings transcripts, macro context |
| **Interpretation** | Aggregated news and generic summaries | Claude-generated research notes *constrained to input data*, clearly labeled as AI interpretation |
| **Portfolio** | Watchlists and basic P&L | SQLite-backed positions with live P&L, weights, correlation matrix, scenario analysis |
| **Automation** | Email price alerts (platform-defined) | User-defined metric alerts + weekly brief scheduler that writes `output/weekly_brief_<date>.md` |
| **Transparency** | Opaque vendor data | Objective sources cited (SEC EDGAR, FRED, reported financials); analyst consensus passed through with attribution |
| **API** | Limited / terms-restricted | Your own FastAPI server — every feature available as JSON |

Sites like Yahoo Finance and Investing.com excel at *browsing* markets. This project excels at *researching a name*, *tracking a portfolio*, and *automating follow-ups* — with a clear audit trail of where each number came from.

**Design principle:** separation of fact and interpretation. Numbers from APIs and filings are stored as-is. AI output is labeled and instructed never to invent figures not present in the input.

---

## Architecture

| Module | Role |
|--------|------|
| `main.py` | CLI entry point; `run_pipeline()` orchestrates the 7-step single-ticker workflow |
| `config.py` | Environment-based API keys and analysis settings |
| `api.py` | FastAPI REST server for reports, portfolio, alerts |
| `data_fundamentals.py` | Prices (yfinance) + fundamentals (FMP primary, yfinance fallback) |
| `data_sec.py` | SEC EDGAR Form 4 insider activity |
| `data_earnings.py` | SEC EDGAR 8-K earnings transcripts / EX-99 exhibits |
| `data_macro.py` | FRED macro context (rates, CPI, unemployment) |
| `peer_comparison.py` | Comps table + relative valuation vs peer median |
| `ai_report.py` | Claude API — research notes, transcript analysis, portfolio briefs; template fallback |
| `portfolio.py` | SQLite portfolio tracker (positions, P&L, weights) |
| `portfolio_risk.py` | 1-year risk decomposition, correlation, scenario analysis |
| `alerts.py` | User-defined alert rules; pipeline-backed condition checking |
| `scheduler.py` | Weekly brief job (Monday 08:00) — analysis + brief + triggered alerts |
| `run_scheduler.py` | Long-running scheduler process |

### Single-ticker pipeline (7 steps)

1. Price history & stats  
2. Fundamentals  
3. Peer comparison  
4. SEC insider activity  
5. Earnings transcript fetch + AI analysis  
6. Analyst consensus & macro context  
7. AI research note generation  

Outputs: `output/<TICKER>_report.md` and `output/<TICKER>_data.json`

---

## Data sources

| Source | Cost | What it provides |
|--------|------|------------------|
| **yfinance** | Free | Daily prices, fallback fundamentals, portfolio live prices |
| **Financial Modeling Prep** | ~$14/mo (optional) | Clean fundamentals, peer lists, analyst consensus |
| **SEC EDGAR** | Free | Form 4 insider filings; 8-K earnings transcripts / exhibits |
| **FRED** | Free (API key) | Fed funds, 10Y yield, CPI YoY, unemployment |
| **Anthropic (Claude)** | Pay per use | Research notes, transcript analysis, portfolio briefs |
| **SQLite** | Free | Local storage (`portfolio.db`) for positions and alerts |

Works with **zero paid keys** (yfinance + SEC only). FMP unlocks peers and analyst data; Claude unlocks AI interpretation (template reports used as fallback).

---

## Setup

```bash
git clone https://github.com/ryanappuhamy/equity-research-platform-ryanappuhamy.git
cd equity-research-platform-ryanappuhamy
pip install -r requirements.txt
```

Set environment variables (PowerShell):

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # optional — AI reports & briefs
$env:FMP_API_KEY = "..."                # optional — peers & analyst consensus
$env:FRED_API_KEY = "..."               # optional — macro context (free at fred.stlouisfed.org)
```

Optional: `$env:PORTFOLIO_DB = "portfolio.db"` to change the SQLite database path.

---

## Usage

### 1. Single-ticker research (CLI)

```bash
python main.py NVDA
python main.py NVDA --peers AMD,AVGO,MRVL,INTC,QCOM
```

### 2. REST API

Start the server:

```bash
uvicorn api:app --reload
```

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

#### Reports

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/report/{ticker}` | Full pipeline → JSON (`report` + `data`). Optional query: `?peers=AMD,INTC` |

```bash
curl http://127.0.0.1:8000/report/NVDA
curl "http://127.0.0.1:8000/report/NVDA?peers=AMD,AVGO"
```

#### Portfolio

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/portfolio` | Save portfolio (replaces existing default portfolio) |
| GET | `/portfolio/analysis` | Risk decomposition — correlation, vol, scenarios |
| GET | `/portfolio/brief` | AI weekly brief for all holdings |

**Add positions** — POST replaces the entire portfolio with your positions list:

```bash
curl -X POST http://127.0.0.1:8000/portfolio \
  -H "Content-Type: application/json" \
  -d '{
    "positions": [
      {"ticker": "NVDA", "shares": 10, "avg_cost_price": 120.50},
      {"ticker": "AAPL", "shares": 25, "avg_cost_price": 175.00}
    ]
  }'
```

**Analyze portfolio:**

```bash
curl http://127.0.0.1:8000/portfolio/analysis
curl http://127.0.0.1:8000/portfolio/brief
```

From Python:

```python
import portfolio
portfolio.add_position("NVDA", shares=10, avg_cost_price=120.50)
portfolio.get_portfolio()       # live prices, P&L, weights
portfolio.update_prices()
```

#### Alerts

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/alerts` | Create an alert rule (duplicates return the existing rule) |
| GET | `/alerts/check` | Evaluate all rules against current portfolio data |

**Supported metrics:** `pe_ttm`, `price`, `revenue_growth_yoy`, `insider_filings`  
**Operators:** `above`, `below`

**Set an alert:**

```bash
curl -X POST http://127.0.0.1:8000/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "NVDA",
    "metric": "pe_ttm",
    "operator": "above",
    "threshold": 45
  }'
```

**Check triggered alerts** (runs the full pipeline per portfolio ticker):

```bash
curl http://127.0.0.1:8000/alerts/check
```

From Python:

```python
import alerts
alerts.add_alert("NVDA", "price", "below", 100)
alerts.get_alerts()
alerts.check_alerts(["NVDA", "AAPL"])
alerts.delete_alert(1)
alerts.clear_alerts()
```

### 3. Weekly brief scheduler

Runs every **Monday at 08:00** (local time). Loads the saved portfolio, runs risk analysis, checks alerts, generates the AI brief, and writes:

```
output/weekly_brief_YYYY-MM-DD.md
```

**Start the scheduler** (runs in foreground; use a process manager in production):

```bash
python run_scheduler.py
```

**Run once immediately**, then keep scheduling:

```bash
python run_scheduler.py --now
```

The weekly brief includes: holdings summary, risk analysis, **triggered alerts**, and the AI brief.

---

## Error handling

All modules fail gracefully — no unhandled exceptions in normal operation. Missing data returns `{"available": false, "note": "..."}`. Claude API failures fall back to template reports. Errors are logged with `[error]` print statements.

---

## Roadmap

| Version | Scope |
|---------|--------|
| **v1** | Single-ticker CLI pipeline |
| **v2 (current)** | Earnings transcripts, portfolio tracker, risk decomposition, alerts, FastAPI, weekly scheduler, robust error handling |
| **v3** | Web frontend, cloud deploy, multi-portfolio support, factor exposure, AI rebalancing notes |

---

## Disclaimer

Educational project. Not financial advice. Past performance and scenario analysis do not guarantee future results. Verify all data against primary sources before making investment decisions.
