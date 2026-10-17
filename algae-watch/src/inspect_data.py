"""Print a sanity summary of the downloaded data (ranges, coverage, date span).

Usage: python src/inspect_data.py [--tag iceland_2023_2024]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402


def describe(name, da):
    v = da.values
    finite = np.isfinite(v)
    q = np.nanpercentile(v, [1, 50, 99]) if finite.any() else [np.nan] * 3
    print(f"  {name:8s} p1={q[0]:8.3f}  median={q[1]:8.3f}  p99={q[2]:8.3f}  valid={finite.mean():6.1%}  units={da.attrs.get('units', '?')}")


def coverage_by_month(da):
    valid = np.isfinite(da).mean(dim=[d for d in da.dims if d != "time"])
    s = valid.to_series()
    return s.groupby(s.index.month).mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=f"iceland_{config.START_DATE[:4]}_{config.END_DATE[:4]}")
    a = ap.parse_args()

    for prefix in ["chl_l4", "pft_l3", "rrs_l3", "phys"]:
        f = config.RAW / f"{prefix}_{a.tag}.nc"
        if not f.exists():
            print(f"missing {f.name}")
            continue
        ds = xr.open_dataset(f)
        t = ds.time.values
        print(f"\n== {f.name}  {str(t[0])[:10]} .. {str(t[-1])[:10]}  ({len(t)} days)  grid={dict(ds.sizes)}")
        for v in ds.data_vars:
            da = ds[v].squeeze()
            describe(v, da)
        if prefix == "pft_l3":
            cov = coverage_by_month(ds["CHL"])
            print("  cloud-free ocean fraction by month (CHL L3; land counts as missing):")
            print("   " + "  ".join(f"{m:>2d}:{c:4.0%}" for m, c in cov.items()))

    w = config.RAW / f"weather_{a.tag}.parquet"
    if w.exists():
        df = pd.read_parquet(w)
        print(f"\n== {w.name}  {df.date.min().date()} .. {df.date.max().date()}  sites={df.site.nunique()}")
        print(df.drop(columns=["date", "site"]).describe().loc[["min", "50%", "max"]].round(2).to_string())


if __name__ == "__main__":
    main()
