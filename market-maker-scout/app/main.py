from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from typing import List

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from scipy.stats import norm

app = FastAPI(title="Market Maker Scout", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


class ScanRequest(BaseModel):
    tickers: List[str] = Field(min_length=1, max_length=250)

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: List[str]) -> List[str]:
        cleaned = [v.strip().upper() for v in values if v.strip()]
        if len(cleaned) != len(set(cleaned)):
            cleaned = list(dict.fromkeys(cleaned))
        if not cleaned:
            raise ValueError("At least one ticker is required")
        return cleaned


@dataclass
class ScoreResult:
    ticker: str
    score: float
    confidence: float
    volume_z: float
    price_volume_corr: float
    accumulation_slope: float
    relative_strength: float
    explanation: str


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


def score_ticker(ticker: str) -> ScoreResult:
    prices, volume = deterministic_series(ticker)
    returns = np.diff(np.log(prices), prepend=np.log(prices[0]))

    vol_mean = volume[:-10].mean()
    vol_std = volume[:-10].std() or 1.0
    volume_z = float((volume[-5:].mean() - vol_mean) / vol_std)
    price_volume_corr = float(np.corrcoef(returns[-20:], volume[-20:])[0, 1])

    x = np.arange(20)
    obv = np.cumsum(np.sign(returns[-20:]) * volume[-20:])
    accumulation_slope = float(np.polyfit(x, obv / max(abs(obv).max(), 1), 1)[0])
    relative_strength = float((prices[-1] / prices[-21] - 1) * 100)

    # Blend several bounded signals. This is a ranking heuristic, not proof of market-maker activity.
    components = np.array([
        np.tanh(volume_z / 3),
        np.nan_to_num(price_volume_corr),
        np.tanh(accumulation_slope * 12),
        np.tanh(relative_strength / 12),
    ])
    weights = np.array([0.35, 0.20, 0.25, 0.20])
    raw = float(np.dot(components, weights))
    score = float(np.clip(50 + 50 * raw, 0, 100))

    # Convert distance from neutral to a model confidence proxy.
    confidence = float(np.clip(2 * abs(norm.cdf(raw * 2.2) - 0.5) * 100, 0, 99))
    explanation = (
        f"Volume anomaly {volume_z:.2f}σ; price/volume correlation "
        f"{price_volume_corr:.2f}; accumulation slope {accumulation_slope:.3f}; "
        f"20-day move {relative_strength:.1f}%."
    )
    return ScoreResult(ticker, round(score, 1), round(confidence, 1), round(volume_z, 2),
                       round(price_volume_corr, 2), round(accumulation_slope, 3),
                       round(relative_strength, 1), explanation)


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/scan")
def scan(request: ScanRequest) -> dict:
    if len(request.tickers) > 250:
        raise HTTPException(status_code=400, detail="Maximum 250 tickers")
    results = sorted((score_ticker(t) for t in request.tickers), key=lambda x: x.score, reverse=True)
    return {
        "mode": os.getenv("DATA_MODE", "demo"),
        "warning": "Research ranking only. It does not identify market makers directly and is not financial advice.",
        "results": [asdict(r) for r in results],
    }
