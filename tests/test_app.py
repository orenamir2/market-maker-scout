from app.main import ScanRequest, score_ticker

def test_score_range():
    result = score_ticker("AAPL")
    assert 0 <= result.score <= 100
    assert 0 <= result.confidence <= 100

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
