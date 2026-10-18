"""Export the web app as a static site (for Netlify or any static host).

Writes <root>/site/:
  index.html, app.js, style.css          the web app
  data/config.json                       layers (date ranges + colour lookup tables), rule, toxin tables
  data/farms.json                        marine farms (company names removed for the public site)
  data/confidence.json                   model scores, calibration, early warning, validation
  data/timeline/<year>.json              farms in a bloom / at high risk per day
  data/day/<year>.json                   status of every farm on every day (compact columns)
  data/farm/<slug>.json                  one farm: daily inputs, bloom-test values, flags, forecast, events
  tiles/<layer>/<date>.png               map overlays, March-October only
Nothing from data/raw, no parquet tables and no MAST database (operators' ID numbers) is published.
Usage: python src/export_static.py [--keep-companies]
"""
import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
import config  # noqa: E402
import common  # noqa: E402
import make_tiles  # noqa: E402
from api import main as api  # noqa: E402  (reuse the API's computations)

OUT = ROOT / "site"
SEASON = (3, 10)
DAILY = {  # farm file columns: name -> (source column, decimals)
    "chl": ("chl", 3), "ratio": ("chl_anomaly_ratio", 2), "sst": ("sst", 2), "sal": ("salinity", 2),
    "mld": ("mixed_layer", 1), "cur": ("current_speed", 3), "wav": ("wave_height", 2),
    "wind": ("wind_speed_10m_mean", 1), "sun": ("shortwave_radiation_sum", 1),
    "thr": ("chl_threshold", 2), "frac": ("bloom_fraction", 3), "rrs": ("rrs555", 4),
    "bloom": ("is_bloom", 0), "milky": ("is_cocco", 0), "cocco": ("cocco_day", 0),
    "r3": ("risk_3d", 3), "r7": ("risk_7d", 3), "b3": ("bloom_in_3d", 0), "b7": ("bloom_in_7d", 0),
}


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


def num(v, d):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    if isinstance(v, (bool, np.bool_)):
        return int(v)
    v = float(v)
    if not math.isfinite(v):
        return None
    return int(round(v)) if d == 0 else round(v, d)


def in_season(d):
    return SEASON[0] <= int(d[5:7]) <= SEASON[1]


def ranges(dates):
    """['2016-03-01', '2016-03-02', ...] -> [['2016-03-01', '2016-03-02'], ...] contiguous runs."""
    ds = sorted(pd.to_datetime(dates))
    out = []
    for d in ds:
        if out and (d - out[-1][1]).days == 1:
            out[-1][1] = d
        else:
            out.append([d, d])
    return [[a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")] for a, b in out]


def lut(key):
    """128-step colour lookup table so the browser can turn a tile pixel back into a value."""
    spec = make_tiles.LAYERS[key]
    from matplotlib import colormaps
    cm = colormaps[spec["cmap"]] if isinstance(spec["cmap"], str) else spec["cmap"]
    n = spec["norm"]
    t = np.linspace(0, 1, 128)
    vals = n.inverse(t)
    return [[float(v), *[int(c * 255) for c in cm(x)[:3]]] for v, x in zip(np.ravel(vals), t)]


def export_config():
    c = api.app_config()
    for k, L in c["layers"].items():
        L["dates"] = ranges([d for d in L["dates"] if in_season(d)])
        L["lut"] = lut(k) if k in make_tiles.LAYERS else None
    if c.get("chl300"):
        for site in c["chl300"]["sites"].values():
            site["dates"] = ranges([d for d in site["dates"] if in_season(d)])
    t = api.toxic_table()
    c["toxic_by_month"] = api.toxic_by_month()
    stations = []
    for st in config.HAFRO_STATIONS:
        info = api.nearest_station(st["lat"], st["lon"])
        if info and info["name"] == st["name"]:
            stations.append({**info, "lat": st["lat"], "lon": st["lon"]})
    c["stations"] = stations
    c["horizons"] = config.HORIZONS
    c["season"] = list(SEASON)
    c["toxic_samples"] = int(len(t))
    write(OUT / "data" / "config.json", c)
    return c


def export_farms(keep_companies):
    farms = api.farms()
    if not keep_companies:  # public site: no operator names (and no licence codes, which identify them)
        for f in farms:
            f.pop("company", None)
            f.pop("licence", None)
    for f in farms:
        f["slug"] = common.slug(f["name"])
    write(OUT / "data" / "farms.json", farms)
    return farms


def export_confidence():
    write(OUT / "data" / "confidence.json", api.confidence())


def export_timelines(years):
    for y in years:
        write(OUT / "data" / "timeline" / f"{y}.json", api.timeline(y))


def export_days(years, farms):
    s = api.series().reset_index()
    fc = api.forecast().reset_index()
    names = [f["name"] for f in farms]
    s = s[s.site.isin(names)]
    fc = fc[fc.site.isin(names)]
    for y in years:
        a = s[(s.date.dt.year == y) & s.date.dt.month.between(*SEASON)]
        b = fc[(fc.date.dt.year == y) & fc.date.dt.month.between(*SEASON)]
        dates = sorted(a.date.dt.strftime("%Y-%m-%d").unique())
        A = a.assign(d=a.date.dt.strftime("%Y-%m-%d")).set_index(["d", "site"])
        B = b.assign(d=b.date.dt.strftime("%Y-%m-%d")).set_index(["d", "site"])
        cols = {"bloom": [], "milky": [], "data": [], "ratio": [], "r3": [], "r7": []}
        for d in dates:
            ad = A.loc[d].reindex(names) if d in A.index.get_level_values(0) else pd.DataFrame(index=names)
            bd = B.loc[d].reindex(names) if d in B.index.get_level_values(0) else pd.DataFrame(index=names)
            cols["bloom"].append("".join("1" if api.truthy(v) else "0" for v in ad.get("is_bloom", pd.Series(index=names))))
            cols["milky"].append("".join("1" if api.truthy(v) else "0" for v in ad.get("is_cocco", pd.Series(index=names))))
            cols["data"].append("".join("1" if pd.notna(v) else "0" for v in ad.get("chl", pd.Series(index=names))))
            cols["ratio"].append([num(v, 1) for v in ad.get("chl_anomaly_ratio", pd.Series(index=names))])
            cols["r3"].append([num(v, 3) for v in bd.get("risk_3d", pd.Series(index=names))])
            cols["r7"].append([num(v, 3) for v in bd.get("risk_7d", pd.Series(index=names))])
        write(OUT / "data" / "day" / f"{y}.json", {"farms": names, "dates": dates, **cols})
        print(f"  day {y}: {len(dates)} days", flush=True)


def export_farm_files(farms, first, last):
    s = api.series()
    fc = api.forecast()
    ev = api.events()
    idx = pd.date_range(first, last, freq="D")
    for f in farms:
        name = f["name"]
        g = s.loc[name].reindex(idx)
        h = fc.loc[name].reindex(idx) if name in fc.index.get_level_values(0) else pd.DataFrame(index=idx)
        both = g.join(h[[c for c in ["risk_3d", "risk_7d", "bloom_in_3d", "bloom_in_7d"] if c in h]], how="left")
        daily = {k: [num(v, d) for v in (both[c] if c in both else [None] * len(idx))] for k, (c, d) in DAILY.items()}
        trained = (h.model_trained_until.dropna().groupby(h.model_trained_until.dropna().index.year).first()
                   if "model_trained_until" in h else pd.Series(dtype=float))
        full = s.loc[name]
        rrs_norm = full.groupby(full.index.month)["rrs555"].median()
        secchi = full[full.index.month.isin(range(3, 11))]["secchi"].median()
        e = ev[ev.site == name]
        events = [{"kind": r.kind, "status": r.status, "start": r.start.strftime("%Y-%m-%d"), "end": r.end.strftime("%Y-%m-%d"),
                   "peak": r.peak_date.strftime("%Y-%m-%d"), "days": int(r.days), "obs": int(r.observed_days),
                   "peak_chl": num(r.peak_chl, 1), "peak_ratio": num(r.peak_ratio, 1), "type": r.type}
                  for r in e.itertuples() if r.status == "confirmed" or r.end >= pd.Timestamp(first)]
        write(OUT / "data" / "farm" / f"{f['slug']}.json", {
            "name": name, "start": idx[0].strftime("%Y-%m-%d"), "daily": daily,
            "trained_until": {str(int(k)): int(v) for k, v in trained.items()},
            "rrs_normal": [num(rrs_norm.get(m), 5) for m in range(1, 13)], "secchi": num(secchi, 1), "events": events})
    print(f"  {len(farms)} farm files", flush=True)


def export_tiles(c):
    """Hard-link (or copy) the season's tiles into site/tiles."""
    src = config.PROCESSED / "tiles"
    n = 0
    jobs = [(k, [d for a, b in L["dates"] for d in pd.date_range(a, b).strftime("%Y-%m-%d")]) for k, L in c["layers"].items()]
    for k, dates in jobs:
        for d in dates:
            n += link(src / k / f"{d}.png", OUT / "tiles" / k / f"{d}.png")
    for slug, site in (c.get("chl300") or {}).get("sites", {}).items():
        for a, b in site["dates"]:
            for d in pd.date_range(a, b).strftime("%Y-%m-%d"):
                n += link(src / "chl300" / slug / f"{d}.png", OUT / "tiles" / "chl300" / slug / f"{d}.png")
    print(f"  {n} tiles", flush=True)


def link(a, b):
    if not a.exists():
        return 0
    b.parent.mkdir(parents=True, exist_ok=True)
    if b.exists():
        return 1
    try:
        os.link(a, b)
    except OSError:
        shutil.copy2(a, b)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-companies", action="store_true", help="keep operator names (private deployments only)")
    a = ap.parse_args()
    if (OUT / "data").exists():
        shutil.rmtree(OUT / "data")
    OUT.mkdir(exist_ok=True)
    for fn in ["index.html", "app.js", "style.css"]:
        shutil.copy2(config.WEB / fn, OUT / fn)
    c = export_config()
    farms = export_farms(a.keep_companies)
    export_confidence()
    years = list(range(c["years"][0], c["years"][1] + 1))
    export_timelines(years)
    export_days(years, farms)
    export_farm_files(farms, f"{years[0]}-01-01", pd.Timestamp.today().strftime("%Y-%m-%d"))
    export_tiles(c)
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    files = sum(1 for p in OUT.rglob("*") if p.is_file())
    print(f"saved {OUT}: {files} files, {total / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
