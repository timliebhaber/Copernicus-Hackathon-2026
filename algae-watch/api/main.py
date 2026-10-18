"""AlgaeWatch Iceland API. Run: .venv/bin/uvicorn api.main:app --reload

Everything is organised around the licensed marine farms (config.FARMS, MAST register).
"""
import json
import math
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
import config  # noqa: E402

app = FastAPI(title="AlgaeWatch Iceland")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

TILES = config.PROCESSED / "tiles"
FARM_NAMES = {f["name"] for f in config.FARMS}

# Map layers: what they are and how the forecast uses them (shown in the layer picker).
LAYER_INFO = {
    "chl": ("Chlorophyll", "Green pigment of algae, seen from space (gap-filled). The main bloom signal.",
            "Bloom signal (used with a 5-day delay)"),
    "chl_obs": ("Clear-sky chlorophyll", "Only real satellite views; gaps are clouds.",
                "Latest clear-sky view (strongest input)"),
    "anomaly": ("Vs normal", "Chlorophyll compared with the 1998–2022 median for this place and date.",
                "How unusual the algae level is"),
    "sst": ("Sea temperature", "Satellite sea-surface temperature (OSTIA).", "Warm water speeds up growth"),
    "salinity": ("Salinity", "Surface salinity (GLORYS ocean model). Fresher water floats on top.",
                 "Freshwater layering"),
    "mixed_layer": ("Surface layer", "Depth of the well-mixed surface layer. Shallow = calm, layered water.",
                    "A shallow layer keeps algae in the light"),
    "currents": ("Currents", "Surface current speed (GLORYS ocean model).", "Moves water between fjords"),
    "waves": ("Waves", "Significant wave height (wave model). Storms mix the water.", "Storms break up blooms"),
    "rrs555": ("Milky water", "Green-light reflectance. Very bright = coccolithophore bloom.",
               "Detects coccolithophore blooms"),
}
# hover values: layer -> (stream, variable, transform)
VALUE_SRC = {
    "chl": ("chl_l4", "CHL", None), "chl_obs": ("pft_l3", "CHL", None), "anomaly": ("chl_l4", "CHL", "anomaly"),
    "bloom": ("chl_l4", "CHL", "anomaly"), "sst": ("sst_ostia", "analysed_sst", "kelvin"),
    "salinity": ("phys", "so", None), "mixed_layer": ("phys", "mlotst", None), "currents": ("phys", "uo", "speed"),
    "waves": ("waves", "VHM0", None), "rrs555": ("rrs_l3", "RRS555", None),
}


def clean_value(v, nd=4):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return v
    return None if not math.isfinite(v) else round(v, nd)


def need(f):
    if not f.exists():
        raise HTTPException(503, f"No processed data yet ({f.name}): run the pipeline (see README)")
    return f


@lru_cache
def series():
    s = pd.read_parquet(need(config.PROCESSED / "site_series.parquet"))
    b = pd.read_parquet(need(config.PROCESSED / "blooms.parquet")).drop(columns=["lat", "lon", "kind", "chl"])
    return s.merge(b, on=["site", "date"], how="left").set_index(["site", "date"]).sort_index()


@lru_cache
def events():
    return pd.read_parquet(need(config.PROCESSED / "bloom_events.parquet"))


@lru_cache
def forecast():
    f = config.PROCESSED / "forecast.parquet"
    return pd.read_parquet(f).set_index(["site", "date"]).sort_index() if f.exists() else pd.DataFrame()


@lru_cache
def manifest():
    return json.loads(need(TILES / "manifest.json").read_text())


@lru_cache
def toxic_table():
    f = config.PROCESSED / "hafro_toxic.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()


GENUS = {"dsp": "dinophysis_above", "psp": "alexandrium_above", "asp": "pseudo_nitzschia_above"}


def month_shares(t):
    """Share of samples above the safe limit, per calendar month: any toxin and per toxin type."""
    out = {}
    for m, g in t.groupby(t.date.dt.month):
        out[int(m)] = {"n": int(len(g)), "any": float(g.any_above.mean()),
                       **{k: float(g[c].mean()) for k, c in GENUS.items()}}
    return out


@lru_cache
def toxic_by_month():
    t = toxic_table()
    return month_shares(t) if len(t) else {}


def nearest_station(lat, lon):
    t = toxic_table()
    if t.empty:
        return None
    st = min(config.HAFRO_STATIONS, key=lambda s: config._km(s, {"lat": lat, "lon": lon}))
    g = t[t.station == st["name"]]
    if g.empty:
        return None
    last = g.sort_values("date").iloc[-1]
    counts = {k: int(g[c].sum()) for k, c in GENUS.items()}
    ms = month_shares(g)
    return {"name": st["name"], "km": round(config._km(st, {"lat": lat, "lon": lon}), 1), "samples": int(len(g)),
            "years": f"{g.date.dt.year.min()}–{g.date.dt.year.max()}", "share_above": float(g.any_above.mean()),
            "counts": counts, "last_date": last.date.strftime("%Y-%m-%d"), "last_above": bool(last.any_above),
            "months": [ms.get(m, {}).get("any") for m in range(1, 13)]}


def truthy(v):
    return bool(v is True or (isinstance(v, (int, float, np.bool_, np.integer, np.floating)) and v == 1))


# ---------------- plain-language explanations ----------------
def explain(key, v):
    """Turn a model input and its value into one short sentence."""
    if v is None:
        return None
    f = {
        "obs_bloom_7d": lambda v: "A bloom was seen on a clear day this past week" if v >= 0.5 else None,
        "obs_anomaly": lambda v: f"Latest clear-sky chlorophyll was {10 ** v:.1f}× normal",
        "obs_chl_log": lambda v: f"Latest clear-sky chlorophyll: {10 ** v:.1f} mg/m³",
        "obs_age": lambda v: "Clear-sky view today" if int(v) == 0 else f"Last clear-sky view {int(v)} day{'s' if int(v) != 1 else ''} ago",
        "fjord_chl_log": lambda v: f"High-resolution fjord view: {10 ** v:.1f} mg/m³",
        "fjord_age": lambda v: f"Last fjord view {int(v)} days ago",
        "anomaly_log": lambda v: f"Chlorophyll {10 ** v:.1f}× normal (5 days ago)",
        "anomaly_7d_max": lambda v: f"Chlorophyll peaked at {10 ** v:.1f}× normal recently",
        "chl_log": lambda v: f"Chlorophyll {10 ** v:.1f} mg/m³ (5 days ago)",
        "chl_3d": lambda v: f"Chlorophyll around {10 ** v:.1f} mg/m³",
        "chl_7d": lambda v: f"Weekly chlorophyll around {10 ** v:.1f} mg/m³",
        "chl_change_3d": lambda v: f"Chlorophyll {'rising' if v > 0 else 'falling'} ({10 ** v:.1f}× in 3 days)",
        "bloom_fraction": lambda v: f"{v:.0%} of nearby sea showed bloom levels",
        "candidate_today": lambda v: "Bloom-like signal nearby" if v >= 0.5 else None,
        "days_since_candidate": lambda v: f"Last bloom signal {int(v)} days ago" if v < 120 else "No bloom signal for months",
        "obs_today": lambda v: "Clear-sky view today" if v >= 0.5 else None,
        "sst": lambda v: f"Sea temperature {v:.1f} °C",
        "sst_anomaly": lambda v: f"Sea {abs(v):.1f} °C {'warmer' if v > 0 else 'colder'} than normal",
        "sst_change_7d": lambda v: f"Sea {'warmed' if v > 0 else 'cooled'} {abs(v):.1f} °C this week",
        "salinity": lambda v: f"Salinity {v:.1f} PSU" + (" (fresh surface water)" if v < 34.5 else ""),
        "salinity_change_7d": lambda v: f"Surface water {'freshening' if v < 0 else 'getting saltier'} this week",
        "mixed_layer": lambda v: f"Surface layer {v:.0f} m deep" + (" (calm, layered water)" if v < 25 else ""),
        "mixed_layer_change_7d": lambda v: f"Surface layer {'shallowing' if v < 0 else 'deepening'} this week",
        "current_speed": lambda v: f"Currents {v:.2f} m/s",
        "wave_height": lambda v: f"Waves {v:.1f} m",
        "wind_7d": lambda v: f"{'Light' if v < 5 else 'Strong' if v > 9 else 'Moderate'} winds this week ({v:.0f} m/s)",
        "sunlight_7d": lambda v: f"{'Sunny' if v > 90 else 'Dull'} week ({v:.0f} MJ/m² of sunlight)",
        "air_temp_7d": lambda v: f"Air temperature {v:.0f} °C this week",
        "rain_7d": lambda v: f"{v:.0f} mm of rain this week",
        "doy_sin": lambda v: "Time of year (bloom season)", "doy_cos": lambda v: "Time of year (bloom season)",
    }.get(key)
    try:
        return f(v) if f else None
    except (TypeError, ValueError, OverflowError):
        return None


# ---------------- endpoints ----------------
@app.get("/api/config")
def app_config():
    m = manifest()
    layers = {}
    for k, v in m["layers"].items():
        if k == "chl300":
            continue
        title, desc, role = LAYER_INFO.get(k, (v["label"], "", ""))
        layers[k] = {**v, "title": title, "description": desc, "role": role}
    return {"layers": layers, "chl300": m["layers"].get("chl300"), "years": m.get("years"),
            "risk": {"low": config.RISK_LOW, "high": config.RISK_HIGH},
            "rule": {"percentile": config.BLOOM_PERCENTILE, "ratio": config.BLOOM_RATIO,
                     "min_chl": config.BLOOM_MIN_CHL, "area": config.BLOOM_SITE_FRACTION,
                     "radius_km": config.SITE_RADIUS_KM, "baseline": list(config.BASELINE_YEARS)}}


@app.get("/api/farms")
def farms():
    """Marine farms with their licence polygon."""
    geo = json.loads(config.FARMS_FILE.read_text()) if config.FARMS_FILE.exists() else {"features": []}
    poly = {f["properties"].get("point"): f["geometry"] for f in geo["features"] if f["properties"].get("point")}
    keys = ["name", "farm", "kind", "lat", "lon", "company", "licence", "area", "region", "species",
            "biomass_limit_t", "in_use", "status"]
    return [{**{k: f.get(k) for k in keys}, "geometry": poly.get(f["name"])} for f in config.FARMS]


@app.get("/api/day")
def day(date: str):
    """Status of every farm on one date: in a bloom? forecast risk?"""
    d = pd.Timestamp(date)
    s = series()
    try:
        today = s.xs(d, level="date")
    except KeyError:
        today = pd.DataFrame()
    fc = forecast()
    try:
        ftoday = fc.xs(d, level="date") if not fc.empty else pd.DataFrame()
    except KeyError:
        ftoday = pd.DataFrame()
    out = {}
    for name in FARM_NAMES:
        r = {"bloom": False, "milky": False, "data": False}
        if name in today.index:
            t = today.loc[name]
            r.update(bloom=truthy(t.get("is_bloom")), milky=truthy(t.get("is_cocco")),
                     data=bool(pd.notna(t.get("chl"))), ratio=clean_value(t.get("chl_anomaly_ratio"), 2))
        if name in ftoday.index:
            f = ftoday.loc[name]
            r.update(risk_3d=clean_value(f.risk_3d, 3), risk_7d=clean_value(f.risk_7d, 3),
                     level_3d=f.level_3d, level_7d=f.level_7d)
        out[name] = r
    return out


@app.get("/api/timeline")
def timeline(year: int):
    """Per day of a year: how many farms were in a bloom / at high forecast risk (for the time bar)."""
    s = series().reset_index()
    s = s[s.site.isin(FARM_NAMES) & (s.date.dt.year == year)]
    g = s.groupby("date").agg(bloom=("is_bloom", lambda x: int(x.map(truthy).sum())),
                              milky=("is_cocco", lambda x: int(x.map(truthy).sum())),
                              data=("chl", lambda x: int(x.notna().sum())))
    fc = forecast()
    if not fc.empty:
        f = fc.reset_index()
        f = f[f.site.isin(FARM_NAMES) & (f.date.dt.year == year)]
        g = g.join(f.groupby("date").agg(high=("level_3d", lambda x: int((x == "high").sum()))))
    g = g.fillna(0).reset_index()
    g["date"] = g.date.dt.strftime("%Y-%m-%d")
    return {"farms": len(FARM_NAMES), "days": g.to_dict("records")}


@lru_cache
def calibration():
    """How often a bloom really followed, by forecast risk level (walk-forward, out of sample)."""
    fc = forecast()
    if fc.empty:
        return None
    f = fc.reset_index()
    f = f[f.site.isin(FARM_NAMES)]
    out = {}
    for h in config.HORIZONS:
        d = f.dropna(subset=[f"bloom_in_{h}d"])
        bins = [0, 0.1, 0.3, 0.6, 0.8, 1.01]
        lab = ["0–10%", "10–30%", "30–60%", "60–80%", "80–100%"]
        d = d.assign(b=pd.cut(d[f"risk_{h}d"], bins, labels=lab, right=False))
        t = d.groupby("b", observed=False)[f"bloom_in_{h}d"].agg(["mean", "size"])
        blooms = d[d[f"bloom_in_{h}d"] == 1]
        out[f"{h}d"] = {
            "buckets": [{"range": k, "observed": clean_value(r["mean"], 3), "n": int(r["size"])} for k, r in t.iterrows()],
            "caught_medium_or_high": clean_value((blooms[f"risk_{h}d"] >= config.RISK_LOW).mean(), 3),
            "high_precision": clean_value(d[d[f"risk_{h}d"] >= config.RISK_HIGH][f"bloom_in_{h}d"].mean(), 3),
            "years": f"{int(d.date.dt.year.min())}–{int(d.date.dt.year.max())}", "n": int(len(d)),
        }
    return out


@lru_cache
def early_warning():
    """Per confirmed bloom (2019-2025, farms): was it flagged before it started, and how fast once it had?"""
    fc = forecast()
    if fc.empty:
        return None
    ev = events()
    ev = ev[(ev.status == "confirmed") & (ev.kind == "high-biomass") & ev.site.isin(FARM_NAMES) & (ev.start.dt.year >= 2019)]
    pre3, pre7, day2 = [], [], []
    for e in ev.itertuples():
        if e.site not in fc.index.get_level_values(0):
            continue
        s = fc.loc[e.site]
        b3 = s.loc[e.start - pd.Timedelta(days=3): e.start - pd.Timedelta(days=1)]
        b7 = s.loc[e.start - pd.Timedelta(days=7): e.start - pd.Timedelta(days=1)]
        if b3.empty:
            continue
        pre3.append(b3.risk_3d.max() >= config.RISK_LOW)
        pre7.append(b7.risk_7d.max() >= config.RISK_LOW)
        d2 = s.loc[e.start: e.start + pd.Timedelta(days=1)]
        day2.append(bool(len(d2)) and d2.risk_3d.max() >= config.RISK_LOW)
    n = len(pre3)
    return {"blooms": n, "years": "2019–2025", "warned_3d_before": float(np.mean(pre3)) if n else None,
            "warned_7d_before": float(np.mean(pre7)) if n else None, "flagged_by_day2": float(np.mean(day2)) if n else None}


@app.get("/api/confidence")
def confidence():
    m = {}
    for name, f in [("bloom", config.DATA / "models" / "bloom_metrics.json"),
                    ("toxic", config.DATA / "models" / "toxic_metrics.json"),
                    ("validation", config.PROCESSED / "validation.json")]:
        m[name] = json.loads(f.read_text()) if f.exists() else None
    f = config.DATA / "models" / "pixel_metrics.json"
    m["pixel"] = json.loads(f.read_text()) if f.exists() else None
    m["calibration"] = calibration()
    m["early_warning"] = early_warning()
    return m


def bucket_note(h, risk):
    c = calibration()
    if not c or risk is None:
        return None
    for b in c[f"{h}d"]["buckets"]:
        lo, hi = [float(x.strip("%")) / 100 for x in b["range"].split("–")]
        if lo <= risk < hi + 1e-9 and b["observed"] is not None:
            return {"range": b["range"], "observed": b["observed"], "n": b["n"], "years": c[f"{h}d"]["years"]}
    return None


@app.get("/api/farm")
def farm(name: str, date: str):
    if name not in FARM_NAMES:
        raise HTTPException(404, f"unknown farm {name}")
    d = pd.Timestamp(date)
    info = next(f for f in config.FARMS if f["name"] == name)
    s = series().loc[name]
    row = s.loc[d] if d in s.index else None

    def g(k):
        return clean_value(row.get(k)) if row is not None and k in row else None

    # --- today's conditions (the model inputs) ---
    win = s.loc[d - pd.Timedelta(days=60): d]

    def spark(col):
        return [clean_value(v, 3) for v in win[col]] if col in win else []
    inputs = [
        {"key": "chl", "label": "Chlorophyll", "value": g("chl"), "unit": "mg/m³", "spark": spark("chl")},
        {"key": "anomaly", "label": "Vs normal", "value": g("chl_anomaly_ratio"), "unit": "×", "spark": spark("chl_anomaly_ratio")},
        {"key": "sst", "label": "Sea temperature", "value": g("sst"), "unit": "°C", "spark": spark("sst")},
        {"key": "salinity", "label": "Salinity", "value": g("salinity"), "unit": "PSU", "spark": spark("salinity")},
        {"key": "mixed_layer", "label": "Surface layer", "value": g("mixed_layer"), "unit": "m", "spark": spark("mixed_layer")},
        {"key": "currents", "label": "Currents", "value": g("current_speed"), "unit": "m/s", "spark": spark("current_speed")},
        {"key": "waves", "label": "Waves", "value": g("wave_height"), "unit": "m", "spark": spark("wave_height")},
        {"key": "wind", "label": "Wind", "value": g("wind_speed_10m_mean"), "unit": "m/s", "spark": spark("wind_speed_10m_mean")},
        {"key": "sun", "label": "Sunlight", "value": g("shortwave_radiation_sum"), "unit": "MJ/m²", "spark": spark("shortwave_radiation_sum")},
    ]

    # --- how we decide it is a bloom: the rule, applied to this farm on this date ---
    allev = events()
    ev = allev[(allev.site == name) & (allev.start <= d) & (allev.end >= d)]
    hb = ev[ev.kind == "high-biomass"]
    e = hb.sort_values("status").iloc[0] if len(hb) else None
    # inside a bloom: test it on its peak day (that is how it was detected); otherwise test today
    ref = s.loc[e.peak_date] if e is not None and e.peak_date in s.index else row

    def gr(k):
        return clean_value(ref.get(k)) if ref is not None and k in ref else None
    chl, thr, ratio, frac = gr("chl"), gr("chl_threshold"), gr("chl_anomaly_ratio"), gr("bloom_fraction")
    checks = None
    if chl is not None:
        checks = {"bloom": truthy(row.get("is_bloom")) if row is not None else False,
                  "on": e.peak_date.strftime("%Y-%m-%d") if e is not None else date, "steps": [
            {"label": f"Algae above {config.BLOOM_MIN_CHL:g} mg/m³", "value": f"{chl:.1f} mg/m³",
             "ok": chl >= config.BLOOM_MIN_CHL},
            {"label": f"At least {config.BLOOM_RATIO:g}× the normal level", "value": f"{ratio:.1f}× normal" if ratio else "–",
             "ok": bool(ratio and ratio >= config.BLOOM_RATIO)},
            {"label": f"Rarer than {config.BLOOM_PERCENTILE}% of days on this date (1998–2022)",
             "value": f"limit {thr:.1f} mg/m³" if thr else "–", "ok": bool(thr and chl >= thr)},
            {"label": f"Covers ≥{config.BLOOM_SITE_FRACTION:.0%} of the sea within {config.SITE_RADIUS_KM} km",
             "value": f"{frac:.0%}" if frac is not None else "–",
             "ok": bool(frac is not None and frac >= config.BLOOM_SITE_FRACTION)},
            {"label": "Lasts at least 3 days", "value": f"{int(e.days)} days" if e is not None else "–",
             "ok": bool(e is not None and e.days >= 3)},
            {"label": "Seen by the satellite on a clear day",
             "value": f"{int(e.observed_days)} clear day{'s' if e.observed_days != 1 else ''}" if e is not None else "–",
             "ok": bool(e is not None and e.observed_days > 0)},
        ]}
    # milky-water (coccolithophore) test, shown when that is what is happening
    mk = ev[ev.kind == "coccolithophore"]
    milky_checks = None
    if len(mk) or truthy(row.get("cocco_day")) if row is not None else False:
        e = mk.iloc[0] if len(mk) else None
        rrs = g("rrs555")
        normal = s[s.index.month == d.month]["rrs555"].median()
        ratio_r = rrs / normal if rrs is not None and normal and normal > 0 else None
        secchi = s[s.index.month.isin(range(3, 11))]["secchi"].median()
        milky_checks = {"bloom": truthy(row.get("is_cocco")) if row is not None else False, "steps": [
            {"label": "Very bright green light (≥ 0.008 sr⁻¹)", "value": f"{rrs:.4f}" if rrs is not None else "cloudy today",
             "ok": bool(rrs is not None and rrs >= 0.008)},
            {"label": f"At least 3× this farm's normal for {d.strftime('%B')}", "value": f"{ratio_r:.1f}×" if ratio_r else "–",
             "ok": bool(ratio_r and ratio_r >= 3)},
            {"label": "Clear water, not muddy (Secchi ≥ 5 m)", "value": f"{secchi:.0f} m" if pd.notna(secchi) else "–",
             "ok": bool(pd.notna(secchi) and secchi >= 5)},
            {"label": "In coccolithophore season (May–Oct)", "value": d.strftime("%B"), "ok": 5 <= d.month <= 10},
            {"label": "Seen on at least 2 clear days", "value": f"{int(e.observed_days)} clear days" if e is not None else "–",
             "ok": bool(e is not None and e.observed_days >= 2)},
        ]}
    current = None
    if len(ev):
        e = ev.sort_values("status").iloc[0]
        current = {"kind": e.kind, "start": e.start.strftime("%Y-%m-%d"), "end": e.end.strftime("%Y-%m-%d"),
                   "days": int(e.days), "day": int((d - e.start).days) + 1, "status": e.status,
                   "type": e.type, "peak_chl": clean_value(e.peak_chl, 1), "peak_ratio": clean_value(e.peak_ratio, 1),
                   "reason": e.reason}

    hist = allev[(allev.site == name) & (allev.status == "confirmed")].sort_values("start", ascending=False)
    history = [{"kind": e.kind, "start": e.start.strftime("%Y-%m-%d"), "peak": e.peak_date.strftime("%Y-%m-%d"),
                "days": int(e.days), "peak_chl": clean_value(e.peak_chl, 1), "peak_ratio": clean_value(e.peak_ratio, 1),
                "type": e.type} for e in hist.itertuples()]

    # --- forecast issued on this date ---
    fc = forecast()
    fcast = None
    if not fc.empty and (name, d) in fc.index:
        f = fc.loc[(name, d)]
        fcast = {"trained_until": int(f.model_trained_until)}
        for h in config.HORIZONS:
            facs = []
            for x in json.loads(f[f"factors_{h}d"]):
                t = explain(x.get("key"), x.get("value"))
                if t and t not in [y["text"] for y in facs]:
                    facs.append({"text": t, "push": x.get("push")})
            fcast[f"{h}d"] = {"risk": clean_value(f[f"risk_{h}d"], 3), "level": f[f"level_{h}d"],
                              "factors": facs, "happened": clean_value(f[f"bloom_in_{h}d"]),
                              "confidence": bucket_note(h, float(f[f"risk_{h}d"]))}
        fs = fc.loc[name].loc[d - pd.Timedelta(days=60): d + pd.Timedelta(days=21)]
        fcast["series"] = {"date": [x.strftime("%Y-%m-%d") for x in fs.index],
                           "risk_3d": [clean_value(v, 3) for v in fs.risk_3d],
                           "risk_7d": [clean_value(v, 3) for v in fs.risk_7d],
                           "bloom": [int(v) for v in fs.is_bloom_today]}

    toxic = None
    if info["kind"] == "shellfish":  # their main risk is toxins, which satellites cannot see
        toxic = {"month": toxic_by_month().get(d.month), "station": nearest_station(info["lat"], info["lon"])}

    return {"info": info, "date": date, "inputs": inputs, "spark_dates": [x.strftime("%Y-%m-%d") for x in win.index],
            "checks": checks, "milky_checks": milky_checks, "current": current, "history": history,
            "forecast": fcast, "toxic": toxic}


@lru_cache(maxsize=64)
def stream_year(stream, year):
    f = config.RAW / f"{stream}_iceland_{year}.nc"
    return xr.open_dataset(f) if f.exists() else None


@lru_cache
def clim():
    return xr.open_dataset(config.PROCESSED / "chl_clim.nc")


@app.get("/api/value")
def value(layer: str, date: str, lat: float, lon: float):
    """Value of a map layer under the cursor."""
    if layer not in VALUE_SRC:
        raise HTTPException(404, "unknown layer")
    stream, var, how = VALUE_SRC[layer]
    d = pd.Timestamp(date)
    ds = stream_year(stream, d.year)
    if ds is None:
        return {"value": None}
    try:
        pt = ds.sel(latitude=lat, longitude=lon, method="nearest")
        if abs(float(pt.latitude) - lat) > 0.1 or abs(float(pt.longitude) - lon) > 0.2:
            return {"value": None}
        sel = pt.sel(time=d, method="nearest")
        if abs((pd.Timestamp(sel.time.values).normalize() - d).days) > 0:
            return {"value": None}
        v = float(np.squeeze(sel[var].values))
        if how == "kelvin":
            v -= 273.15
        elif how == "speed":
            v = float(np.hypot(v, np.squeeze(sel["vo"].values)))
        elif how == "anomaly":
            c = clim()["log10_chl_clim"].sel(doy=d.dayofyear, latitude=lat, longitude=lon, method="nearest")
            v = v / 10 ** float(c)
    except (KeyError, ValueError):
        return {"value": None}
    return {"value": clean_value(v)}


# The web app reads the static export (site/, built by src/export_static.py); the /api routes above
# stay available for exploring the data.
SITE = ROOT / "site"
if (SITE / "data").exists():
    app.mount("/", StaticFiles(directory=SITE, html=True), name="site")
else:
    app.mount("/tiles", StaticFiles(directory=TILES, check_dir=False), name="tiles")
    app.mount("/", StaticFiles(directory=config.WEB, html=True), name="web")
