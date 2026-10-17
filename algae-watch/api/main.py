"""AlgaeWatch Iceland API. Run: .venv/bin/uvicorn api.main:app --reload"""
import json
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
import config  # noqa: E402
import common  # noqa: E402

app = FastAPI(title="AlgaeWatch Iceland")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

TILES = config.PROCESSED / "tiles"


TS_COLS = ["date", "chl", "chl_obs", "chl_300m", "diatoms", "haptophytes", "dinoflagellates", "rrs555",
           "secchi", "sst", "salinity", "mixed_layer", "nitrate", "silicate", "wave_height",
           "shortwave_radiation_sum", "wind_speed_10m_mean", "current_speed"]


@lru_cache
def series():
    f = config.PROCESSED / "site_series.parquet"
    if not f.exists():
        raise HTTPException(503, "No processed data yet: run the pipeline (see README)")
    s = pd.read_parquet(f)
    b = config.PROCESSED / "blooms.parquet"
    if b.exists():
        bl = pd.read_parquet(b)[["date", "site", "chl_clim", "chl_anomaly_ratio", "bloom_fraction",
                                 "is_bloom", "observed_bloom"]]
        s = s.merge(bl, on=["date", "site"], how="left")
    return s


@lru_cache
def events():
    f = config.PROCESSED / "bloom_events.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()


@lru_cache
def phenology():
    f = config.PROCESSED / "phenology.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()


def clean(df):
    """DataFrame -> JSON-safe dict of columns (NaN -> null)."""
    out = {}
    for c in df.columns:
        if c == "date":
            out[c] = df[c].dt.strftime("%Y-%m-%d").tolist()
        else:
            v = df[c].astype(float).round(4).to_numpy()
            out[c] = [None if not np.isfinite(x) else float(x) for x in v]
    return out


@app.get("/api/sites")
def sites():
    return [{**s, "slug": common.slug(s["name"]), "placeholder": config.SITES_ARE_PLACEHOLDERS}
            for s in config.SITES]


@app.get("/api/manifest")
def manifest():
    f = TILES / "manifest.json"
    if not f.exists():
        raise HTTPException(404, "Run src/make_tiles.py first")
    return json.loads(f.read_text())


@app.get("/api/timeseries")
def timeseries(site: str, start: str | None = None, end: str | None = None):
    start = start or f"{config.APP_YEARS[0]}-01-01"
    end = end or f"{config.APP_YEARS[1]}-12-31"
    df = series()
    df = df[(df.site == site) & (df.date >= start) & (df.date <= end)]
    if df.empty:
        raise HTTPException(404, f"unknown site {site}")
    cols = [c for c in TS_COLS + ["chl_clim", "chl_anomaly_ratio", "bloom_fraction", "is_bloom", "observed_bloom"]
            if c in df.columns]
    return clean(df[cols].sort_values("date"))


def records(df):
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    out = out.astype(object).where(out.notna(), None)
    return out.to_dict("records")


@app.get("/api/events")
def bloom_events(site: str | None = None):
    ev = events()
    if site:
        ev = ev[ev.site == site]
    return records(ev.sort_values("start", ascending=False).drop(columns=["sst_anomaly_hint"], errors="ignore"))


@app.get("/api/phenology")
def bloom_phenology(site: str):
    ph = phenology()
    return records(ph[ph.site == site].sort_values("year"))


@app.get("/api/calendar")
def calendar():
    """Bloom days per site and year (all years), plus per site and month."""
    df = series()
    df = df[df.is_bloom == True]  # noqa: E712
    by_year = df.groupby(["site", df.date.dt.year]).size().rename("days").reset_index()
    by_month = df.groupby(["site", df.date.dt.month]).size().rename("days").reset_index()
    return {"years": [config.FIRST_YEAR, config.LAST_YEAR], "baseline": list(config.BASELINE_YEARS),
            "by_year": records(by_year.rename(columns={"date": "year"})),
            "by_month": records(by_month.rename(columns={"date": "month"}))}


@app.get("/api/snapshot")
def snapshot(date: str):
    """All sites' values on one date (for marker tooltips)."""
    df = series()
    df = df[df.date == pd.Timestamp(date)]
    cols = [c for c in ["site", "chl", "chl_300m", "sst", "rrs555", "haptophytes", "diatoms",
                        "chl_anomaly_ratio", "is_bloom"] if c in df.columns]
    return {r["site"]: {k: (None if pd.isna(r[k]) else round(float(r[k]), 3)) for k in cols[1:]}
            for r in df[cols].to_dict("records")}


TILES.mkdir(parents=True, exist_ok=True)  # lets the app start before any data is processed
app.mount("/tiles", StaticFiles(directory=TILES), name="tiles")
app.mount("/", StaticFiles(directory=config.WEB, html=True), name="web")
