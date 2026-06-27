# Changelog — Equity Research Platform

## 2026-05-21
- Progetto concepito: idea iniziale di piattaforma equity research
- Scelta stack: Python backend + Next.js frontend + Supabase

## 2026-06-24 (Parte 1 — Backend)
- Built complete FastAPI backend from scratch
- 7-step modular data pipeline:
  - Price & stats (yfinance)
  - Fundamentals (yfinance + FMP fallback)
  - Peer comparison
  - SEC EDGAR insider activity (Form 4)
  - Earnings call transcripts (8-K EX-99)
  - Analyst consensus (Finnhub)
  - AI report generation (Claude API)
- Deployed backend to Render
- Migrated from SQLite to Supabase Postgres (Session Pooler)
- Added CORS middleware for Vercel frontend
- All 9 endpoints live and tested

## 2026-06-24 (Parte 2 — Frontend)
- Frontend (Next.js) pushed to Vercel by collaborator Dilan
- Added Finnhub for analyst consensus
- Fixed missing endpoints (GET /alerts, DELETE /alerts/{id}, GET /portfolio)

## 2026-06-25 (Parte 3 — Wiring & Features)
- Wired Research Report page to live backend (removed demo data)
- Added Alpha Vantage as primary fundamentals source
- Implemented exponential backoff on yfinance (2s/4s/8s)
- Added Supabase cache layer:
  - Fundamentals: 24h TTL
  - Price history: 30min TTL
  - Price live: no cache
- Extended fundamentals cache to 24h
- Wired Portfolio page to live backend
- Added add/remove positions UI in Portfolio
- Wired Weekly Brief to live backend
- Wired Alerts page to live backend
- Fixed alert check to use lightweight price fetch (was running full pipeline)
- Added Recent Searches chips (localStorage, max 6 tickers)
- Added full report cache (24h) — repeat lookups instant, zero Anthropic cost
- Added live price injection on cache hits
- Added 7-day Weekly Brief cache with password-protected force regeneration (ExtraPls)
- Added DELETE /report/{ticker}/cache endpoint
- Added "Something didn't load correctly? Retry" button with password
- Added "Did You Know?" finance education cards in loading screen (15-20s per card, 3s delay)
- Added cold-start warning message in loading screen
- Redesigned Research Report: 6 metric cards (Price, Valuation, Growth, Profitability, Financial Health, Financials TTM)
- Redesigned Weekly Brief with react-markdown (tables, colored P&L, warning blockquotes)
- Added freshness badge ("Cached X hours ago") to Weekly Brief
- Fixed duplicate title in Weekly Brief
- Negotiated 30% student discount with FMP
- Renamed backend repo to equity-research-backend-ryanappuhamy

## 2026-06-26 (Parte 3 continua)
- Added caching for SEC EDGAR insider activity (24h)
- Added caching for Finnhub analyst consensus (24h)  
- Added caching for SEC EDGAR earnings transcript (7 days)
- Added caching for FRED macro data (24h)
- Fixed insider activity table rendering in frontend
