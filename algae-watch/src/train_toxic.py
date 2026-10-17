"""Forecast toxic algae (Hafró samples above the shellfish-poisoning threshold) from Copernicus conditions.

Labels:  Hafró monitoring entries 2005-2019 (src/scrape_hafro.py), any_above + per genus.
Features: conditions at the station known LEAD days before the sample date (no peeking):
  sea temperature (OSTIA) + anomaly + 14-day change, salinity and mixed-layer depth (GLORYS),
  chlorophyll (gap-free) + anomaly vs normal, wind, sunlight, air temperature, rain,
  season (day of year), station, and the last Hafró result known at forecast time.
Split by time: train 2005-2014, test 2015-2019. Baselines: seasonal (station x month rate from
training years) and persistence (last known result).
Output: <data>/models/toxic_<target>_<lead>d.txt, <data>/models/toxic_metrics.json
Usage: python src/train_toxic.py [--lead 7]
"""
import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, precision_score, recall_score, roc_auc_score, average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

MODELS = config.DATA / "models"
TARGETS = ["any_above", "alexandrium_above", "dinophysis_above", "pseudo_nitzschia_above"]
TRAIN_END, TEST_START = 2014, 2015


def daily_features(s):
    """Rolling features per station from the daily series (values known at each date)."""
    out = []
    for st, g in s.groupby("site"):
        g = g.set_index("date").sort_index()
        f = pd.DataFrame(index=g.index)
        f["sst"] = g.sst.rolling(7, min_periods=3).mean()
        f["sst_change_14d"] = g.sst - g.sst.shift(14)
        doy = g.index.dayofyear
        base = g[(g.index.year >= config.BASELINE_YEARS[0]) & (g.index.year <= config.BASELINE_YEARS[1])]
        clim = base.sst.groupby(base.index.dayofyear).mean().rolling(31, center=True, min_periods=1).mean()
        f["sst_anomaly"] = f.sst - clim.reindex(doy).values
        f["salinity"] = g.salinity.rolling(7, min_periods=3).mean()
        f["salinity_change_14d"] = g.salinity - g.salinity.shift(14)
        f["mixed_layer"] = g.mixed_layer.rolling(7, min_periods=3).mean()
        f["chl"] = np.log10(g.chl.rolling(7, min_periods=2).mean())
        f["chl_anomaly"] = np.log10(g.chl_anomaly_ratio.rolling(7, min_periods=2).median()) if "chl_anomaly_ratio" in g else np.nan
        f["wind"] = g.wind_speed_10m_mean.rolling(7, min_periods=3).mean()
        f["sunlight"] = g.shortwave_radiation_sum.rolling(7, min_periods=3).sum()
        f["air_temp"] = g.temperature_2m_mean.rolling(7, min_periods=3).mean()
        f["rain"] = g.precipitation_sum.rolling(7, min_periods=3).sum()
        f["site"] = st
        out.append(f.reset_index())
    return pd.concat(out, ignore_index=True)


MAX_KNOWN_AGE = np.timedelta64(60, "D")
STATION_ORDER = sorted(st["name"] for st in config.HAFRO_STATIONS)


def last_known(dates, labels, t):
    """Most recent Hafró result on or before t, if it is at most 60 days old."""
    ok = dates <= t
    if not ok.any():
        return np.nan
    i = np.where(ok)[0][-1]
    return float(labels[i]) if t - dates[i] <= MAX_KNOWN_AGE else np.nan


def build_table(lead):
    tox = pd.read_parquet(config.PROCESSED / "hafro_toxic.parquet")
    s = pd.read_parquet(config.PROCESSED / "site_series.parquet")
    b = config.PROCESSED / "blooms.parquet"
    if b.exists():
        s = s.merge(pd.read_parquet(b)[["site", "date", "chl_anomaly_ratio"]], on=["site", "date"], how="left")
    feats = daily_features(s)
    tox = tox.sort_values(["station", "date"]).copy()
    tox["feature_date"] = tox.date - pd.Timedelta(days=lead)
    rows = tox.merge(feats, left_on=["station", "feature_date"], right_on=["site", "date"],
                     how="left", suffixes=("", "_f")).drop(columns=["date_f", "site"])
    # last Hafró result known at forecast time (a sample taken >= lead days earlier, <= 60 days old)
    prev = []
    for st, g in tox.groupby("station"):
        dates, labels = g.date.values, g.any_above.values
        for d in dates:
            prev.append(last_known(dates, labels, d - np.timedelta64(lead, "D")))
    rows["last_known_above"] = prev
    rows["doy_sin"] = np.sin(2 * np.pi * rows.date.dt.dayofyear / 365.25)
    rows["doy_cos"] = np.cos(2 * np.pi * rows.date.dt.dayofyear / 365.25)
    rows["month"] = rows.date.dt.month
    rows["station_id"] = rows.station.map(STATION_ORDER.index)
    return rows


FEATURES = ["sst", "sst_anomaly", "sst_change_14d", "salinity", "salinity_change_14d", "mixed_layer",
            "chl", "chl_anomaly", "wind", "sunlight", "air_temp", "rain", "doy_sin", "doy_cos",
            "station_id", "last_known_above"]


def scores(y, p, thr):
    yhat = p >= thr
    return {"roc_auc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
            "pr_auc": float(average_precision_score(y, p)) if len(set(y)) > 1 else None,
            "brier": float(brier_score_loss(y, p)),
            "precision": float(precision_score(y, yhat, zero_division=0)),
            "recall": float(recall_score(y, yhat, zero_division=0)),
            "threshold": float(thr)}


def oof_predict(train, ytr, params, base_rate, folds=5):
    """Out-of-fold predictions on the training years (leave-years-out), for picking a threshold."""
    years = np.array(sorted(train.date.dt.year.unique()))
    oof = np.full(len(train), np.nan)
    for chunk in np.array_split(years, folds):
        te = train.date.dt.year.isin(chunk).values
        if ytr[~te].sum() < 5:
            continue
        m = lgb.train(params, lgb.Dataset(train[FEATURES][~te], ytr[~te], categorical_feature=["station_id"]), 300)
        raw = m.predict(train[FEATURES][te])
        odds = raw / (1 - raw) * base_rate / (1 - base_rate)
        oof[te] = odds / (1 + odds)
    return oof


def best_f1_threshold(y, p):
    ok = np.isfinite(p)
    y, p = y[ok], p[ok]
    best = (0, 0.5)
    for t in np.unique(np.round(p, 3)):
        yhat = p >= t
        if yhat.sum() == 0:
            continue
        prec, rec = (y & yhat).sum() / yhat.sum(), (y & yhat).sum() / max(y.sum(), 1)
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
        if f1 > best[0]:
            best = (f1, t)
    return float(best[1])


def bootstrap_auc_diff(y, p_model, p_base, n=2000, seed=0):
    """95% interval of ROC-AUC(model) - ROC-AUC(baseline) by resampling test samples."""
    rng = np.random.default_rng(seed)
    d = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) < 2:
            continue
        d.append(roc_auc_score(y[i], p_model[i]) - roc_auc_score(y[i], p_base[i]))
    return [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def run(table, target, lead):
    t = table.dropna(subset=["sst"])  # need satellite conditions
    train, test = t[t.date.dt.year <= TRAIN_END], t[t.date.dt.year >= TEST_START]
    ytr, yte = train[target].astype(int).values, test[target].astype(int).values
    if ytr.sum() < 10 or yte.sum() < 5:
        return None
    base_rate = ytr.mean()

    # baseline 1: seasonal (station x month rate in training years, smoothed toward the overall rate)
    k = 5
    grp = train.groupby(["station", "month"])[target].agg(["sum", "count"])
    seas = ((grp["sum"] + k * base_rate) / (grp["count"] + k)).rename("p")
    p_seas = test.join(seas, on=["station", "month"]).p.fillna(base_rate).values
    # baseline 2: persistence (last known result)
    p_pers = test.last_known_above.fillna(base_rate).values

    params = dict(objective="binary", learning_rate=0.03, num_leaves=7, min_child_samples=15,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0,
                  scale_pos_weight=(1 - base_rate) / base_rate, verbose=-1, seed=0)
    dtr = lgb.Dataset(train[FEATURES], ytr, categorical_feature=["station_id"])
    model = lgb.train(params, dtr, num_boost_round=300)
    raw = model.predict(test[FEATURES])
    # class weighting inflates probabilities; recalibrate to the training base rate (odds shift)
    odds = raw / (1 - raw) * base_rate / (1 - base_rate)
    p_gbm = odds / (1 + odds)

    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight=None))
    Xtr = train[FEATURES].fillna(train[FEATURES].median())
    Xte = test[FEATURES].fillna(train[FEATURES].median())
    lr.fit(Xtr, ytr)
    p_lr = lr.predict_proba(Xte)[:, 1]

    # alert thresholds picked on TRAINING data only (max F1): out-of-fold for the models
    p_oof = oof_predict(train, ytr, params, base_rate)
    thr_gbm = best_f1_threshold(ytr, p_oof)
    lr_tr = lr.predict_proba(Xtr)[:, 1]
    thr_lr = best_f1_threshold(ytr, lr_tr)
    seas_tr = train.join(seas, on=["station", "month"]).p.fillna(base_rate).values
    thr_seas = best_f1_threshold(ytr, seas_tr)
    best_base = "baseline_seasonal" if roc_auc_score(yte, p_seas) >= roc_auc_score(yte, p_pers) else "baseline_persistence"
    ci = bootstrap_auc_diff(yte, p_gbm, p_seas if best_base == "baseline_seasonal" else p_pers)

    MODELS.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODELS / f"toxic_{target}_{lead}d.txt"))
    imp = pd.Series(model.feature_importance("gain"), index=FEATURES).sort_values(ascending=False)
    return {"target": target, "lead_days": lead, "n_train": int(len(train)), "n_test": int(len(test)),
            "positives_train": int(ytr.sum()), "positives_test": int(yte.sum()),
            "base_rate_train": float(base_rate),
            "lightgbm": scores(yte, p_gbm, thr_gbm), "logistic": scores(yte, p_lr, thr_lr),
            "baseline_seasonal": scores(yte, p_seas, thr_seas), "baseline_persistence": scores(yte, p_pers, 0.5),
            "auc_gain_vs_best_baseline": {"baseline": best_base, "ci95": ci},
            "importance": {k: float(v) for k, v in (imp / imp.sum()).round(4).items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", type=int, default=7, help="forecast lead time in days")
    a = ap.parse_args()
    table = build_table(a.lead)
    have = table.dropna(subset=["sst"])
    print(f"Hafró samples: {len(table)}, with satellite conditions: {len(have)} "
          f"({have.date.min().date() if len(have) else '-'} .. {have.date.max().date() if len(have) else '-'})")
    results = [r for r in (run(table, t, a.lead) for t in TARGETS) if r]
    if not results:
        print("Not enough labelled samples overlapping the downloaded data (needs 2005-2019).")
        return
    f = MODELS / "toxic_metrics.json"
    f.write_text(json.dumps(results, indent=1))
    for r in results:
        print(f"\n== {r['target']}  lead {r['lead_days']} days  train {r['n_train']} ({r['positives_train']} pos)  "
              f"test {r['n_test']} ({r['positives_test']} pos)")
        print(f"   {'method':22s} ROC-AUC  PR-AUC  Brier  precision recall")
        for m in ["lightgbm", "logistic", "baseline_seasonal", "baseline_persistence"]:
            x = r[m]
            print(f"   {m:22s} {x['roc_auc'] or 0:6.3f}  {x['pr_auc'] or 0:6.3f}  {x['brier']:.3f}  "
                  f"{x['precision']:8.2f} {x['recall']:6.2f}")
        g = r["auc_gain_vs_best_baseline"]
        print(f"   LightGBM ROC-AUC gain vs {g['baseline']}: 95% interval [{g['ci95'][0]:+.3f}, {g['ci95'][1]:+.3f}]"
              + ("  -> significantly better" if g["ci95"][0] > 0 else
                 "  -> significantly WORSE" if g["ci95"][1] < 0 else "  -> not significant (too few test samples)"))
        top = list(r["importance"].items())[:5]
        print("   top features: " + ", ".join(f"{k} {v:.0%}" for k, v in top))
    print(f"\nsaved {f}")


if __name__ == "__main__":
    main()
