from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from scipy.stats import norm

app = FastAPI(title="Market Maker Scout", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_PROFILE_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
DEFAULT_HISTORY_DIR = "data/scan-history"
DEFAULT_SCAN_WORKERS = 24
MAX_SCAN_WORKERS = 64
DEFAULT_MARKET_DATA_TIMEOUT_SECONDS = 6.0
DEFAULT_INDUSTRY_TIMEOUT_SECONDS = 3.0
SCAN_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ScanRequest(BaseModel):
    tickers: List[str] = Field(min_length=1, max_length=250)
    save: bool = True

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: List[str]) -> List[str]:
        cleaned = [v.strip().upper() for v in values if v.strip()]
        if len(cleaned) != len(set(cleaned)):
            cleaned = list(dict.fromkeys(cleaned))
        if not cleaned:
            raise ValueError("At least one ticker is required")
        return cleaned


class TrendRequest(BaseModel):
    min_score_change: float = Field(default=0, ge=0, le=100)
    min_confidence_change: float = Field(default=0, ge=0, le=100)
    max_days: int = Field(default=30, ge=2, le=365)


@dataclass
class ScoreResult:
    ticker: str
    industry: str
    latest_price: float
    latest_volume: int
    latest_at: str
    data_source: str
    score: float
    confidence: float
    suggested_signal: str
    suggested_horizon: str
    volume_z: float
    volume_acceleration: float
    price_volume_corr: float
    accumulation_slope: float
    relative_strength: float
    momentum_z: float
    return_t_stat: float
    up_down_volume_ratio: float
    volatility_compression: float
    drawdown_resilience: float
    score_components: dict[str, float]
    explanation: str


@dataclass
class MarketSeries:
    prices: np.ndarray
    volume: np.ndarray
    latest_at: str
    source: str
    industry: str = "Unknown"


def deterministic_series(ticker: str, days: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """Demo-only deterministic market data. Replace with a real provider in production."""
    seed = int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.018, days)
    prices = 20 * np.exp(np.cumsum(returns))
    base_volume = rng.lognormal(13.2, 0.35, days)
    # Inject a mild, deterministic accumulation pattern into some symbols.
    if seed % 5 == 0:
        base_volume[-10:] *= np.linspace(1.2, 2.4, 10)
        prices[-10:] *= np.linspace(1.0, 1.08, 10)
    return prices, base_volume


def demo_market_series(ticker: str) -> MarketSeries:
    prices, volume = deterministic_series(ticker)
    return MarketSeries(
        prices=prices,
        volume=volume,
        latest_at="demo",
        source="demo",
        industry="Unknown",
    )


def scan_history_dir() -> Path:
    return Path(os.getenv("SCAN_HISTORY_DIR", DEFAULT_HISTORY_DIR))


def scan_timezone() -> timezone | ZoneInfo:
    configured = os.getenv("SCAN_TIMEZONE", "UTC")
    try:
        return ZoneInfo(configured)
    except ZoneInfoNotFoundError:
        return timezone.utc


def scan_date() -> str:
    return datetime.now(scan_timezone()).date().isoformat()


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def scan_workers(ticker_count: int) -> int:
    configured = env_int("SCAN_WORKERS", DEFAULT_SCAN_WORKERS, 1, MAX_SCAN_WORKERS)
    return max(1, min(configured, ticker_count))


def history_file(day: str) -> Path:
    return scan_history_dir() / f"{day}.json"


def load_daily_scan(day: str) -> dict:
    path = history_file(day)
    if not path.exists():
        return {"date": day, "results": []}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def has_daily_scan(day: str) -> bool:
    return bool(load_daily_scan(day).get("results"))


def save_daily_scan(results: list[ScoreResult], mode: str, day: str | None = None) -> None:
    day = day or scan_date()
    path = history_file(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_daily_scan(day)
    by_ticker = {
        str(result.get("ticker", "")).upper(): result
        for result in existing.get("results", [])
        if result.get("ticker")
    }
    for result in results:
        row = asdict(result)
        row["scanned_at"] = datetime.now(timezone.utc).isoformat()
        by_ticker[result.ticker] = row

    payload = {
        "date": day,
        "mode": mode,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "results": sorted(by_ticker.values(), key=lambda x: x["score"], reverse=True),
    }
    temp_path = path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    temp_path.replace(path)


def load_scan_history(max_days: int = 60) -> list[dict]:
    root = scan_history_dir()
    if not root.exists():
        return []
    files = sorted(root.glob("*.json"), reverse=True)[:max_days]
    history = []
    for path in sorted(files):
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        history.append(payload)
    return history


def find_score_growth_candidates(
    min_score_change: float = 0,
    min_confidence_change: float = 0,
    max_days: int = 30,
) -> list[dict]:
    histories: dict[str, list[dict]] = {}
    for daily_scan in load_scan_history(max_days=max_days):
        day = daily_scan.get("date")
        for result in daily_scan.get("results", []):
            ticker = result.get("ticker")
            score = result.get("score")
            confidence = result.get("confidence")
            if not ticker or score is None or confidence is None:
                continue
            histories.setdefault(str(ticker), []).append(
                {
                    "date": day,
                    "score": float(score),
                    "confidence": float(confidence),
                    "industry": result.get("industry", "Unknown"),
                    "latest_price": result.get("latest_price"),
                    "latest_at": result.get("latest_at"),
                    "suggested_signal": result.get("suggested_signal", ""),
                }
            )

    candidates = []
    for ticker, points in histories.items():
        points = sorted(points, key=lambda point: point["date"])
        if len(points) < 2:
            continue
        first = points[0]
        last = points[-1]
        score_change = last["score"] - first["score"]
        confidence_change = last["confidence"] - first["confidence"]
        if score_change > 0 and confidence_change > 0 and score_change >= min_score_change and confidence_change >= min_confidence_change:
            candidates.append(
                {
                    "ticker": ticker,
                    "industry": last["industry"],
                    "start_date": first["date"],
                    "start_score": round(first["score"], 1),
                    "start_confidence": round(first["confidence"], 1),
                    "latest_date": last["date"],
                    "latest_score": round(last["score"], 1),
                    "latest_confidence": round(last["confidence"], 1),
                    "score_change": round(score_change, 1),
                    "confidence_change": round(confidence_change, 1),
                    "latest_price": last["latest_price"],
                    "latest_at": last["latest_at"],
                    "suggested_signal": last["suggested_signal"],
                    "history": points,
                }
            )

    return sorted(
        candidates,
        key=lambda item: (item["score_change"], item["confidence_change"]),
        reverse=True,
    )


@lru_cache(maxsize=512)
def fetch_industry(ticker: str) -> str:
    """Best-effort company industry lookup for scan context."""
    headers = {"User-Agent": "market-maker-scout/0.1"}
    timeout = env_float(
        "INDUSTRY_TIMEOUT_SECONDS",
        DEFAULT_INDUSTRY_TIMEOUT_SECONDS,
        1.0,
        10.0,
    )
    try:
        response = httpx.get(
            YAHOO_SEARCH_URL,
            params={"q": ticker, "quotesCount": 6, "newsCount": 0},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        quotes = payload.get("quotes") or []
        exact_quote = next(
            (
                quote
                for quote in quotes
                if str(quote.get("symbol") or "").upper() == ticker.upper()
            ),
            {},
        )
        industry = str(
            exact_quote.get("industryDisp") or exact_quote.get("industry") or ""
        ).strip()
        if industry:
            return industry
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass

    try:
        response = httpx.get(
            YAHOO_PROFILE_URL.format(ticker=ticker),
            params={"modules": "assetProfile"},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("quoteSummary", {}).get("result") or []
        profile = (results[0] if results else {}).get("assetProfile") or {}
        industry = str(profile.get("industry") or "").strip()
        return industry or "Unknown"
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return "Unknown"


def fetch_live_market_series(ticker: str) -> MarketSeries:
    """Fetch the latest available daily bars for a ticker.

    Yahoo's public chart endpoint is suitable for this prototype, but production use
    should switch to a licensed market-data provider with clear latency guarantees.
    """
    response = httpx.get(
        YAHOO_CHART_URL.format(ticker=ticker),
        params={"range": "3mo", "interval": "1d"},
        headers={"User-Agent": "market-maker-scout/0.1"},
        timeout=env_float(
            "MARKET_DATA_TIMEOUT_SECONDS",
            DEFAULT_MARKET_DATA_TIMEOUT_SECONDS,
            1.0,
            20.0,
        ),
    )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart", {})
    error = chart.get("error")
    if error:
        raise ValueError(error.get("description") or f"No chart data for {ticker}")

    results = chart.get("result") or []
    if not results:
        raise ValueError(f"No chart data for {ticker}")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quotes = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    rows = [
        (ts, close, vol)
        for ts, close, vol in zip(timestamps, closes, volumes)
        if close is not None and vol is not None
    ]
    if len(rows) < 41:
        raise ValueError(f"Not enough recent data for {ticker}")

    recent_rows = rows[-60:]
    latest_timestamp = recent_rows[-1][0]
    latest_at = datetime.fromtimestamp(latest_timestamp, tz=timezone.utc).isoformat()
    return MarketSeries(
        prices=np.array([row[1] for row in recent_rows], dtype=float),
        volume=np.array([row[2] for row in recent_rows], dtype=float),
        latest_at=latest_at,
        source="yahoo_finance_chart",
        industry=fetch_industry(ticker),
    )


def safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.nan_to_num(np.corrcoef(a, b)[0, 1]))


def normalized_slope(values: np.ndarray) -> float:
    scale = max(float(np.max(np.abs(values))), 1.0)
    return float(np.polyfit(np.arange(len(values)), values / scale, 1)[0])


def t_stat(values: np.ndarray) -> float:
    std = float(np.std(values, ddof=1))
    if std == 0:
        return 0.0
    return float(np.mean(values) / (std / np.sqrt(len(values))))


def suggested_signal(score: float) -> tuple[str, str]:
    if score >= 80:
        return ("Strong research watchlist signal; require price/volume confirmation before entry.", "1-4 weeks")
    if score >= 70:
        return ("Positive research watchlist signal; monitor for confirmation.", "1-2 weeks")
    if score >= 60:
        return ("Mild signal; watch only unless follow-through improves.", "Several trading days")
    if score >= 45:
        return ("Neutral signal; no standalone bullish edge from this scan.", "No suggested hold")
    return ("Weak signal; avoid as a bullish setup from this scan.", "No suggested hold")


def score_ticker(ticker: str, market_series: MarketSeries | None = None) -> ScoreResult:
    market_series = market_series or demo_market_series(ticker)
    prices = market_series.prices
    volume = market_series.volume
    returns = np.diff(np.log(prices), prepend=np.log(prices[0]))

    vol_mean = volume[:-10].mean()
    vol_std = volume[:-10].std() or 1.0
    volume_z = float((volume[-5:].mean() - vol_mean) / vol_std)
    volume_acceleration = float((volume[-5:].mean() - volume[-20:-5].mean()) / vol_std)
    price_volume_corr = safe_corr(returns[-20:], volume[-20:])

    obv = np.cumsum(np.sign(returns[-20:]) * volume[-20:])
    accumulation_slope = normalized_slope(obv)
    relative_strength = float((prices[-1] / prices[-21] - 1) * 100)
    earlier_strength = float((prices[-21] / prices[-41] - 1) * 100)
    momentum_z = float((relative_strength - earlier_strength) / (np.std(returns[-40:-20]) * 100 or 1.0))

    return_t_stat = t_stat(returns[-20:])
    up_volume = float(volume[-20:][returns[-20:] > 0].sum())
    down_volume = float(volume[-20:][returns[-20:] < 0].sum())
    up_down_volume_ratio = up_volume / max(down_volume, 1.0)

    recent_volatility = float(np.std(returns[-10:]))
    prior_volatility = float(np.std(returns[-40:-10]) or recent_volatility or 1.0)
    volatility_compression = float(1 - recent_volatility / prior_volatility)

    running_high = np.maximum.accumulate(prices[-20:])
    max_drawdown = float(np.min(prices[-20:] / running_high - 1))
    drawdown_resilience = float(max_drawdown * 100)

    # Blend several bounded signals. This is a ranking heuristic, not proof of market-maker activity.
    score_components = {
        "volume_anomaly": float(np.tanh(volume_z / 3)),
        "volume_acceleration": float(np.tanh(volume_acceleration / 2.5)),
        "price_volume_confirmation": price_volume_corr,
        "accumulation_slope": float(np.tanh(accumulation_slope * 12)),
        "relative_strength": float(np.tanh(relative_strength / 12)),
        "momentum_improvement": float(np.tanh(momentum_z / 2.5)),
        "return_consistency": float(np.tanh(return_t_stat / 3)),
        "up_down_volume": float(np.tanh(np.log(max(up_down_volume_ratio, 0.01)) / 1.5)),
        "volatility_compression": float(np.tanh(volatility_compression * 2)),
        "drawdown_resilience": float(np.tanh((drawdown_resilience + 12) / 8)),
    }
    weights = {
        "volume_anomaly": 0.16,
        "volume_acceleration": 0.10,
        "price_volume_confirmation": 0.11,
        "accumulation_slope": 0.14,
        "relative_strength": 0.12,
        "momentum_improvement": 0.10,
        "return_consistency": 0.09,
        "up_down_volume": 0.08,
        "volatility_compression": 0.04,
        "drawdown_resilience": 0.06,
    }
    raw = float(sum(score_components[name] * weight for name, weight in weights.items()))
    score = float(np.clip(50 + 50 * raw, 0, 100))

    # Convert distance from neutral to a model confidence proxy.
    confidence = float(np.clip(2 * abs(norm.cdf(raw * 2.2) - 0.5) * 100, 0, 99))
    signal, horizon = suggested_signal(score)
    explanation = (
        f"Vol z {volume_z:.2f}σ, accel {volume_acceleration:.2f}σ; "
        f"price/volume corr {price_volume_corr:.2f}; OBV slope {accumulation_slope:.3f}; "
        f"20d move {relative_strength:.1f}%, momentum z {momentum_z:.2f}; "
        f"return t {return_t_stat:.2f}; up/down vol {up_down_volume_ratio:.2f}; "
        f"vol compression {volatility_compression:.2f}; max drawdown {drawdown_resilience:.1f}%."
    )
    return ScoreResult(
        ticker=ticker,
        industry=market_series.industry,
        latest_price=round(float(prices[-1]), 2),
        latest_volume=int(volume[-1]),
        latest_at=market_series.latest_at,
        data_source=market_series.source,
        score=round(score, 1),
        confidence=round(confidence, 1),
        suggested_signal=signal,
        suggested_horizon=horizon,
        volume_z=round(volume_z, 2),
        volume_acceleration=round(volume_acceleration, 2),
        price_volume_corr=round(price_volume_corr, 2),
        accumulation_slope=round(accumulation_slope, 3),
        relative_strength=round(relative_strength, 1),
        momentum_z=round(momentum_z, 2),
        return_t_stat=round(return_t_stat, 2),
        up_down_volume_ratio=round(up_down_volume_ratio, 2),
        volatility_compression=round(volatility_compression, 2),
        drawdown_resilience=round(drawdown_resilience, 1),
        score_components={name: round(value, 3) for name, value in score_components.items()},
        explanation=explanation,
    )


def scan_one_ticker(ticker: str, mode: str) -> ScoreResult:
    market_series = (
        demo_market_series(ticker)
        if mode == "demo"
        else fetch_live_market_series(ticker)
    )
    return score_ticker(ticker, market_series)


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/scan")
def scan(request: ScanRequest) -> dict:
    return run_scan(request)


def run_scan(request: ScanRequest, day: str | None = None) -> dict:
    if len(request.tickers) > 250:
        raise HTTPException(status_code=400, detail="Maximum 250 tickers")
    day = day or scan_date()
    mode = os.getenv("DATA_MODE", "live").lower()
    results = []
    errors = []
    worker_count = scan_workers(len(request.tickers))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(scan_one_ticker, ticker, mode): ticker
            for ticker in request.tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results.append(future.result())
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                errors.append({"ticker": ticker, "error": str(exc)})

    if not results and errors:
        raise HTTPException(status_code=502, detail={"message": "No market data could be fetched", "errors": errors})

    results = sorted(results, key=lambda x: x.score, reverse=True)
    if request.save:
        save_daily_scan(results, mode, day=day)
    warning = (
        "Research ranking only. Live values use the latest available provider bars and may be delayed; "
        "not proof of market-maker buying and not financial advice."
        if mode != "demo"
        else "Research ranking only. Demo mode uses synthetic data and is not financial advice."
    )
    return {
        "mode": mode,
        "warning": warning,
        "errors": errors,
        "scanned": len(results),
        "requested": len(request.tickers),
        "workers": worker_count,
        "saved": request.save,
        "scan_date": day,
        "results": [asdict(r) for r in results],
    }


@app.post("/api/daily-scan")
def daily_scan(request: ScanRequest) -> dict:
    day = scan_date()
    if has_daily_scan(day):
        return {
            "scan_date": day,
            "saved": True,
            "skipped": True,
            "message": f"Saved scan already exists for {day}.",
            "results": load_daily_scan(day).get("results", []),
        }

    request.save = True
    payload = run_scan(request, day=day)
    payload["skipped"] = False
    return payload


@app.get("/api/history")
def history(max_days: int = 14) -> dict:
    return {"history": load_scan_history(max_days=max(1, min(max_days, 365)))}


@app.get("/api/history/{day}")
def history_day(day: str) -> dict:
    if not SCAN_DAY_RE.match(day):
        raise HTTPException(status_code=400, detail="Scan date must use YYYY-MM-DD")
    payload = load_daily_scan(day)
    if not payload.get("results"):
        raise HTTPException(status_code=404, detail=f"No saved scan found for {day}")
    return payload


@app.get("/api/trends")
def trends(
    min_score_change: float = 0,
    min_confidence_change: float = 0,
    max_days: int = 30,
) -> dict:
    trend_request = TrendRequest(
        min_score_change=min_score_change,
        min_confidence_change=min_confidence_change,
        max_days=max_days,
    )
    return {
        "criteria": trend_request.model_dump(),
        "candidates": find_score_growth_candidates(**trend_request.model_dump()),
    }
