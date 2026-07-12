from app.main import ScanRequest, score_ticker

def test_score_range():
    result = score_ticker("AAPL")
    assert 0 <= result.score <= 100
    assert 0 <= result.confidence <= 100

def test_ticker_normalization():
    req = ScanRequest(tickers=[" aapl ", "MSFT", "AAPL"])
    assert req.tickers == ["AAPL", "MSFT"]
