"""Forecast high-biomass blooms 3 and 7 days ahead at each site.

Target:   bloom_in_Hd = a CONFIRMED bloom day (detect_blooms.py) on any of the next H days.
Features: only what is known on the forecast date: real cloud-free observations up to today
          (4 km and 300 m: level, anomaly, bloom seen), gap-filled chlorophyll DELAYED by 5 days
          (the reprocessed gap-free product may fill a day using later observations), bloom-pixel
          fraction and days since the last bloom signal (also delayed), sea temperature + anomaly (OSTIA), salinity, mixed-layer depth, currents (GLORYS),
          waves, wind, sunlight, air temperature, rain, season, site.
Split by time: train 1998-2015, validate 2016-2018 (early stopping), test 2019-2025.
Baselines: persistence (a bloom was seen on a clear day in the past week) and seasonal
(site x month rate). Also scored on NEW blooms: days with no bloom seen in the past week
(the useful early-warning case).
Output: <data>/models/bloom_<H>d.txt, <data>/models/bloom_metrics.json
Usage: python src/train_bloom.py
"""
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

MODELS = config.DATA / "models"
HORIZONS = config.HORIZONS
TRAIN_END, VAL_END = 2015, 2018
SEASON = (3, 10)

OBS_FEATURES = ["obs_chl_log", "obs_age", "obs_anomaly", "obs_bloom_7d", "fjord_chl_log", "fjord_age"]
FEATURES = OBS_FEATURES + ["chl_log", "chl_3d", "chl_7d", "chl_change_3d", "anomaly_log", "anomaly_7d_max",
            "bloom_fraction", "candidate_today", "days_since_candidate", "obs_today",
            "sst", "sst_anomaly", "sst_change_7d", "salinity", "salinity_change_7d",
            "mixed_layer", "mixed_layer_change_7d", "current_speed", "wave_height",
            "wind_7d", "sunlight_7d", "air_temp_7d", "rain_7d", "doy_sin", "doy_cos", "site_id"]

LABELS = {  # readable names for the app
    "chl_log": "chlorophyll today", "chl_3d": "chlorophyll (3-day mean)", "chl_7d": "chlorophyll (7-day mean)",
    "chl_change_3d": "chlorophyll rising (3 days)", "anomaly_log": "chlorophyll vs normal",
    "anomaly_7d_max": "highest chlorophyll vs normal (7 days)", "bloom_fraction": "bloom pixels nearby today",
    "candidate_today": "bloom-like signal today", "days_since_candidate": "days since last bloom signal",
    "obs_today": "clear-sky observation today", "sst": "sea temperature", "sst_anomaly": "sea temperature vs normal",
    "sst_change_7d": "sea temperature change (7 days)", "salinity": "salinity",
    "salinity_change_7d": "salinity change (7 days)", "mixed_layer": "mixed-layer depth",
    "mixed_layer_change_7d": "mixed-layer change (7 days)", "current_speed": "current speed",
    "wave_height": "wave height", "wind_7d": "wind (7-day mean)", "sunlight_7d": "sunlight (7 days)",
    "air_temp_7d": "air temperature (7 days)", "rain_7d": "rain (7 days)", "doy_sin": "season",
    "doy_cos": "season", "site_id": "site",
    "obs_chl_log": "observed chlorophyll (latest clear day)", "obs_age": "days since last clear-sky view",
    "obs_anomaly": "observed chlorophyll vs normal", "obs_bloom_7d": "bloom seen on a clear day (7 days)",
    "fjord_chl_log": "fjord 300 m chlorophyll (latest)", "fjord_age": "days since last 300 m view",
}
CHL_LAG_DEFAULT = 5  # gap-filled chlorophyll used with this delay (see build_features)


def site_doy_anomaly(g, col):
    base = g[g.index.year.isin(range(config.BASELINE_YEARS[0], config.BASELINE_YEARS[1] + 1))]
    clim = base[col].groupby(base.index.dayofyear).mean()
    clim = clim.reindex(range(1, 367)).interpolate(limit_direction="both").rolling(31, center=True, min_periods=1).mean()
    return g[col] - clim.reindex(g.index.dayofyear).values


CHL_FEATURES = ["chl_log", "chl_3d", "chl_7d", "chl_change_3d", "anomaly_log", "anomaly_7d_max",
                "bloom_fraction", "candidate_today", "days_since_candidate", "obs_today"]


def build_features(chl_lag=CHL_LAG_DEFAULT):
    """chl_lag > 0 shifts all chlorophyll-derived features back by that many days. The gap-free
    (reprocessed) product may fill a cloudy day using later observations, so a lag guards
    against peeking at the future."""
    s = pd.read_parquet(config.PROCESSED / "site_series.parquet")
    b = pd.read_parquet(config.PROCESSED / "blooms.parquet")[
        ["site", "date", "chl_clim", "chl_anomaly_ratio", "bloom_fraction", "candidate_bloom", "is_bloom",
         "observed_bloom"]]
    s = s.merge(b, on=["site", "date"], how="left")
    last_obs = s.loc[s.chl.notna(), "date"].max()  # no forecasts issued after the latest satellite day
    sites = sorted(s.site.unique())
    out = []
    for site, g in s.groupby("site"):
        g = g.set_index("date").sort_index()
        f = pd.DataFrame(index=g.index)
        chl = g.chl.where(g.chl > 0)
        f["chl_log"] = np.log10(chl)
        f["chl_3d"] = np.log10(chl.rolling(3, min_periods=1).mean())
        f["chl_7d"] = np.log10(chl.rolling(7, min_periods=2).mean())
        f["chl_change_3d"] = f.chl_log - f.chl_log.shift(3)
        f["anomaly_log"] = np.log10(g.chl_anomaly_ratio.where(g.chl_anomaly_ratio > 0))
        f["anomaly_7d_max"] = f.anomaly_log.rolling(7, min_periods=1).max()
        f["bloom_fraction"] = g.bloom_fraction
        cand = g.candidate_bloom.fillna(False).astype(bool)
        f["candidate_today"] = cand.astype(float)
        last = pd.Series(np.where(cand, np.arange(len(g)), np.nan), index=g.index).ffill()
        f["days_since_candidate"] = np.minimum(np.arange(len(g)) - last.values, 120)
        f["obs_today"] = g.chl_obs.notna().astype(float)
        f["sst"] = g.sst
        f["sst_anomaly"] = site_doy_anomaly(g, "sst")
        f["sst_change_7d"] = g.sst - g.sst.shift(7)
        f["salinity"] = g.salinity
        f["salinity_change_7d"] = g.salinity - g.salinity.shift(7)
        f["mixed_layer"] = g.mixed_layer
        f["mixed_layer_change_7d"] = g.mixed_layer - g.mixed_layer.shift(7)
        f["current_speed"] = g.current_speed
        f["wave_height"] = g.wave_height if "wave_height" in g else np.nan
        f["wind_7d"] = g.wind_speed_10m_mean.rolling(7, min_periods=3).mean()
        f["sunlight_7d"] = g.shortwave_radiation_sum.rolling(7, min_periods=3).sum()
        f["air_temp_7d"] = g.temperature_2m_mean.rolling(7, min_periods=3).mean()
        f["rain_7d"] = g.precipitation_sum.rolling(7, min_periods=3).sum()
        f["doy_sin"] = np.sin(2 * np.pi * g.index.dayofyear / 365.25)
        f["doy_cos"] = np.cos(2 * np.pi * g.index.dayofyear / 365.25)
        f["site_id"] = sites.index(site)
        bloom = g.is_bloom.fillna(False).astype(bool)
        f["is_bloom_today"] = bloom.astype(int)
        for h in HORIZONS:
            # any confirmed bloom day in (t, t+h]; NaN if the window has no chlorophyll data
            fut = pd.concat([bloom.shift(-k) for k in range(1, h + 1)], axis=1)
            has = pd.concat([g.chl.notna().shift(-k) for k in range(1, h + 1)], axis=1).fillna(False).any(axis=1)
            f[f"bloom_in_{h}d"] = np.where(has, fut.fillna(False).any(axis=1).astype(int), np.nan)
        if chl_lag:
            f[CHL_FEATURES] = f[CHL_FEATURES].shift(chl_lag)
        # real (cloud-free) observations up to today: known on the forecast date, no gap-filling
        obs = g.chl_obs.where(g.chl_obs > 0)
        seen = obs.notna()
        idx = pd.Series(np.where(seen, np.arange(len(g)), np.nan), index=g.index).ffill()
        f["obs_age"] = np.minimum(np.arange(len(g)) - idx.values, 30)
        f["obs_chl_log"] = np.log10(obs).ffill(limit=10)
        f["obs_anomaly"] = np.log10(obs / g.chl_clim).ffill(limit=10)
        f["obs_bloom_7d"] = g.observed_bloom.fillna(False).astype(float).rolling(7, min_periods=1).max()
        fj = g.chl_300m.where(g.chl_300m > 0) if "chl_300m" in g else pd.Series(np.nan, index=g.index)
        fidx = pd.Series(np.where(fj.notna(), np.arange(len(g)), np.nan), index=g.index).ffill()
        f["fjord_age"] = np.minimum(np.arange(len(g)) - fidx.values, 30)
        f["fjord_chl_log"] = np.log10(fj).ffill(limit=10)
        f["site"] = site
        f["lat"], f["lon"], f["kind"] = g.lat.iloc[0], g.lon.iloc[0], g.kind.iloc[0]
        out.append(f.reset_index())
    df = pd.concat(out, ignore_index=True)
    df["month"] = df.date.dt.month
    keep = df.month.between(*SEASON) & df.chl_7d.notna() & (df.date <= last_obs)
    return df[keep].reset_index(drop=True), sites


PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=31, min_child_samples=50,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=0)


def fit(train, target, rounds=None, valid=None):
    dtr = lgb.Dataset(train[FEATURES], train[target], categorical_feature=["site_id"])
    if valid is not None:
        dva = lgb.Dataset(valid[FEATURES], valid[target], categorical_feature=["site_id"])
        return lgb.train(PARAMS, dtr, 2000, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])
    return lgb.train(PARAMS, dtr, rounds)


def scores(y, p, thr):
    yhat = p >= thr
    tp = (yhat & (y == 1)).sum()
    return {"roc_auc": float(roc_auc_score(y, p)), "pr_auc": float(average_precision_score(y, p)),
            "brier": float(brier_score_loss(y, np.clip(p, 0, 1))),
            "precision": float(tp / max(yhat.sum(), 1)), "recall": float(tp / max((y == 1).sum(), 1)),
            "threshold": float(thr)}


def bootstrap_ci(y, p1, p2, groups, n=500, seed=0):
    """95% interval of ROC-AUC(p1) - ROC-AUC(p2), resampling whole area-years (days and farms are correlated)."""
    rng = np.random.default_rng(seed)
    ug = np.unique(groups)
    idx = {g: np.where(groups == g)[0] for g in ug}
    d = []
    for _ in range(n):
        i = np.concatenate([idx[g] for g in rng.choice(ug, len(ug))])
        if len(set(y[i])) < 2:
            continue
        d.append(roc_auc_score(y[i], p1[i]) - roc_auc_score(y[i], p2[i]))
    return [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def evaluate(df):
    results = {}
    for h in HORIZONS:
        t = f"bloom_in_{h}d"
        d = df.dropna(subset=[t])
        train = d[d.date.dt.year <= TRAIN_END]
        valid = d[d.date.dt.year.between(TRAIN_END + 1, VAL_END)]
        test = d[d.date.dt.year > VAL_END]
        model = fit(train, t, valid=valid)
        p = model.predict(test[FEATURES], num_iteration=model.best_iteration)
        y = test[t].astype(int).values
        # seasonal baseline from training+validation years
        tv = pd.concat([train, valid])
        rate = tv.groupby(["site", "month"])[t].mean().rename("p")
        p_seas = test.join(rate, on=["site", "month"]).p.fillna(tv[t].mean()).values
        p_pers = test.obs_bloom_7d.fillna(0).values  # "a bloom was seen on a clear day this past week"
        # alert threshold chosen on validation years (max F1)
        pv = model.predict(valid[FEATURES], num_iteration=model.best_iteration)
        yv = valid[t].astype(int).values
        cand = np.quantile(pv, np.linspace(0.5, 0.995, 100))
        f1 = [2 * ((pv >= c) & (yv == 1)).sum() / max((pv >= c).sum() + (yv == 1).sum(), 1) for c in cand]
        thr = float(cand[int(np.argmax(f1))])
        # resample whole area-years: farms in one fjord see nearly the same water, so they are not independent
        groups = (test.site.map(config.AREA_OF).fillna(test.site) + test.date.dt.year.astype(str)).values
        onset = (test.obs_bloom_7d == 0).values  # no bloom seen in the past week -> a NEW bloom
        r = {"horizon_days": h, "n_train": int(len(train)), "n_test": int(len(test)),
             "test_years": f"{VAL_END + 1}-{test.date.dt.year.max()}", "positive_rate_test": float(y.mean()),
             "best_iteration": int(model.best_iteration),
             "model": scores(y, p, thr), "baseline_persistence": scores(y, p_pers, 0.5),
             "baseline_seasonal": scores(y, p_seas, np.quantile(p_seas, 1 - y.mean())),
             "auc_gain_vs_persistence_ci95": bootstrap_ci(y, p, p_pers, groups),
             "auc_gain_vs_seasonal_ci95": bootstrap_ci(y, p, p_seas, groups),
             "onset": {"n": int(onset.sum()), "positive_rate": float(y[onset].mean()),
                       "model": scores(y[onset], p[onset], thr),
                       "baseline_seasonal": scores(y[onset], p_seas[onset], np.quantile(p_seas, 1 - y.mean())),
                       "auc_gain_vs_seasonal_ci95": bootstrap_ci(y[onset], p[onset], p_seas[onset], groups[onset])}}
        imp = pd.Series(model.feature_importance("gain"), index=FEATURES)
        imp = imp.groupby(lambda k: LABELS[k]).sum().sort_values(ascending=False)
        r["importance"] = {k: float(v) for k, v in (imp / imp.sum()).round(4).items()}
        # final model on all years for live forecasts
        final = fit(d, t, rounds=max(model.best_iteration, 50))
        MODELS.mkdir(parents=True, exist_ok=True)
        final.save_model(str(MODELS / f"bloom_{h}d.txt"))
        r["alert_threshold"] = thr
        results[f"{h}d"] = r
    return results


def report(results):
    for k, r in results.items():
        print(f"\n== bloom within {r['horizon_days']} days  test {r['test_years']}  n={r['n_test']}  "
              f"positives {r['positive_rate_test']:.1%}")
        print(f"   {'method':22s} ROC-AUC  PR-AUC  precision recall")
        for m in ["model", "baseline_persistence", "baseline_seasonal"]:
            x = r[m]
            print(f"   {m:22s} {x['roc_auc']:6.3f}  {x['pr_auc']:6.3f}  {x['precision']:8.2f} {x['recall']:6.2f}")
        ci_p, ci_s = r["auc_gain_vs_persistence_ci95"], r["auc_gain_vs_seasonal_ci95"]
        print(f"   AUC gain vs persistence [{ci_p[0]:+.3f}, {ci_p[1]:+.3f}]   vs seasonal [{ci_s[0]:+.3f}, {ci_s[1]:+.3f}]")
        o = r["onset"]
        print(f"   NEW BLOOMS (none seen in past week, n={o['n']}, {o['positive_rate']:.1%} positive): model AUC "
              f"{o['model']['roc_auc']:.3f} PR {o['model']['pr_auc']:.3f} | seasonal AUC {o['baseline_seasonal']['roc_auc']:.3f} "
              f"PR {o['baseline_seasonal']['pr_auc']:.3f} | gain [{o['auc_gain_vs_seasonal_ci95'][0]:+.3f}, "
              f"{o['auc_gain_vs_seasonal_ci95'][1]:+.3f}]")
        print("   top drivers: " + ", ".join(f"{k} {v:.0%}" for k, v in list(r["importance"].items())[:6]))


def main():
    df, sites = build_features()
    print(f"feature rows: {len(df)} ({df.date.min().date()} .. {df.date.max().date()}), sites {len(sites)}")
    results = evaluate(df)
    (MODELS / "bloom_metrics.json").write_text(json.dumps(results, indent=1))
    report(results)
    print(f"\nsaved {MODELS / 'bloom_metrics.json'}")


if __name__ == "__main__":
    main()
