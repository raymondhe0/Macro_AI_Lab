# 📊 Analysis: yfinance Economic Calendar vs. Paid Finnhub

The following report summarizes the exploration of **yfinance's** `Calendars` feature as a potential free replacement for macroeconomic data (CPI, NFP, FOMC) currently provided by paid API tiers like Finnhub.

---

## 1. Feature Availability in `yfinance`

Recent versions of the `yfinance` library include a dedicated `Calendars` class that scrapes economic event data from Yahoo Finance.

### 🛠️ Key Implementation
```python
import yfinance as yf
from datetime import datetime, timedelta

# Default initialization (today + 7 days)
cal = yf.Calendars()

# Accessing the economic calendar
econ_df = cal.get_economic_events_calendar()
```

### 📋 Data Architecture
The returned DataFrame contains the following columns:
- **Region**: Country/Area code (e.g., US, BR, OM).
- **Event Time**: ISO timestamp of the occurrence.
- **For**: Period the data covers (e.g., "Mar 2026").
- **Actual**: The released value (Free/Public).
- **Expected**: Market consensus (This is the "Consensus" value).
- **Last**: Previous value without revisions.
- **Revised**: Previous value after adjustments.

---

## 2. Critical Findings & Data Gaps

> [!WARNING]
> **Source data on Yahoo Finance (free version) is highly inconsistent for consensus/market expectation values.**

### 🛑 The "Consensus" Gap
While the API provides an `Expected` column, it is **consistently empty (`-`)** for the most critical US macroeconomic indicators, including:
- **NFP (Non-Farm Payrolls)**
- **CPI (Consumer Price Index)**
- **Jobless Claims**
- **PCE Price Index**

### 📉 Coverage Limitations
- **Selective Data**: Yahoo Finance's free calendar primarily lists dates and actual releases. It rarely populates the "Expectation" field for high-impact US events unless a premium subscription is active on their web interface.
- **Filtering**: There is no native library method to filter by event impact (e.g., "High Impact" or "3-star" events).
- **Region Logic**: Requires manual filtering of the `Region` column (e.g., `df[df['Region'] == 'US']`).

---

## 3. Comparison Table

| Feature | yfinance (Free Scraper) | Finnhub (Paid) |
| :--- | :--- | :--- |
| **Release Dates** | ✅ Yes | ✅ Yes |
| **Actual Values** | ✅ Yes | ✅ Yes |
| **Consensus (Expected)** | ❌ **No (Mostly empty for US)** | ✅ Yes |
| **Historical Data** | ⚠️ Sparse (Last 30-90 days) | ✅ Full Database |
| **Reliability** | ⚠️ High (Depends on web scraping) | ✅ High (Dedicated API) |

---

## 4. Strategic Recommendations

If your automation pipeline requires comparing **Actual vs. Consensus** for market reasoning (as the "Macro Playbook" logic usually does), `yfinance` is **not** currently a viable total replacement for a paid Finnhub subscription.

### ✅ Alternative Workflows
1. **FRED Integration**: Use the `fredapi` library to get official **Actual** US economic series for free. It is more robust than scraping Yahoo Finance.
2. **Hybrid Logic**: Use `yfinance` to fetch the **dates** and **actual values** to save on API usage, but fall back to a low-cost or limited provider only for the **Expected (Consensus)** numbers.
3. **News Extraction**: Since you already have a news-fetching pipeline, the LLM can often extract consensus/expectations from the *snippets* of news articles (e.g., "Economists expect CPI to rise 0.2%...") instead of relying on a structured API field.

---

**Macro AI Lab Research — 2026**
