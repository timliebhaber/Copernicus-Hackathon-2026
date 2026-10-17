"""Check the bloom detector against independent evidence.

1. Documented events (validation/documented_events.csv): did we detect them?
2. Hafró toxic-algae samples (2005-2019): how often does a satellite bloom coincide with toxic
   algae above threshold? (Expected: weakly. Toxic species are low-biomass.)
3. Spring-bloom timing vs the literature (Gudmundsson 1998: starts late Mar/early Apr, peaks May).
Output: <data>/processed/validation.json and a printed report.
Usage: python src/validate.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

WINDOW_DAYS = 3


def documented(events, blooms):
    rows = []
    series = pd.read_parquet(config.PROCESSED / "site_series.parquet")
    needed = {"coccolithophore": "rrs555", "high-biomass": "chl"}
    docs = pd.read_csv(config.ROOT / "validation" / "documented_events.csv", parse_dates=["start", "end"])
    for _, d in docs.iterrows():
        areas = d.areas.split(";")  # fjord areas (config.AREA_OF); every farm point in them counts
        sites = [p for p, a in config.AREA_OF.items() if a in areas or p in areas]
        lo, hi = d.start - pd.Timedelta(days=WINDOW_DAYS), d.end + pd.Timedelta(days=WINDOW_DAYS)
        col = needed.get(d.kind, "chl")
        covered = series[(series.site.isin(sites)) & series.date.between(lo, hi)]
        if covered.empty or col not in covered or covered[col].isna().all():
            rows.append({"event": d.event, "result": f"no {col} data yet for this period", "detected_at": []})
            continue
        ev = events[(events.kind == d.kind) & events.site.isin(sites) &
                    (events.end >= lo) & (events.start <= hi)]
        conf = ev[ev.status == "confirmed"]
        result = ("DETECTED (confirmed)" if len(conf) else "detected as candidate only" if len(ev) else "MISSED")
        rows.append({"event": d.event, "kind": d.kind, "result": result,
                     "location_known": d.location_known,
                     "detected_at": sorted(set((conf if len(conf) else ev).site)),
                     "first_detection": str((conf if len(conf) else ev).start.min().date()) if len(ev) else None})
    return rows


def toxic_vs_satellite(blooms):
    f = config.PROCESSED / "hafro_toxic.parquet"
    if not f.exists():
        return None
    tox = pd.read_parquet(f)
    b = blooms.set_index(["site", "date"]).sort_index()
    out = []
    for _, r in tox.iterrows():
        if r.station not in b.index.get_level_values(0):
            continue
        w = b.loc[r.station].loc[r.date - pd.Timedelta(days=WINDOW_DAYS): r.date + pd.Timedelta(days=WINDOW_DAYS)]
        if w.empty or w.chl.isna().all():
            continue
        out.append({"toxic_above": bool(r.any_above), "sat_bloom": bool(w.is_bloom.any()),
                    "chl_ratio": float(w.chl_anomaly_ratio.median())})
    df = pd.DataFrame(out)
    if df.empty:
        return None
    ct = pd.crosstab(df.toxic_above, df.sat_bloom)
    p_bloom_given_toxic = df[df.toxic_above].sat_bloom.mean()
    p_bloom_given_clean = df[~df.toxic_above].sat_bloom.mean()
    return {"n_samples_with_satellite_data": int(len(df)),
            "toxic_above_samples": int(df.toxic_above.sum()),
            "share_toxic_samples_with_satellite_bloom": float(p_bloom_given_toxic),
            "share_clean_samples_with_satellite_bloom": float(p_bloom_given_clean),
            "median_chl_ratio_toxic": float(df[df.toxic_above].chl_ratio.median()),
            "median_chl_ratio_clean": float(df[~df.toxic_above].chl_ratio.median()),
            "crosstab": {f"toxic={k}": {f"sat_bloom={c}": int(v) for c, v in row.items()} for k, row in ct.iterrows()}}


def phenology_vs_literature():
    f = config.PROCESSED / "phenology.parquet"
    if not f.exists():
        return None
    ph = pd.read_parquet(f)
    ph = ph[ph.r2 > 0.3]
    if ph.empty:
        return None
    doy = lambda d: (pd.Timestamp("2001-01-01") + pd.Timedelta(days=float(d) - 1)).strftime("%d %b")  # noqa: E731
    return {"fits": int(len(ph)), "median_start": doy(ph.start_doy.median()), "median_peak": doy(ph.peak_doy.median()),
            "literature": "start late March / early April, peak usually May (Gudmundsson 1998)",
            "by_site": {s: {"start": doy(g.start_doy.median()), "peak": doy(g.peak_doy.median()), "years": int(len(g))}
                        for s, g in ph.groupby("site")}}


def main():
    events = pd.read_parquet(config.PROCESSED / "bloom_events.parquet")
    blooms = pd.read_parquet(config.PROCESSED / "blooms.parquet")
    R = {"documented_events": documented(events, blooms),
         "hafro_toxic_vs_satellite": toxic_vs_satellite(blooms),
         "phenology_vs_literature": phenology_vs_literature()}
    (config.PROCESSED / "validation.json").write_text(json.dumps(R, indent=1, default=str))

    print("== 1. Documented events")
    for r in R["documented_events"]:
        print(f"  {r['result']:28s} {r['event']}  {r.get('detected_at') or ''} {r.get('first_detection') or ''}")
    t = R["hafro_toxic_vs_satellite"]
    print("\n== 2. Hafró toxic samples vs satellite blooms (±3 days)")
    if t:
        print(f"  samples with satellite data: {t['n_samples_with_satellite_data']} "
              f"(toxic above threshold: {t['toxic_above_samples']})")
        print(f"  satellite bloom when toxic above threshold: {t['share_toxic_samples_with_satellite_bloom']:.0%}  |  "
              f"when not: {t['share_clean_samples_with_satellite_bloom']:.0%}")
        print(f"  median chlorophyll vs normal: toxic {t['median_chl_ratio_toxic']:.2f}x  |  "
              f"clean {t['median_chl_ratio_clean']:.2f}x")
    else:
        print("  no overlap between Hafró samples (2005-2019) and the data period")
    p = R["phenology_vs_literature"]
    print("\n== 3. Spring bloom timing")
    if p:
        print(f"  {p['fits']} good fits: median start {p['median_start']}, median peak {p['median_peak']}")
        print(f"  literature: {p['literature']}")


if __name__ == "__main__":
    main()
