# Macro AI Lab — Upgrade Plan

**Goal:** Strengthen the NQ & GC futures trading pipeline with better technicals, macro regime awareness, and adversarial reasoning. No new frameworks, no broker integration, no architecture changes.

---

## Phase 1 — Bug Fixes (unblocks everything)

### 1a. Fix ImportError — Remove Earnings Calendar from Trading Pipeline
**File:** `scripts/trading_analyst.py`  
**Problem:** Line 21 imports `fetch_economic_calendar` and `format_economic_calendar` which don't exist in `finnhub_client.py`. Pipeline crashes on every run before executing a single line.  
**Decision:** Do not replace with `fetch_earnings_calendar` / `format_earnings_calendar` — the earnings calendar is not useful in the trading pipeline:
- For **GC**: earnings are irrelevant to gold futures.
- For **NQ**: a single-day earnings lookup adds nothing — Section C news (Serper "NQ premarket outlook today") already surfaces any major earnings moving the market.
- The macro pipeline already covers the full weekly earnings calendar correctly.

**Fix:** Delete entirely — no replacement needed:
1. Remove the broken import on line 21
2. Remove `cal_events` and `calendar_md` from `main()` (lines 278–279)
3. Remove `calendar_md` parameter from `build_user_message()` signature and remove Section B from its body

**Verify:** `python3 trading_analyst.py --test` completes without ImportError. Trading prompt no longer has a Section B.

---

### 1b. Fix "Current Price" Mislabel
**File:** `scripts/trading_analyst.py` — `_fetch_levels()`  
**Problem:** `hist["Close"].iloc[-1]` is prior day's CME settlement, not a live price. LLM treats it as real-time and makes inaccurate intraday claims.  
**Fix:** Rename table row label from `Current Price` → `Prior Settlement`.  
**Verify:** Output table shows `Prior Settlement` row.

---

### 1c. Fix ETF Proxy Prices (Zero at 7 AM)
**File:** `scripts/lib/market_data.py`  
**Problem:** Finnhub returns `c=0` when equity markets are closed (outside 9:30 AM – 4:00 PM ET). If the pipeline runs outside those hours — which is the common case for a morning pre-market briefing — all 8 ETF proxies show "—". Section A is effectively empty on every pre-market run.  
**Fix:** When `c=0`, fall back to `pc` (previous close field from Finnhub). Label the column `Prev Close` so the LLM knows it is prior session data, not real-time.  
**Verify:** Run `macro_analyst.py --test` before market open — prices show prior close values instead of "—".

---

## Phase 2 — Technical Indicators

### 2a. Split Daily vs Weekly Pivot Computation
**File:** `scripts/trading_analyst.py` — `_fetch_levels()`  
**Problem:** Pivot is always computed from 5 daily bars (prior week H/L/C range). Intraday prompt receives weekly pivots and treats them as intraday reference levels — wrong time horizon.  
**Fix:** Add `mode` parameter to `_fetch_levels()`:
- `mode="intraday"` → pivot from single prior day bar (`hist.iloc[-1]`, the most recent completed session — H/L/C)
- `mode="weekly"` → keep current 5-bar logic (prior week H/L range)

Note: `hist.iloc[-1]` is the correct prior day bar. yfinance `history(interval="1d")` returns only completed bars, so iloc[-1] is yesterday's settlement. iloc[-2] would be two days ago — do not use it for daily pivot.

Pass `mode` from `main()` based on `args.mode`. Update `get_all_technical_levels()` to accept and forward `mode`.  
**Verify:** Intraday pivot PP ≠ weekly pivot PP for the same run date.

---

### 2b. Add RSI(14), ATR(14), MACD, Bollinger Bands
**File:** `scripts/trading_analyst.py` — `_fetch_levels()`  
**Dependency:** `stockstats` library (install: `pip install stockstats`, add to `requirements.txt`)  
**Method:** Wrap the existing yfinance OHLCV DataFrame with `stockstats.wrap()`, then access indicators as columns. All computed from the same data already fetched — no new API calls.

**Caveat:** `_fetch_levels()` currently discards the DataFrame after extracting scalar values. The refactor must retain the full DataFrame (or restructure the function) before passing it to `stockstats.wrap()`. The simplest approach: compute all scalars and indicators from the same `hist` DataFrame before building the markdown string.

New rows to add to the markdown table:

| Indicator | Column name | Purpose in prompt |
|:----------|:-----------|:-----------------|
| RSI(14) | `rsi` | Overbought (>70) / oversold (<30) flag |
| ATR(14) | `atr` | Stop-loss sizing relative to volatility |
| MACD line | `macd` | Momentum direction |
| MACD signal | `macds` | Crossover signal |
| Bollinger Upper | `boll_ub` | Breakout / overbought zone |
| Bollinger Lower | `boll_lb` | Breakdown / oversold zone |

**Verify:** Spot-check RSI value against a reference (TradingView or manual calc on same date).

---

## Phase 3 — Macro Regime Injection

### 3a. Cross-Pipeline Regime Signal
**File:** `scripts/trading_analyst.py` — `build_user_message()`  
**Problem:** Macro and trading pipelines are siloed. The macro report classifies the market as Risk-On / Risk-Off / Transitional. The trading report never sees this signal.  
**Fix:**
1. Add import: `from lib.report_store import load_previous_report` (already exists, used by `macro_analyst.py`)
2. In `main()`, call `macro_regime = load_previous_report("macro")` — reads from `reports/macro_YYYY-MM-DD.md`, returns only the STEP 3 Daily Synthesis section (~600–1000 chars). Load it the same way `prev_log` is already loaded in `main()`.
3. Add `macro_regime: str | None` as a new parameter to `build_user_message()`, following the existing pattern of `prev_log`.
4. Inside `build_user_message()`, inject `macro_regime` as **Section E — Macro Regime Context** before the closing instruction line.

Do NOT call `load_previous_report()` inside `build_user_message()` — the function currently does no I/O and all context is passed in as parameters. Keep it consistent.

Do NOT use `load_previous_log("macro")` — that function (`previous_log.py`) reads raw infrastructure debug logs from `logs/`, not the clean analysis. The correct source is `report_store.py` → `reports/`.

Note: Since there is no fixed run order, the recommended workflow is to run `macro_analyst.py` first, then `trading_analyst.py`. Section E will then contain today's macro regime. If trading is run first or standalone, Section E will contain yesterday's regime — still useful as baseline context.  
**Verify:** Section E appears in trading report output (will be empty on first run, populated once `macro_analyst.py` has run at least once).

---

## Phase 4 — Prompt Redesign: Bull/Bear Debate

### 4a. Intraday Prompt
**File:** `prompts/trading/intraday_nq_gc.md`  
**Change:** Insert a new Step 1.5 between "Market Context Assessment" and "Intraday Setups":

```
STEP 1.5 — ADVERSARIAL STRESS TEST
════════════════════════════════════════
For each instrument, before committing to a directional bias:

BULL CASE: State the 2–3 strongest reasons to be long today.
  What data, levels, or news support this?

BEAR CASE: Challenge every bull point. What invalidates the bull case?
  What would technically or fundamentally drive a short?

RESOLUTION: Given the debate, what is the final directional bias and
  conviction level (High / Medium / Low)? High = both sides agree on direction.
  Medium = one side is clearly stronger. Low = too uncertain for directional bet.
```

The existing Scenario A / Scenario B structure in Step 2 becomes the execution plan for whichever side wins the debate.

---

### 4b. Weekly Prompt
**File:** `prompts/trading/weekly_nq_gc.md`  
**Change:** Insert the same adversarial step between Step 1 (Weekly Macro Backdrop) and Step 2 (Weekly Setups). Weekly version should also reference COT positioning as a tiebreaker when bull/bear cases are evenly matched.

---

## Dependency Map

```
Phase 1 (bugs)
  └─ must complete before any testing of Phase 2, 3, 4

Phase 2a (pivot split)
  └─ prerequisite for Phase 2b (needs mode param in place first)

Phase 2b (indicators)
  └─ requires: stockstats in requirements.txt

Phase 3 (regime injection)
  └─ independent of Phase 2, can run in parallel
  └─ requires: macro_analyst.py has run at least once to populate reports/

Phase 4 (prompt redesign)
  └─ independent, can be done any time
  └─ no code changes — prompt files only
```

---

## What This Does NOT Change

- Email delivery system (SMTP, HTML rendering, bilingual structure)
- Macro pipeline (`macro_analyst.py`) — no modifications
- LangGraph or any new orchestration framework — not added
- Broker integration or order execution — not added
- Scheduling setup — pipelines remain manually triggered via shell wrappers
- Overall two-pipeline architecture

---

## Acceptance Criteria

| Change | Test |
|:-------|:-----|
| 1a Remove earnings calendar | `python3 trading_analyst.py --test` exits cleanly, no Section B in output |
| 1b Label fix | Table shows "Prior Settlement" not "Current Price" |
| 1c ETF fallback | Macro report shows prev close values before 9:30 AM ET |
| 2a Pivot split | Intraday PP ≠ weekly PP on same day |
| 2b New indicators | RSI, ATR, MACD, Boll rows appear in technical table |
| 3a Regime injection | Section E present in trading report output |
| 4a/4b Debate prompt | Report contains Bull Case / Bear Case / Resolution blocks |
