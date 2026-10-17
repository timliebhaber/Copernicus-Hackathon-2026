"""Daily bloom-risk forecasts for every site, replayable for any past date.

Walk-forward: forecasts for year Y come from a model trained only on years before Y, so a replay
shows what the system would really have said on that day (out of sample).
- Bloom risk within 3 and 7 days at every site (train_bloom.py features), 2005-2025, with the
  top 3 factors pushing the risk up (LightGBM feature contributions).
- Toxic-algae risk 7 days ahead at the Hafró stations (train_toxic.py features): walk-forward for
  2010-2019, then the model trained on all 2005-2019 samples for later years (experimental).
Output: <data>/processed/forecast.parquet
Usage: python src/predict.py            # build the table
       python src/predict.py --date 2022-09-01   # print the forecast for one day
"""
import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import common  # noqa: E402
import train_bloom as tb  # noqa: E402
import train_toxic as tt  # noqa: E402

OUT = config.PROCESSED / "forecast.parquet"
FIRST_FORECAST_YEAR = 2005
TOXIC_FIRST_YEAR = 2010


def level(p):
    return np.where(p >= config.RISK_HIGH, "high", np.where(p >= config.RISK_LOW, "medium", "low"))


def top_factors(model, X, k=3):
    """Top k features pushing each prediction UP, as JSON strings [{feature, value}]."""
    contrib = model.predict(X, pred_contrib=True)[:, :-1]  # last column is the bias
    names = [tb.LABELS.get(c, c) for c in X.columns]
    contrib[:, list(X.columns).index("site_id")] = 0  # "which fjord" is not an explanation
    out = []
    for row, vals in zip(contrib, X.values):
        order = np.argsort(row)[::-1][:k]
        out.append(json.dumps([{"feature": names[i], "push": round(float(row[i]), 3),
                                "value": None if not np.isfinite(vals[i]) else round(float(vals[i]), 3)}
                               for i in order if row[i] > 0]))
    return out


def bloom_forecasts(metrics):
    df, _ = tb.build_features()
    years = sorted(df.date.dt.year.unique())
    parts = []
    for y in [y for y in years if y >= FIRST_FORECAST_YEAR]:
        cur = df[df.date.dt.year == y].copy()
        past = df[df.date.dt.year < y]
        for h in tb.HORIZONS:
            t = f"bloom_in_{h}d"
            tr = past.dropna(subset=[t])
            m = tb.fit(tr, t, rounds=max(metrics[f"{h}d"]["best_iteration"], 50))
            cur[f"risk_{h}d"] = m.predict(cur[tb.FEATURES])
            cur[f"factors_{h}d"] = top_factors(m, cur[tb.FEATURES])
        cur["model_trained_until"] = y - 1
        parts.append(cur)
        print(f"  bloom forecasts {y}", end="\r", flush=True)
    print()
    out = pd.concat(parts, ignore_index=True)
    keep = ["date", "site", "lat", "lon", "kind", "model_trained_until", "is_bloom_today"]
    for h in tb.HORIZONS:
        out[f"level_{h}d"] = level(out[f"risk_{h}d"])
        keep += [f"risk_{h}d", f"level_{h}d", f"factors_{h}d", f"bloom_in_{h}d"]
    return out[keep]


def toxic_forecasts(lead=7):
    """Daily P(toxic genus above threshold 'lead' days later) at the Hafró stations."""
    table = tt.build_table(lead)                     # training rows (one per Hafró sample)
    s = pd.read_parquet(config.PROCESSED / "site_series.parquet")
    b = pd.read_parquet(config.PROCESSED / "blooms.parquet")[["site", "date", "chl_anomaly_ratio"]]
    feats = tt.daily_features(s.merge(b, on=["site", "date"], how="left"))
    feats = feats[feats.site.isin(tt.STATION_ORDER)].copy()
    tox = pd.read_parquet(config.PROCESSED / "hafro_toxic.parquet").sort_values("date")
    lk = []
    for st, g in feats.groupby("site"):
        h = tox[tox.station == st]
        d, lab = h.date.values, h.any_above.values
        lk.append(pd.Series([tt.last_known(d, lab, x) for x in g.date.values], index=g.index))
    feats["last_known_above"] = pd.concat(lk)
    feats["doy_sin"] = np.sin(2 * np.pi * feats.date.dt.dayofyear / 365.25)
    feats["doy_cos"] = np.cos(2 * np.pi * feats.date.dt.dayofyear / 365.25)
    feats["station_id"] = feats.site.map(tt.STATION_ORDER.index)
    feats = feats[feats.date.dt.month.between(4, 10)]

    params = dict(objective="binary", learning_rate=0.03, num_leaves=7, min_child_samples=15,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=0)

    def fit_predict(train, X):
        y = train.any_above.astype(int).values
        base = y.mean()
        p = dict(params, scale_pos_weight=(1 - base) / base)
        m = lgb.train(p, lgb.Dataset(train[tt.FEATURES], y, categorical_feature=["station_id"]), 300)
        raw = m.predict(X[tt.FEATURES])
        odds = raw / (1 - raw) * base / (1 - base)
        return odds / (1 + odds)

    parts = []
    last_label_year = int(table.date.dt.year.max())
    for y in sorted(feats.date.dt.year.unique()):
        if y < TOXIC_FIRST_YEAR:
            continue
        cur = feats[feats.date.dt.year == y].copy()
        train = table[table.date.dt.year < min(y, last_label_year + 1)].dropna(subset=["sst"])
        cur["toxic_risk_7d"] = fit_predict(train, cur)
        cur["toxic_trained_until"] = min(y - 1, last_label_year)
        parts.append(cur)
    out = pd.concat(parts, ignore_index=True).rename(columns={"site": "site"})
    return out[["date", "site", "toxic_risk_7d", "toxic_trained_until", "last_known_above"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="print the forecast for this date instead of rebuilding")
    a = ap.parse_args()
    if a.date:
        f = pd.read_parquet(OUT)
        d = f[f.date == pd.Timestamp(a.date)]
        if d.empty:
            sys.exit(f"no forecast for {a.date} (season Mar-Oct, {FIRST_FORECAST_YEAR}-{config.LAST_YEAR})")
        for _, r in d.sort_values("risk_7d", ascending=False).iterrows():
            fac = ", ".join(x["feature"] for x in json.loads(r.factors_7d))
            tox = f"  toxic {r.toxic_risk_7d:.0%}" if pd.notna(r.get("toxic_risk_7d")) else ""
            print(f"{r.site:24s} 3d {r.risk_3d:5.1%} ({r.level_3d:6s})  7d {r.risk_7d:5.1%} ({r.level_7d:6s}){tox}  | {fac}")
        return
    metrics = json.loads((tb.MODELS / "bloom_metrics.json").read_text())
    bloom = bloom_forecasts(metrics)
    tox = toxic_forecasts()
    out = bloom.merge(tox, on=["date", "site"], how="left")
    out = common.add_coords(out.drop(columns=["lat", "lon", "kind"]))
    out.to_parquet(OUT, index=False)
    print(f"saved {OUT}: {len(out)} site-days, {out.date.min().date()} .. {out.date.max().date()}")
    print("risk level shares (7d):", out.level_7d.value_counts(normalize=True).round(3).to_dict())


if __name__ == "__main__":
    main()
