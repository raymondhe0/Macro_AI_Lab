"""
Unit tests for FinViz market breadth scraping.

Covers three layers:
  1. _parse_finviz_count  — pure HTML parsing, no network
  2. _fetch_finviz_breadth_pct — HTTP layer mocked with responses library
  3. _breadth_signal      — threshold logic, no network
  4. fetch_market_breadth — integration: mocked network, checks full output
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_fetcher.fetch_macro import (
    _BOTTOM_PCT,
    _BULL_200D_PCT,
    _SP500_COUNT,
    _TOP_RISK_PCT,
    _breadth_signal,
    _fetch_finviz_breadth_pct,
    _parse_finviz_count,
    fetch_market_breadth,
)


# ── Fixtures: sample FinViz HTML ──────────────────────────────────────────────

def _make_finviz_html(count: int) -> str:
    """Minimal FinViz screener HTML containing the count pattern."""
    return f"""
    <html><body>
      <td class="count-text">#1 / {count} Total</td>
    </body></html>
    """


FINVIZ_HTML_223  = _make_finviz_html(223)
FINVIZ_HTML_450  = _make_finviz_html(450)
FINVIZ_HTML_ZERO = _make_finviz_html(0)

FINVIZ_HTML_NO_PATTERN = """
<html><body>
  <td>No results found.</td>
</body></html>
"""

FINVIZ_HTML_WHITESPACE = """
<html><body>
  <td>#1  /   271   Total</td>
</body></html>
"""


# ── 1. _parse_finviz_count ────────────────────────────────────────────────────

class TestParseFinvizCount:

    def test_standard_pattern(self):
        assert _parse_finviz_count(FINVIZ_HTML_223) == 223

    def test_large_count(self):
        assert _parse_finviz_count(FINVIZ_HTML_450) == 450

    def test_zero_count(self):
        assert _parse_finviz_count(FINVIZ_HTML_ZERO) == 0

    def test_no_pattern_returns_none(self):
        assert _parse_finviz_count(FINVIZ_HTML_NO_PATTERN) is None

    def test_extra_whitespace_in_pattern(self):
        # FinViz sometimes renders extra spaces
        assert _parse_finviz_count(FINVIZ_HTML_WHITESPACE) == 271

    def test_empty_string_returns_none(self):
        assert _parse_finviz_count("") is None


# ── 2. _fetch_finviz_breadth_pct ─────────────────────────────────────────────

class TestFetchFinvizBreadthPct:

    def _mock_response(self, html: str, status: int = 200):
        mock = MagicMock()
        mock.status_code = status
        mock.text = html
        mock.raise_for_status = MagicMock()
        if status >= 400:
            import requests
            mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
        return mock

    def test_returns_correct_percentage(self):
        # 223 stocks above SMA20 out of 503 → 44.3%
        expected = round(223 / _SP500_COUNT * 100, 1)
        with patch("data_fetcher.fetch_macro.requests.get",
                   return_value=self._mock_response(FINVIZ_HTML_223)):
            result = _fetch_finviz_breadth_pct(20)
        assert result == expected

    def test_full_market_above_sma_returns_near_100(self):
        # All 503 stocks above SMA → 100%
        html = _make_finviz_html(_SP500_COUNT)
        with patch("data_fetcher.fetch_macro.requests.get",
                   return_value=self._mock_response(html)):
            result = _fetch_finviz_breadth_pct(200)
        assert result == 100.0

    def test_no_stocks_above_sma_returns_zero(self):
        with patch("data_fetcher.fetch_macro.requests.get",
                   return_value=self._mock_response(FINVIZ_HTML_ZERO)):
            result = _fetch_finviz_breadth_pct(200)
        assert result == 0.0

    def test_network_error_returns_none(self):
        import requests as req
        with patch("data_fetcher.fetch_macro.requests.get",
                   side_effect=req.RequestException("timeout")):
            result = _fetch_finviz_breadth_pct(20)
        assert result is None

    def test_http_error_returns_none(self):
        import requests as req
        with patch("data_fetcher.fetch_macro.requests.get",
                   return_value=self._mock_response("", status=403)):
            result = _fetch_finviz_breadth_pct(20)
        assert result is None

    def test_unparseable_html_returns_none(self):
        with patch("data_fetcher.fetch_macro.requests.get",
                   return_value=self._mock_response(FINVIZ_HTML_NO_PATTERN)):
            result = _fetch_finviz_breadth_pct(20)
        assert result is None

    def test_uses_correct_ma_period_in_url(self):
        """Verify the correct SMA filter is included in the request URL."""
        with patch("data_fetcher.fetch_macro.requests.get",
                   return_value=self._mock_response(FINVIZ_HTML_223)) as mock_get:
            _fetch_finviz_breadth_pct(50)
        called_url = mock_get.call_args[0][0]
        assert "ta_sma50_pa" in called_url

    def test_result_is_rounded_to_one_decimal(self):
        # 271 / 503 = 53.876... → should be 53.9
        html = _make_finviz_html(271)
        expected = round(271 / _SP500_COUNT * 100, 1)
        with patch("data_fetcher.fetch_macro.requests.get",
                   return_value=self._mock_response(html)):
            result = _fetch_finviz_breadth_pct(200)
        assert result == expected
        assert isinstance(result, float)


# ── 3. _breadth_signal ───────────────────────────────────────────────────────

class TestBreadthSignal:

    def test_at_top_risk_threshold(self):
        sig = _breadth_signal(_TOP_RISK_PCT, "short-term")
        assert "顶部风险" in sig
        assert "⚠️" in sig

    def test_above_top_risk_threshold(self):
        sig = _breadth_signal(92.0, "short-term")
        assert "顶部风险" in sig

    def test_at_bottom_threshold(self):
        sig = _breadth_signal(_BOTTOM_PCT, "long-term")
        assert "底部" in sig
        assert "⚠️" in sig

    def test_below_bottom_threshold(self):
        sig = _breadth_signal(5.0, "short-term")
        assert "底部" in sig

    def test_neutral_range(self):
        sig = _breadth_signal(50.0, "mid-term")
        assert "中性" in sig

    def test_moderately_bullish(self):
        sig = _breadth_signal(75.0, "mid-term")
        assert "偏多" in sig

    def test_moderately_bearish(self):
        sig = _breadth_signal(25.0, "mid-term")
        assert "偏空" in sig

    def test_signal_includes_percentage_value(self):
        sig = _breadth_signal(44.3, "short-term")
        assert "44.3" in sig


# ── 4. fetch_market_breadth (integration) ────────────────────────────────────

class TestFetchMarketBreadth:

    def _mock_pct(self, side_effects: list):
        """Patch _fetch_finviz_breadth_pct to return values from a list."""
        return patch(
            "data_fetcher.fetch_macro._fetch_finviz_breadth_pct",
            side_effect=side_effects,
        )

    def test_bull_market_footer_when_200d_above_50pct(self):
        with self._mock_pct([44.3, 49.9, 53.9]):
            result = fetch_market_breadth()
        assert "牛市环境" in result
        assert "🟢" in result

    def test_bear_market_footer_when_200d_below_50pct(self):
        with self._mock_pct([20.0, 25.0, 40.0]):
            result = fetch_market_breadth()
        assert "熊市环境" in result
        assert "🔴" in result

    def test_no_footer_when_200d_unavailable(self):
        # None means the 200d fetch failed
        with self._mock_pct([44.3, 49.9, None]):
            result = fetch_market_breadth()
        assert "牛熊分界线" not in result

    def test_all_rows_present_on_success(self):
        with self._mock_pct([44.3, 49.9, 53.9]):
            result = fetch_market_breadth()
        assert "20-day" in result
        assert "50-day" in result
        assert "200-day" in result

    def test_fallback_row_on_partial_failure(self):
        # SMA20 fails, SMA50 and SMA200 succeed
        with self._mock_pct([None, 49.9, 53.9]):
            result = fetch_market_breadth()
        assert "获取失败" in result
        assert "49.9" in result

    def test_output_is_valid_markdown_table(self):
        with self._mock_pct([44.3, 49.9, 53.9]):
            result = fetch_market_breadth()
        lines = result.splitlines()
        table_lines = [l for l in lines if l.startswith("|")]
        assert len(table_lines) >= 5   # header + separator + 3 data rows
        for line in table_lines:
            assert line.startswith("|") and line.endswith("|")

    def test_extreme_overbought_flagged_in_output(self):
        with self._mock_pct([90.0, 88.0, 75.0]):
            result = fetch_market_breadth()
        assert "顶部风险" in result

    def test_extreme_oversold_flagged_in_output(self):
        with self._mock_pct([10.0, 12.0, 20.0]):
            result = fetch_market_breadth()
        assert "底部" in result
