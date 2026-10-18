"""Predicted-bloom map: chance of a bloom at EVERY 4 km sea pixel in the next 3 / 7 days.

Same bloom definition as detect_blooms.py (chlorophyll above the pixel's 95th percentile for the
date, >= 2.5x the median and >= 3 mg/m3), but per pixel, so the forecast covers the whole sea and
not only the farms. A pixel-day is "bloom within H days" if any of the next H days is a bloom.
Inputs known on the forecast day (no peeking):
  - latest real cloud-free view (<= 10 days old): how unusual it was, how old it is, bloom seen (<= 7 days)
  - gap-filled chlorophyll DELAYED 5 days (the archive can fill a day with later images): anomaly, bloom,
    and the share of nearby pixels in bloom (about 12 km and 36 km)
  - place and season: latitude, longitude, time of year, the pixel's normal and bloom limit
Train 1998-2018 (a random sample of pixel-days), test 2019-2026. Compared with persistence ("a bloom
was seen in the past week") and the season (the pixel's bloom rate in that month).
Output: <data>/models/pixel_<H>d.txt, pixel_metrics.json, tiles/risk<H>/<date>.png and manifest entries.
Usage: python src/pixel_forecast.py [--no-tiles]
"""
import argparse
import json
import sys
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import xarray as xr
from PIL import Image
from scipy.ndimage import uniform_filter
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import make_tiles  # noqa: E402

warnings.simplefilter("ignore")
MODELS = config.DATA / "models"
TILES = config.PROCESSED / "tiles"
HORIZONS = [3, 7]
LAG = 5            # days: gap-filled chlorophyll is used with this delay
PAD = 20           # days of the previous year kept for lags and look-backs
SEASON = (3, 10)
TRAIN_END, VALID_END = 2015, 2018
SAMPLE = {"train": 0.012, "test": 0.01}
SHOW_MIN = 0.05    # below this chance the map stays clear
ALPHA = {"a0": 40, "a1": 240, "vmax": 0.8}   # tile opacity encodes the chance, so the browser can read it back
GREENS = ["#d7f7c2", "#8fe388", "#34c759", "#13994a", "#06612e"]
NAMES = ["anom4_lag", "bl4_lag", "nb_12km", "nb_36km", "obs_anom", "obs_age", "obs_bloom_7d", "obs_nb_20km",
         "lat", "lon", "doy_sin", "doy_cos", "log_med", "log_thr"]


def yearly(prefix):
    return {int(f.stem[-4:]): f for f in sorted(config.RAW.glob(f"{prefix}_iceland_[0-9][0-9][0-9][0-9].nc"))}


class Grid:
    def __init__(self):
        c = xr.open_dataset(config.PROCESSED / "chl_clim.nc")
        self.med = 10 ** c["log10_chl_clim"].values.astype(np.float32)
        high = 10 ** c["log10_chl_high"].values.astype(np.float32)
        self.thr = np.maximum(np.maximum(high, config.BLOOM_RATIO * self.med), config.BLOOM_MIN_CHL)
        self.lat, self.lon = c.latitude.values, c.longitude.values
        self.ocean = np.isfinite(self.med).any(axis=0)
        self.n = int(self.ocean.sum())
        la, lo = np.meshgrid(self.lat, self.lon, indexing="ij")
        self.la, self.lo = la[self.ocean].astype(np.float32), lo[self.ocean].astype(np.float32)
        self.l4, self.l3 = yearly("chl_l4"), {**yearly("chl_l3_early"), **yearly("pft_l3")}


def nbmean(x, size):
    """Mean of x over a size x size window, ignoring NaN."""
    ok = np.isfinite(x).astype(np.float32)
    s = uniform_filter(np.where(np.isfinite(x), x, 0).astype(np.float32), size, mode="constant")
    c = uniform_filter(ok, size, mode="constant")
    return np.where(c > 0.02, s / np.maximum(c, 1e-6), np.nan)


def load(G, y, carry):
    d4 = xr.open_dataset(G.l4[y])["CHL"]
    t = pd.to_datetime(d4.time.values).normalize()
    a4 = d4.values.astype(np.float32)
    if y in G.l3:
        d3 = xr.open_dataset(G.l3[y])["CHL"]
        d3 = d3.assign_coords(time=pd.to_datetime(d3.time.values).normalize()).reindex(time=t)
        a3 = d3.values.astype(np.float32)
    else:
        a3 = np.full_like(a4, np.nan)
    if carry is not None:
        t = carry[0].append(t)
        a4, a3 = np.concatenate([carry[1], a4]), np.concatenate([carry[2], a3])
    return t, a4, a3


def prepare(G, t, a4, a3):
    doy = t.dayofyear.values - 1
    thr, med = G.thr[doy], G.med[doy]
    with np.errstate(all="ignore"):
        anom4 = np.log10(a4 / med).astype(np.float32)
        bl4 = np.where(np.isfinite(a4), (a4 >= thr).astype(np.float32), np.nan)
        anom3 = np.log10(a3 / med).astype(np.float32)
        bl3 = np.where(np.isfinite(a3), (a3 >= thr).astype(np.float32), np.nan)
    # latest real view (<=10 days) and bloom seen in the past 7 days, carried forward in time
    T = len(t)
    last_an, last_age, last_bl = (np.full(a4.shape, np.nan, np.float32) for _ in range(3))
    cur_an = np.full(a4.shape[1:], np.nan, np.float32)
    cur_age = np.full(a4.shape[1:], 99, np.float32)
    cur_bl = np.full(a4.shape[1:], np.nan, np.float32)
    cur_bl_age = np.full(a4.shape[1:], 99, np.float32)
    for i in range(T):
        seen = np.isfinite(anom3[i])
        cur_an = np.where(seen, anom3[i], cur_an)
        cur_age = np.where(seen, 0, cur_age + 1)
        cur_bl = np.where(seen, bl3[i], cur_bl)
        cur_bl_age = np.where(seen, 0, cur_bl_age + 1)
        last_an[i] = np.where(cur_age <= 10, cur_an, np.nan)
        last_age[i] = np.where(cur_age <= 10, cur_age, np.nan)
        last_bl[i] = np.where(cur_bl_age <= 7, cur_bl, np.nan)
    return anom4, bl4, last_an, last_age, last_bl


def labels(bl4, i, h):
    fut = bl4[i + 1: i + 1 + h]
    if len(fut) < h:
        return None
    with np.errstate(all="ignore"):
        got = np.nanmax(np.where(np.isfinite(fut), fut, -1), axis=0)
    return np.where(got < 0, np.nan, got).astype(np.float32)


def day_features(G, t, prep, i):
    anom4, bl4, last_an, last_age, last_bl = prep
    j = i - LAG
    d = t[i]
    doy = d.dayofyear
    ang = 2 * np.pi * doy / 365.25
    ocean = G.ocean
    cols = [anom4[j][ocean], bl4[j][ocean], nbmean(bl4[j], 3)[ocean], nbmean(bl4[j], 9)[ocean],
            last_an[i][ocean], last_age[i][ocean], last_bl[i][ocean], nbmean(last_bl[i], 5)[ocean],
            G.la, G.lo, np.full(G.n, np.sin(ang), np.float32), np.full(G.n, np.cos(ang), np.float32),
            np.log10(G.med[doy - 1][ocean]), np.log10(G.thr[doy - 1][ocean])]
    return np.column_stack(cols).astype(np.float32)


def walk(G, years, want_all=False, rng=None, frac=None):
    """Yield (date, X, {h: labels}, last_bloom_flag) for every season day of the given years."""
    carry = None
    first = min(G.l4)
    for y in sorted(G.l4):
        if y < first:
            continue
        t, a4, a3 = load(G, y, carry)
        carry = (t[-PAD:], a4[-PAD:], a3[-PAD:])
        if y not in years:
            continue
        prep = prepare(G, t, a4, a3)
        for i in range(PAD if len(t) > PAD and y != first else LAG + 10, len(t)):
            d = t[i]
            if d.year != y or not (SEASON[0] <= d.month <= SEASON[1]):
                continue
            X = day_features(G, t, prep, i)
            ys = {h: labels(prep[1], i, h) for h in HORIZONS}
            yield d, X, {h: (v[G.ocean] if v is not None else None) for h, v in ys.items()}


def seasonal_table(G, years):
    """Per pixel and month: share of days that were a bloom within H days (training years only)."""
    sums = {h: np.zeros((12, G.n)) for h in HORIZONS}
    cnts = {h: np.zeros((12, G.n)) for h in HORIZONS}
    for d, X, ys in walk(G, years):
        for h in HORIZONS:
            if ys[h] is None:
                continue
            ok = np.isfinite(ys[h])
            sums[h][d.month - 1] += np.where(ok, ys[h], 0)
            cnts[h][d.month - 1] += ok
    return {h: sums[h] / np.maximum(cnts[h], 1) for h in HORIZONS}


def collect(G, years, frac, seed):
    rng = np.random.default_rng(seed)
    out = {h: [] for h in HORIZONS}
    meta = []
    for d, X, ys in walk(G, years):
        pick = rng.random(G.n) < frac
        if not pick.any():
            continue
        meta.append(pd.DataFrame({"pix": np.flatnonzero(pick), "month": d.month, "year": d.year}))
        for h in HORIZONS:
            y = ys[h][pick] if ys[h] is not None else np.full(int(pick.sum()), np.nan, np.float32)
            out[h].append(np.column_stack([X[pick], y]))
        print(f"  sampling {d.date()}", end="\r", flush=True)
    print()
    return {h: np.vstack(v) for h, v in out.items()}, pd.concat(meta, ignore_index=True)


def render(G, risk_full, path):
    """Green overlay: opacity encodes the chance (so the page can read it back), colour deepens with it."""
    r = np.where(risk_full >= SHOW_MIN, risk_full, np.nan)
    cm = make_tiles.LinearSegmentedColormap.from_list("g", GREENS)
    rgba = cm(np.clip(r / ALPHA["vmax"], 0, 1))
    a = ALPHA["a0"] + (ALPHA["a1"] - ALPHA["a0"]) * np.clip(r / ALPHA["vmax"], 0, 1)
    rgba[..., 3] = np.where(np.isfinite(r), a / 255.0, 0)
    flip = G.lat[0] > G.lat[-1]
    lats = G.lat[::-1] if flip else G.lat
    rows = make_tiles.mercator_rows(lats, len(lats))
    img = rgba[::-1] if flip else rgba
    Image.fromarray((img[rows] * 255).round().astype(np.uint8), "RGBA").save(path, optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tiles", action="store_true")
    a = ap.parse_args()
    G = Grid()
    print(f"ocean pixels: {G.n}")
    tr_years = [y for y in G.l4 if y <= VALID_END]
    seas = seasonal_table(G, tr_years)
    train, _ = collect(G, [y for y in G.l4 if y <= TRAIN_END], SAMPLE["train"], 1)
    valid, _ = collect(G, [y for y in G.l4 if TRAIN_END < y <= VALID_END], SAMPLE["train"], 2)
    test, tmeta = collect(G, [y for y in G.l4 if y > VALID_END], SAMPLE["test"], 3)
    MODELS.mkdir(parents=True, exist_ok=True)
    params = dict(objective="binary", learning_rate=0.06, num_leaves=63, min_child_samples=200, feature_fraction=0.9,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=2.0, verbose=-1, seed=0)
    results, models = {}, {}
    for h in HORIZONS:
        def split(A):
            A = A[np.isfinite(A[:, -1])]
            return A[:, :-1], A[:, -1].astype(int)
        Xtr, ytr = split(train[h]); Xva, yva = split(valid[h]); Xte_all = test[h]
        ok = np.isfinite(Xte_all[:, -1])
        Xte, yte = Xte_all[ok][:, :-1], Xte_all[ok][:, -1].astype(int)
        m = lgb.train(params, lgb.Dataset(Xtr, ytr, feature_name=NAMES), 1500, valid_sets=[lgb.Dataset(Xva, yva, feature_name=NAMES)],
                      callbacks=[lgb.early_stopping(60, verbose=False)])
        p = m.predict(Xte, num_iteration=m.best_iteration)
        pers = np.nan_to_num(Xte[:, NAMES.index("obs_bloom_7d")])
        pix = tmeta.pix.values[ok]; mon = tmeta.month.values[ok] - 1
        sea = seas[h][mon, pix]
        quiet = (pers == 0) & (np.nan_to_num(Xte[:, NAMES.index("bl4_lag")]) == 0)
        bins = [0, 0.05, 0.15, 0.30, 0.50, 1.01]
        cal = []
        for lo, hi in zip(bins[:-1], bins[1:]):
            s = (p >= lo) & (p < hi)
            cal.append({"range": f"{int(lo * 100)}–{int(min(hi, 1) * 100)}%", "observed": float(yte[s].mean()) if s.sum() else None, "n": int(s.sum())})
        r = {"horizon_days": h, "n_train": int(len(ytr)), "n_test": int(len(yte)), "bloom_rate_test": float(yte.mean()),
             "test_years": f"{VALID_END + 1}–{config.LAST_YEAR}", "best_iteration": int(m.best_iteration),
             "model": {"roc_auc": float(roc_auc_score(yte, p)), "pr_auc": float(average_precision_score(yte, p))},
             "persistence": {"roc_auc": float(roc_auc_score(yte, pers)), "pr_auc": float(average_precision_score(yte, pers))},
             "seasonal": {"roc_auc": float(roc_auc_score(yte, sea)), "pr_auc": float(average_precision_score(yte, sea))},
             "quiet": {"share": float(quiet.mean()), "bloom_rate": float(yte[quiet].mean()),
                       "model_roc_auc": float(roc_auc_score(yte[quiet], p[quiet])), "seasonal_roc_auc": float(roc_auc_score(yte[quiet], sea[quiet])),
                       "model_pr_auc": float(average_precision_score(yte[quiet], p[quiet])), "seasonal_pr_auc": float(average_precision_score(yte[quiet], sea[quiet]))},
             "calibration": cal}
        imp = pd.Series(m.feature_importance("gain"), index=NAMES)
        r["importance"] = {k: float(v) for k, v in (imp / imp.sum()).round(3).sort_values(ascending=False).items()}
        results[f"{h}d"] = r
        final = lgb.train(params, lgb.Dataset(np.vstack([Xtr, Xva]), np.concatenate([ytr, yva]), feature_name=NAMES), max(m.best_iteration, 50))
        final.save_model(str(MODELS / f"pixel_{h}d.txt"))
        models[h] = m
        print(f"\n== bloom within {h} days at any sea pixel · test {r['test_years']} · {len(yte):,} pixel-days · {yte.mean():.1%} in bloom")
        print(f"   ROC-AUC model {r['model']['roc_auc']:.3f} | persistence {r['persistence']['roc_auc']:.3f} | season {r['seasonal']['roc_auc']:.3f}")
        print(f"   PR-AUC  model {r['model']['pr_auc']:.3f} | persistence {r['persistence']['pr_auc']:.3f} | season {r['seasonal']['pr_auc']:.3f}")
        print(f"   NEW blooms (no bloom seen lately; {r['quiet']['share']:.0%} of pixel-days, {r['quiet']['bloom_rate']:.1%} bloom): "
              f"AUC model {r['quiet']['model_roc_auc']:.3f} vs season {r['quiet']['seasonal_roc_auc']:.3f}; PR {r['quiet']['model_pr_auc']:.3f} vs {r['quiet']['seasonal_pr_auc']:.3f}")
        print("   calibration: " + " | ".join(f"{c['range']}: {c['observed'] if c['observed'] is None else round(c['observed'] * 100)}% of {c['n']:,}" for c in cal))
        print("   drivers: " + ", ".join(f"{k} {v:.0%}" for k, v in list(r["importance"].items())[:6]))
    (MODELS / "pixel_metrics.json").write_text(json.dumps(results, indent=1))
    if a.no_tiles:
        return

    # ---- render the map for the years shown in the app ----
    lo, hi = config.APP_YEARS
    mf = TILES / "manifest.json"
    manifest = json.loads(mf.read_text())
    ds0 = xr.open_dataset(G.l4[max(G.l4)])
    bnds = make_tiles.bounds(ds0)
    dates = {h: [] for h in HORIZONS}
    for h in HORIZONS:
        (TILES / f"risk{h}").mkdir(parents=True, exist_ok=True)
    for d, X, ys in walk(G, [y for y in G.l4 if lo <= y <= hi]):
        for h in HORIZONS:
            risk = np.full(G.ocean.shape, np.nan, np.float32)
            risk[G.ocean] = models[h].predict(X, num_iteration=models[h].best_iteration)
            risk[~np.isfinite(G.med[d.dayofyear - 1])] = np.nan
            render(G, risk, TILES / f"risk{h}" / f"{d.strftime('%Y-%m-%d')}.png")
            dates[h].append(d.strftime("%Y-%m-%d"))
        print(f"  tiles {d.date()}", end="\r", flush=True)
    print()
    cm = make_tiles.LinearSegmentedColormap.from_list("g", GREENS)
    stops = [{"v": float(v), "c": "#%02x%02x%02x" % tuple(int(c * 255) for c in cm(v / ALPHA["vmax"])[:3])} for v in np.linspace(0, ALPHA["vmax"], 6)]
    for h in HORIZONS:
        manifest["layers"][f"risk{h}"] = {
            "label": f"Predicted bloom chance, next {h} days", "units": "chance", "ticks": [0.1, 0.2, 0.4, 0.6, 0.8], "log": False,
            "min": 0.0, "max": ALPHA["vmax"], "stops": stops, "bounds": bnds, "dates": dates[h], "kind": "region", "alpha": ALPHA}
    mf.write_text(json.dumps(manifest))
    print(f"saved tiles + manifest ({sum(len(v) for v in dates.values())} tiles)")


if __name__ == "__main__":
    main()
