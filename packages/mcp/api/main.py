"""Insider Signal MCP — FastAPI server."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from api.bridge import (
    get_signals, get_cluster, get_insider_activity,
    run_valuation, get_combos, get_health, get_buyback_status,
)

app = FastAPI(title="Insider Signal MCP", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    try:
        return get_health()
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/signals")
def signals(
    limit: int = Query(50, ge=1, le=500),
    min_score: float = Query(0),
    sector: Optional[str] = None,
    cluster_only: bool = False,
):
    try:
        return get_signals(limit, min_score, sector, cluster_only)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/cluster/{ticker}")
def cluster(ticker: str):
    try:
        return get_cluster(ticker.upper())
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/buyback/{ticker}")
def buyback(ticker: str):
    try:
        return get_buyback_status(ticker.upper())
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/insider_activity/{ticker}")
def insider_activity(ticker: str):
    try:
        result = get_insider_activity(ticker.upper())
        if not result["purchases"]:
            raise HTTPException(404, f"No insider purchases found for {ticker.upper()}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/valuation/{ticker}")
def valuation(ticker: str):
    try:
        return run_valuation(ticker.upper())
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/combos")
def combos():
    try:
        return get_combos()
    except Exception as e:
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8502, reload=True)
