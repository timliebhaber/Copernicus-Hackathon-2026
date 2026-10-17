"""Forecast NEW blooms: will a confirmed bloom START at this farm within 3 or 7 days?

Only farm-days with no confirmed bloom in the past week are used (a bloom already there is shown,
not forecast). On top of train_bloom.py's inputs this adds
  - blooms near the farm: rings 0-10 / 10-30 / 30-60 km and upstream (build_ring_features.py),
    from cloud-free views (known on the day) and gap-filled data (5-day delay);
  - physical triggers (Gudmundsson 1998; Zhai et al. 2012): surface layer shallowing over 14 days,
    freshening, warming, calm wind over 3 days, sunlight vs normal.
Split by time: train 1998-2015, validate 2016-2018, test 2019-2026. Compared with the season
(site x time-of-year onset rate) and with the current bloom model (train_bloom.py).
Alert levels (watch / medium / high) are picked on the validation years by precision.
Output: <data>/models/onset_<H>d.txt, <data>/models/onset_metrics.json
Usage: python src/train_onset.py
"""
import json
import sys
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import train_bloom as tb  # noqa: E402

warnings.simplefilter("ignore")
MODELS = config.DATA / "models"
HORIZONS = [3, 7]
QUIET_DAYS = 7
LEVEL_PRECISION = {"watch": 0.15, "medium": 0.30, "high": 0.50}

RING_OBS = ["obs_bloom_r1", "obs_bloom_r2", "obs_bloom_r3", "obs_valid_r2", "obs_valid_r3",
            "obs_anom_max_10_60", "obs_bloom_upstream"]
RING_L4 = ["l4_bloom_r1", "l4_bloom_r2", "l4_bloom_r3", "l4_bloom_upstream"]   # used with 5-day delay
PHYS_EXTRA = ["mixed_layer_change_14d", "salinity_change_14d", "sst_change_14d", "wind_3d", "sunlight_anomaly"]
FEATURES = tb.FEATURES + RING_OBS + [f"{c}_lag" for c in RING_L4] + PHYS_EXTRA

LABELS = {**tb.LABELS,
          "obs_bloom_r1": "bloom seen within 10 km", "obs_bloom_r2": "bloom seen 10-30 km away",
          "obs_bloom_r3": "bloom seen 30-60 km away", "obs_valid_r2": "clear view 10-30 km",
          "obs_valid_r3": "clear view 30-60 km", "obs_anom_max_10_60": "strongest chlorophyll 10-60 km away",
          "obs_bloom_upstream": "bloom seen upstream", "l4_bloom_r1_lag": "bloom within 10 km (5 days ago)",
          "l4_bloom_r2_lag": "bloom 10-30 km away (5 days ago)", "l4_bloom_r3_lag": "bloom 30-60 km away (5 days ago)",
          "l4_bloom_upstream_lag": "bloom upstream (5 days ago)", "mixed_layer_change_14d": "surface layer change (14 days)",
          "salinity_change_14d": "salinity change (14 days)", "sst_change_14d": "sea temperature change (14 days)",
          "wind_3d": "wind (last 3 days)", "sunlight_anomaly": "sunlight vs normal"}


def build_onset_table():
    df, sites = tb.build_features()
    # blooms near the farm
    r = pd.read_parquet(config.PROCESSED / "ring_features.parquet")
    r = r.sort_values(["site", "date"])
    for c in RING_L4:
        r[f"{c}_lag"] = r.groupby("site")[c].shift(tb.CHL_LAG_DEFAULT)
    df = df.merge(r[["site", "date"] + RING_OBS + [f"{c}_lag" for c in RING_L4]], on=["site", "date"], how="left")
    # extra physical triggers
    s = pd.read_parquet(config.PROCESSED / "site_series.parquet",
                        columns=["site", "date", "mixed_layer", "salinity", "sst", "wind_speed_10m_mean", "shortwave_radiation_sum"])
    extra = []
    for site, g in s.groupby("site"):
        g = g.set_index("date").sort_index()
        e = pd.DataFrame(index=g.index)
        e["mixed_layer_change_14d"] = g.mixed_layer - g.mixed_layer.shift(14)
        e["salinity_change_14d"] = g.salinity - g.salinity.shift(14)
        e["sst_change_14d"] = g.sst - g.sst.shift(14)
        e["wind_3d"] = g.wind_speed_10m_mean.rolling(3, min_periods=2).mean()
        sun = g.shortwave_radiation_sum.rolling(7, min_periods=3).mean()
        base = sun[(sun.index.year >= config.BASELINE_YEARS[0]) & (sun.index.year <= config.BASELINE_YEARS[1])]
        clim = base.groupby(base.index.dayofyear).mean().reindex(range(1, 367)).interpolate(limit_direction="both")
        e["sunlight_anomaly"] = sun - clim.reindex(g.index.dayofyear).values
        e["site"] = site
        extra.append(e.reset_index())
    df = df.merge(pd.concat(extra), on=["site", "date"], how="left")
    # quiet population: no confirmed bloom at this farm in the past week (including today)
    b = pd.read_parquet(config.PROCESSED / "blooms.parquet", columns=["site", "date", "is_bloom"]).sort_values(["site", "date"])
    b["recent"] = b.groupby("site").is_bloom.transform(lambda x: x.astype(float).rolling(QUIET_DAYS + 1, min_periods=1).max())
    df = df.merge(b[["site", "date", "recent"]], on=["site", "date"], how="left")
    df["quiet"] = df.recent.fillna(0) == 0
    # target: a confirmed high-biomass bloom starts within (t, t+h]
    ev = pd.read_parquet(config.PROCESSED / "bloom_events.parquet")
    ev = ev[(ev.status == "confirmed") & (ev.kind == "high-biomass")]
    starts = {k: np.sort(g.start.values) for k, g in ev.groupby("site")}
    for h in HORIZONS:
        out = np.zeros(len(df), dtype=float)
        for site, idx in df.groupby("site").groups.items():
            st = starts.get(site, np.array([], dtype="datetime64[ns]"))
            t = df.loc[idx, "date"].values
            lo = np.searchsorted(st, t, side="right")                       # first start after t
            hi = np.searchsorted(st, t + np.timedelta64(h, "D"), side="right")
            out[np.asarray(idx)] = (hi > lo).astype(float)
        df[f"onset_in_{h}d"] = np.where(df[f"bloom_in_{h}d"].notna(), out, np.nan)
    df["area"] = df.site.map(config.AREA_OF).fillna(df.site)
    return df, sites


def seasonal(train, test, target):
    """Onset rate for the site and time of year (+-15 days) in the training years."""
    tr = train.assign(doy=train.date.dt.dayofyear)
    base = tr[target].mean()
    rates = {}
    for site, g in tr.groupby("site"):
        r = np.full(367, np.nan)
        for d in range(1, 367):
            w = g[(np.abs(g.doy - d) <= 15)]
            if len(w) >= 20:
                r[d] = (w[target].sum() + 5 * base) / (len(w) + 5)
        rates[site] = r
    doy = test.date.dt.dayofyear.values
    return np.array([rates.get(s, np.full(367, np.nan))[d] for s, d in zip(test.site.values, doy)], dtype=float)


def levels_from_validation(y, p):
    """Lowest threshold whose validation precision reaches each level's target."""
    out = {}
    cand = np.unique(np.quantile(p, np.linspace(0.5, 0.999, 300)))
    for name, target in LEVEL_PRECISION.items():
        ok = [c for c in cand if (p >= c).sum() >= 30 and y[p >= c].mean() >= target]
        out[name] = float(min(ok)) if ok else None
    return out


def boot(y, p1, p2, groups, n=400, seed=0):
    rng = np.random.default_rng(seed)
    ug = np.unique(groups)
    idx = {g: np.where(groups == g)[0] for g in ug}
    d = []
    for _ in range(n):
        i = np.concatenate([idx[g] for g in rng.choice(ug, len(ug))])
        if len(set(y[i])) < 2 or not np.isfinite(p2[i]).all():
            continue
        d.append(roc_auc_score(y[i], p1[i]) - roc_auc_score(y[i], p2[i]))
    return [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))] if d else None


def warning_stats(test, p, h, thr):
    """Per onset: warned at >= thr in the h days before it? Plus false alarms and alert load."""
    t = test.assign(p=p).sort_values(["site", "date"])
    farms = {f["name"] for f in config.FARMS}
    ev = pd.read_parquet(config.PROCESSED / "bloom_events.parquet")
    ev = ev[(ev.status == "confirmed") & (ev.kind == "high-biomass") & ev.site.isin(farms) & (ev.start >= t.date.min())]
    tt = t.set_index(["site", "date"]).p
    warned, n = 0, 0
    for e in ev.itertuples():
        if e.site not in tt.index.get_level_values(0):
            continue
        w = tt.loc[e.site].loc[e.start - pd.Timedelta(days=h): e.start - pd.Timedelta(days=1)]
        if w.empty:
            continue
        n += 1
        warned += int(w.max() >= thr)
    flagged = t[t.p >= thr]
    tf = t[t.site.isin(farms)]
    seasons = tf.groupby(["site", tf.date.dt.year]).ngroups
    return {"threshold": thr, "onsets": n, "warned_share": warned / n if n else None,
            "alert_precision": float(flagged[f"onset_in_{h}d"].mean()) if len(flagged) else None,
            "alert_days_per_farm_season": float((tf.p >= thr).sum() / max(seasons, 1))}


def main():
    df, sites = build_onset_table()
    q = df[df.quiet]
    print(f"quiet farm-days: {len(q):,} of {len(df):,}")
    old = pd.read_parquet(config.PROCESSED / "forecast.parquet", columns=["site", "date", "risk_3d", "risk_7d"])
    results = {}
    for h in HORIZONS:
        t = f"onset_in_{h}d"
        d = q.dropna(subset=[t])
        train, valid, test = d[d.date.dt.year <= 2015], d[d.date.dt.year.between(2016, 2018)], d[d.date.dt.year >= 2019]
        pos = train[t].mean()
        params = dict(tb.PARAMS, scale_pos_weight=1.0)
        dtr = lgb.Dataset(train[FEATURES], train[t], categorical_feature=["site_id"])
        dva = lgb.Dataset(valid[FEATURES], valid[t], categorical_feature=["site_id"])
        m = lgb.train(params, dtr, 2000, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])
        p = m.predict(test[FEATURES], num_iteration=m.best_iteration)
        pv = m.predict(valid[FEATURES], num_iteration=m.best_iteration)
        y, yv = test[t].astype(int).values, valid[t].astype(int).values
        p_seas = seasonal(pd.concat([train, valid]), test, t)
        p_seas = np.where(np.isfinite(p_seas), p_seas, pos)
        p_old = test.merge(old, on=["site", "date"], how="left")[f"risk_{h}d"].values
        groups = (test.area + test.date.dt.year.astype(str)).values
        lv = levels_from_validation(yv, pv)
        # old model warning thresholds for a fair comparison: same alert load as the new "watch"
        r = {"horizon_days": h, "n_train": int(len(train)), "n_test": int(len(test)), "onset_rate_test": float(y.mean()),
             "best_iteration": int(m.best_iteration), "levels": lv,
             "new": {"roc_auc": float(roc_auc_score(y, p)), "pr_auc": float(average_precision_score(y, p))},
             "seasonal": {"roc_auc": float(roc_auc_score(y, p_seas)), "pr_auc": float(average_precision_score(y, p_seas))}}
        ok = np.isfinite(p_old)
        r["old_model"] = {"roc_auc": float(roc_auc_score(y[ok], p_old[ok])), "pr_auc": float(average_precision_score(y[ok], p_old[ok])),
                          "rows": int(ok.sum())}
        r["gain_vs_seasonal_ci95"] = boot(y, p, p_seas, groups)
        r["gain_vs_old_ci95"] = boot(y[ok], p[ok], p_old[ok], groups[ok])
        r["warnings"] = {k: warning_stats(test, p, h, v) for k, v in lv.items() if v is not None}
        # old model at the level the app used (medium = 30%) for comparison
        r["warnings_old_medium30"] = warning_stats(test.assign(), np.where(ok, p_old, 0), h, 0.3)
        imp = pd.Series(m.feature_importance("gain"), index=FEATURES)
        imp = imp.groupby(lambda k: LABELS.get(k, k)).sum().sort_values(ascending=False)
        r["importance"] = {k: float(v) for k, v in (imp / imp.sum()).round(4).items()}
        final = lgb.train(params, lgb.Dataset(d[FEATURES], d[t], categorical_feature=["site_id"]), max(m.best_iteration, 50))
        MODELS.mkdir(parents=True, exist_ok=True)
        final.save_model(str(MODELS / f"onset_{h}d.txt"))
        results[f"{h}d"] = r

        print(f"\n== NEW bloom within {h} days · test 2019-2026 · {len(test):,} quiet farm-days · onset rate {y.mean():.1%}")
        print(f"   ROC-AUC  new {r['new']['roc_auc']:.3f} | old model {r['old_model']['roc_auc']:.3f} | season {r['seasonal']['roc_auc']:.3f}")
        print(f"   PR-AUC   new {r['new']['pr_auc']:.3f} | old model {r['old_model']['pr_auc']:.3f} | season {r['seasonal']['pr_auc']:.3f}")
        print(f"   AUC gain vs season {r['gain_vs_seasonal_ci95']}  vs old model {r['gain_vs_old_ci95']}")
        print(f"   levels (from 2016-2018): {lv}")
        for k, w in r["warnings"].items():
            print(f"   {k:6s} >= {w['threshold']:.3f}: warned {w['warned_share']:.0%} of {w['onsets']} onsets in the {h} days before | "
                  f"alerts right {w['alert_precision']:.0%} | {w['alert_days_per_farm_season']:.1f} alert days per farm-season")
        w = r["warnings_old_medium30"]
        print(f"   old model at 30%: warned {w['warned_share']:.0%} | alerts right {w['alert_precision'] or 0:.0%} | "
              f"{w['alert_days_per_farm_season']:.1f} alert days per farm-season")
        print("   top drivers: " + ", ".join(f"{k} {v:.0%}" for k, v in list(r["importance"].items())[:8]))
    (MODELS / "onset_metrics.json").write_text(json.dumps(results, indent=1))
    print(f"\nsaved {MODELS / 'onset_metrics.json'}")


if __name__ == "__main__":
    main()
