"""Blooms NEAR each farm: rings around it and the water upstream of it.

For every farm-day this measures how much of the sea around the farm is in a bloom, in rings
(0-10 km, 10-30 km, 30-60 km), using
  - real cloud-free observations (L3), known on the day, and
  - gap-filled chlorophyll (L4), which train_bloom.py uses with a 5-day delay.
It also measures blooms UPSTREAM: pixels 10-60 km away in the direction the surface current is
coming from (GLORYS current at the farm), since blooms drift with the water.
A pixel is a bloom with the same rule as detect_blooms.py: chl above the pixel's 95th percentile
for the date, >= 2.5x the median and >= 3 mg/m3.
Output: <data>/processed/ring_features.parquet
Usage: python src/build_ring_features.py
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

warnings.simplefilter("ignore", RuntimeWarning)
RINGS = {"r1": (0, 10), "r2": (10, 30), "r3": (30, 60)}
UP_KM = (10, 60)
UP_HALF_ANGLE = 60   # degrees either side of the upstream direction
MIN_VALID = 0.05     # need at least 5% of a ring seen on a clear day to say anything


def yearly(prefix):
    return {int(f.stem[-4:]): f for f in sorted(config.RAW.glob(f"{prefix}_iceland_[0-9][0-9][0-9][0-9].nc"))}


def geometry(lat, lon, p):
    dy = (lat[:, None] - p["lat"]) * 111.2
    dx = (lon[None, :] - p["lon"]) * 111.2 * np.cos(np.radians(p["lat"]))
    dist = np.hypot(dx, dy)
    bearing = (np.degrees(np.arctan2(dx, dy)) + 360) % 360   # 0 = north, 90 = east
    return dist, bearing


def frac(bloom, valid, idx):
    """Share of valid pixels in bloom (NaN when too few valid pixels)."""
    if len(idx) == 0:
        return np.full(bloom.shape[0], np.nan), np.full(bloom.shape[0], np.nan)
    v = valid[:, idx].sum(1)
    share_valid = v / len(idx)
    f = np.where(share_valid >= MIN_VALID, bloom[:, idx].sum(1) / np.maximum(v, 1), np.nan)
    return f, share_valid


def main():
    clim = xr.open_dataset(config.PROCESSED / "chl_clim.nc")
    med = 10 ** clim["log10_chl_clim"].values
    thr_all = np.maximum(np.maximum(10 ** clim["log10_chl_high"].values, config.BLOOM_RATIO * med), config.BLOOM_MIN_CHL)
    lat, lon = clim.latitude.values, clim.longitude.values
    ocean = np.isfinite(med).any(axis=0)
    geo = {p["name"]: geometry(lat, lon, p) for p in config.POINTS}
    rings = {n: {k: np.flatnonzero((ocean & (d >= lo) & (d < hi)).ravel()) for k, (lo, hi) in RINGS.items()}
             for n, (d, _) in geo.items()}
    upidx = {n: np.flatnonzero((ocean & (d >= UP_KM[0]) & (d < UP_KM[1])).ravel()) for n, (d, _) in geo.items()}
    upbear = {n: b.ravel()[upidx[n]] for n, (_, b) in geo.items()}

    s = pd.read_parquet(config.PROCESSED / "site_series.parquet", columns=["site", "date", "current_u", "current_v"])
    cur = s.set_index(["site", "date"]).sort_index()

    obs_files = {**yearly("chl_l3_early"), **yearly("pft_l3")}   # pft_l3 (2016+) wins where both exist
    l4_files = yearly("chl_l4")
    out = []
    for y, f4 in l4_files.items():
        l4 = xr.open_dataset(f4)["CHL"]
        t = pd.to_datetime(l4.time.values).normalize()
        doy = t.dayofyear.values
        thr = thr_all[doy - 1].reshape(len(t), -1)
        a4 = l4.values.reshape(len(t), -1)
        b4, v4 = (a4 >= thr) & np.isfinite(a4), np.isfinite(a4)
        if y in obs_files:
            ob = xr.open_dataset(obs_files[y])["CHL"].reindex(time=l4.time, method="nearest", tolerance="12h")
            ao = ob.values.reshape(len(t), -1)
            assert ao.shape[1] == a4.shape[1], "L3 and L4 grids differ"
        else:
            ao = np.full_like(a4, np.nan)
        bo, vo = (ao >= thr) & np.isfinite(ao), np.isfinite(ao)
        anom = np.log10(ao / med[doy - 1].reshape(len(t), -1))
        for p in config.POINTS:
            n = p["name"]
            cols = {"site": n, "date": t}
            for k, idx in rings[n].items():
                cols[f"obs_bloom_{k}"], cols[f"obs_valid_{k}"] = frac(bo, vo, idx)
                cols[f"l4_bloom_{k}"], _ = frac(b4, v4, idx)
            far = np.concatenate([rings[n]["r2"], rings[n]["r3"]])
            with np.errstate(all="ignore"):
                cols["obs_anom_max_10_60"] = np.nanmax(np.where(vo[:, far], anom[:, far], np.nan), axis=1) if len(far) else np.nan
            # upstream: pixels in the direction the current comes from
            try:
                c = cur.loc[n].reindex(t)
                u, v = c.current_u.values, c.current_v.values
            except KeyError:
                u = v = np.full(len(t), np.nan)
            towards = (np.degrees(np.arctan2(u, v)) + 360) % 360
            upstream = (towards + 180) % 360
            idx = upidx[n]
            if len(idx):
                diff = np.abs(((upbear[n][None, :] - upstream[:, None]) + 180) % 360 - 180)
                sector = diff <= UP_HALF_ANGLE
                bo_i, vo_i, b4_i = bo[:, idx] & sector, vo[:, idx] & sector, b4[:, idx] & sector
                nv = vo_i.sum(1)
                cols["obs_bloom_upstream"] = np.where(nv >= MIN_VALID * sector.sum(1).clip(1), bo_i.sum(1) / np.maximum(nv, 1), np.nan)
                cols["l4_bloom_upstream"] = b4_i.sum(1) / np.maximum(sector.sum(1), 1)
                cols["obs_bloom_upstream"] = np.where(np.isfinite(u), cols["obs_bloom_upstream"], np.nan)
                cols["l4_bloom_upstream"] = np.where(np.isfinite(u), cols["l4_bloom_upstream"], np.nan)
            out.append(pd.DataFrame(cols))
        print(f"  rings {y}", end="\r", flush=True)
    print()
    df = pd.concat(out, ignore_index=True)
    p = config.PROCESSED / "ring_features.parquet"
    df.to_parquet(p, index=False)
    season = df[df.date.dt.month.between(4, 9)]
    print(f"saved {p}: {len(df)} rows")
    print(season[[c for c in df.columns if c.startswith(("obs_bloom", "l4_bloom"))]].describe().loc[["count", "mean"]].round(3).to_string())


if __name__ == "__main__":
    main()
