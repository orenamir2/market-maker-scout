from app.main import ScanRequest, score_ticker

def test_score_range():
    result = score_ticker("AAPL")
    assert 0 <= result.score <= 100
    assert 0 <= result.confidence <= 100

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

def test_ticker_normalization():
    req = ScanRequest(tickers=[" aapl ", "MSFT", "AAPL"])
    assert req.tickers == ["AAPL", "MSFT"]

def test_starting_universe_has_250_smaller_companies():
    html = open("app/static/index.html", encoding="utf-8").read()
    default_tickers = html.split('<textarea id="tickers">', 1)[1].split("</textarea>", 1)[0].split(",")
    assert len(default_tickers) == 250
    assert len(default_tickers) == len(set(default_tickers))
    assert "AAPL" not in default_tickers
    assert "MSFT" not in default_tickers
