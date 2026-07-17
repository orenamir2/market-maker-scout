from dataclasses import replace

import numpy as np
import httpx

from app.main import (
    MarketSeries,
    ScanRequest,
    fetch_industry,
    find_score_growth_candidates,
    load_scan_history,
    save_daily_scan,
    score_ticker,
    suggested_signal,
)

def test_score_range():
    result = score_ticker("AAPL")
    assert 0 <= result.score <= 100
    assert 0 <= result.confidence <= 100
    assert result.data_source == "demo"

def test_score_uses_multiple_statistical_components():
    result = score_ticker("AAPL")
    expected_components = {
        "volume_anomaly",
        "volume_acceleration",
        "price_volume_confirmation",
        "accumulation_slope",
        "relative_strength",
        "momentum_improvement",
        "return_consistency",
        "up_down_volume",
        "volatility_compression",
        "drawdown_resilience",
    }
    assert set(result.score_components) == expected_components
    assert all(-1 <= value <= 1 for value in result.score_components.values())
    assert result.volume_acceleration is not None
    assert result.return_t_stat is not None

def test_score_includes_latest_market_metadata():
    series = MarketSeries(
        prices=np.linspace(10, 20, 60),
        volume=np.linspace(1000, 2000, 60),
        latest_at="2026-07-13T20:00:00+00:00",
        source="test_provider",
        industry="Software - Application",
    )
    result = score_ticker("TEST", series)
    assert result.industry == "Software - Application"
    assert result.latest_price == 20
    assert result.latest_volume == 2000
    assert result.latest_at == "2026-07-13T20:00:00+00:00"
    assert result.data_source == "test_provider"
    assert result.suggested_signal
    assert result.suggested_horizon

def test_suggested_signal_bands():
    assert suggested_signal(85)[1] == "1-4 weeks"
    assert suggested_signal(75)[1] == "1-2 weeks"
    assert suggested_signal(65)[1] == "Several trading days"
    assert suggested_signal(50)[1] == "No suggested hold"
    assert suggested_signal(40)[1] == "No suggested hold"

def test_ticker_normalization():
    req = ScanRequest(tickers=[" aapl ", "MSFT", "AAPL"])
    assert req.tickers == ["AAPL", "MSFT"]
    assert req.save is True

def test_scan_history_saves_and_merges_by_day(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN_HISTORY_DIR", str(tmp_path))
    first = replace(score_ticker("AAA"), score=51.2)
    second = replace(score_ticker("BBB"), score=72.4)
    updated_first = replace(score_ticker("AAA"), score=54.8)

    save_daily_scan([first, second], mode="demo", day="2026-07-15")
    save_daily_scan([updated_first], mode="demo", day="2026-07-15")

    history = load_scan_history()
    assert len(history) == 1
    assert history[0]["date"] == "2026-07-15"
    scores = {result["ticker"]: result["score"] for result in history[0]["results"]}
    assert scores == {"AAA": 54.8, "BBB": 72.4}

def test_find_score_growth_candidates_uses_approximate_bands(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN_HISTORY_DIR", str(tmp_path))
    start = replace(score_ticker("AAA"), score=52.0)
    latest = replace(score_ticker("AAA"), score=69.5)
    flat = replace(score_ticker("BBB"), score=50.5)

    save_daily_scan([start, flat], mode="demo", day="2026-07-13")
    save_daily_scan([latest, flat], mode="demo", day="2026-07-16")

    candidates = find_score_growth_candidates(
        start_score=50,
        target_score=70,
        tolerance=8,
        max_days=7,
    )
    assert [candidate["ticker"] for candidate in candidates] == ["AAA"]
    assert candidates[0]["score_change"] == 17.5

def test_fetch_industry_uses_search_exact_symbol_match(monkeypatch):
    fetch_industry.cache_clear()

    def fake_get(url, **kwargs):
        assert kwargs["params"]["q"] == "AAPL"
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "quotes": [
                    {"symbol": "AAPL.MX", "industryDisp": "Wrong Industry"},
                    {"symbol": "AAPL", "industryDisp": "Consumer Electronics"},
                ]
            },
        )

    monkeypatch.setattr("app.main.httpx.get", fake_get)

    assert fetch_industry("AAPL") == "Consumer Electronics"

def test_fetch_industry_falls_back_to_profile(monkeypatch):
    fetch_industry.cache_clear()

    def fake_get(url, **kwargs):
        if "finance/search" in url:
            return httpx.Response(200, request=httpx.Request("GET", url), json={"quotes": []})
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "quoteSummary": {
                    "result": [{"assetProfile": {"industry": "Software - Application"}}]
                }
            },
        )

    monkeypatch.setattr("app.main.httpx.get", fake_get)

    assert fetch_industry("APP") == "Software - Application"

def test_starting_universe_has_250_smaller_companies():
    html = open("app/static/index.html", encoding="utf-8").read()
    default_tickers = html.split('<textarea id="tickers">', 1)[1].split("</textarea>", 1)[0].split(",")
    assert len(default_tickers) == 250
    assert len(default_tickers) == len(set(default_tickers))
    assert "AAPL" not in default_tickers
    assert "MSFT" not in default_tickers
