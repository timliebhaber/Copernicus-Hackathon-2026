"""Detect algal blooms around Iceland by combining data streams.

1. Climatology: per 4 km pixel and day of year, median log10 chlorophyll (+-15 days) over
   config.BASELINE_YEARS.                                    -> data/processed/chl_clim.nc
2. Bloom pixel: chl >= BLOOM_RATIO x climatology AND chl >= BLOOM_MIN_CHL.
   Site-day bloom: >= BLOOM_SITE_FRACTION of valid pixels around the site are bloom pixels.
3. Events: consecutive bloom days (gaps <= 2 days), each labelled with
   - type from the algae-group product + 555 nm reflectance (2016+),
   - confidence from real observations (4 km L3 or 300 m) and water clarity.
                                                              -> blooms.parquet, bloom_events.parquet
4. Spring-bloom phenology (Zhai et al. 2012 style): shifted Gaussian fitted to each site's
   chlorophyll per year -> start, peak, duration, amplitude.  -> phenology.parquet
Usage: python src/detect_blooms.py
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from scipy.optimize import curve_fit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import common  # noqa: E402

warnings.simplefilter("ignore", RuntimeWarning)
CLIM_FILE = config.PROCESSED / "chl_clim.nc"
TURBID_SECCHI_M = 5.0      # median Secchi depth below this -> murky water, low confidence
GROUPS = ["diatoms", "haptophytes", "dinoflagellates"]


def chl_files(years=None):
    files = sorted(config.RAW.glob("chl_l4_iceland_[0-9][0-9][0-9][0-9].nc"))
    if years:
        files = [f for f in files if years[0] <= int(f.stem[-4:]) <= years[1]]
    return files


# ---------- 1. climatology ----------
def build_climatology():
    if CLIM_FILE.exists():
        print(f"[cached] {CLIM_FILE.name}")
        return xr.open_dataset(CLIM_FILE)
    files = chl_files(config.BASELINE_YEARS)
    print(f"climatology from {len(files)} years ({files[0].stem[-4:]}-{files[-1].stem[-4:]})")
    arrs, doys = [], []
    for f in files:
        ds = xr.open_dataset(f)
        arrs.append(np.log10(ds["CHL"].values.astype(np.float32)))
        doys.append(ds.time.dt.dayofyear.values)
        lat, lon = ds.latitude.values, ds.longitude.values
        ds.close()
    data, doy = np.concatenate(arrs), np.concatenate(doys)
    clim = np.full((366, len(lat), len(lon)), np.nan, np.float32)
    w = config.CLIM_WINDOW_DAYS
    for d in range(1, 367):
        dist = np.abs(doy - d)
        sel = np.minimum(dist, 366 - dist) <= w
        block = data[sel]
        if np.isfinite(block).any():
            clim[d - 1] = np.nanmedian(block, axis=0)
    out = xr.Dataset({"log10_chl_clim": (("doy", "latitude", "longitude"), clim)},
                     coords={"doy": np.arange(1, 367), "latitude": lat, "longitude": lon},
                     attrs={"baseline_years": str(config.BASELINE_YEARS), "window_days": w})
    out.to_netcdf(CLIM_FILE)
    print(f"saved {CLIM_FILE}")
    return out


# ---------- 2. bloom flags per site ----------
def site_bloom_series(clim):
    clim_lin = 10 ** clim["log10_chl_clim"].values
    rows = []
    masks = None
    for f in chl_files():
        ds = xr.open_dataset(f)
        if masks is None:
            masks = {s["name"]: common.site_mask(ds, s) for s in config.SITES}
        chl = ds["CHL"].values
        c = clim_lin[ds.time.dt.dayofyear.values - 1]
        ratio = chl / c
        bloom = (ratio >= config.BLOOM_RATIO) & (chl >= config.BLOOM_MIN_CHL)
        valid = np.isfinite(chl)
        t = pd.to_datetime(ds.time.values).normalize()
        for name, m in masks.items():
            nvalid = valid[:, m].sum(axis=1)
            rows.append(pd.DataFrame({
                "date": t, "site": name,
                "chl_clim": np.nanmean(c[:, m], axis=1),
                "bloom_fraction": np.where(nvalid > 0, bloom[:, m].sum(axis=1) / np.maximum(nvalid, 1), np.nan),
                "valid_fraction": nvalid / m.sum(),
            }))
        ds.close()
        print(f"  flags {f.stem[-4:]}", end="\r", flush=True)
    print()
    return pd.concat(rows, ignore_index=True)


# ---------- 3. events ----------
def label_events(b, s):
    df = b.merge(s, on=["site", "date"], how="left").sort_values(["site", "date"])
    df["chl_anomaly_ratio"] = df.chl / df.chl_clim
    df["is_bloom"] = df.bloom_fraction >= config.BLOOM_SITE_FRACTION
    # confirmation by a real (non gap-filled) observation, 4 km L3 or 300 m fjord
    def confirmed(x):
        return (x >= config.BLOOM_MIN_CHL) & (x >= config.BLOOM_RATIO * df.chl_clim)
    df["observed_bloom"] = confirmed(df.chl_obs) | confirmed(df.chl_300m)

    season = df[df.date.dt.month.between(3, 10)]
    secchi = season.groupby("site").secchi.median()
    rrs_p95 = season.groupby("site").rrs555.quantile(0.95)

    events = []
    for site, g in df[df.is_bloom].groupby("site"):
        gap = g.date.diff().dt.days.fillna(99) > 3
        for _, e in g.groupby(gap.cumsum()):
            obs = e[e.chl_obs.notna()]
            shares = {k: (obs[k] / obs.chl_obs).mean() for k in GROUPS} if len(obs) else {}
            if e.date.min().year < 2016 or not shares or all(pd.isna(v) for v in shares.values()):
                btype = "unknown (before 2016 / no clear-sky data)" if e.date.min().year < 2016 else "unknown (cloudy)"
            else:
                btype = max(shares, key=lambda k: -1 if pd.isna(shares[k]) else shares[k])
                if e.rrs555.max() >= rrs_p95.get(site, np.inf) and btype == "haptophytes":
                    btype = "coccolithophores (milky water)"
            n_obs = int(e.observed_bloom.sum())
            turbid = secchi.get(site, np.inf) < TURBID_SECCHI_M
            conf = "low (murky water)" if turbid else ("high" if n_obs > 0 else "medium (gap-filled only)")
            events.append({
                "site": site, "start": e.date.min(), "end": e.date.max(),
                "days": int((e.date.max() - e.date.min()).days + 1),
                "peak_chl": float(e.chl.max()), "peak_ratio": float(e.chl_anomaly_ratio.max()),
                "peak_date": e.loc[e.chl.idxmax(), "date"],
                "mean_bloom_fraction": float(e.bloom_fraction.mean()),
                "observed_days": n_obs, "type": btype, "confidence": conf,
                "sst_mean": float(e.sst.mean()), "sst_anomaly_hint": None,
                **{f"share_{k}": (None if pd.isna(v) else float(v)) for k, v in shares.items()},
            })
    return df, pd.DataFrame(events)


# ---------- 4. phenology ----------
def gauss(t, b0, beta, h, tm, sigma):
    return b0 + beta * t + h / (sigma * np.sqrt(2 * np.pi)) * np.exp(-((t - tm) ** 2) / (2 * sigma ** 2))


def fit_phenology(df):
    out = []
    for (site, year), g in df.groupby([df.site, df.date.dt.year]):
        g = g[(g.date.dt.dayofyear >= 60) & (g.date.dt.dayofyear <= 250)].dropna(subset=["chl"])
        if len(g) < 100:
            continue
        t, y = g.date.dt.dayofyear.values.astype(float), g.chl.values
        sm = pd.Series(y).rolling(7, center=True, min_periods=1).mean().values
        i = np.argmax(sm[t <= 210]) if (t <= 210).any() else np.argmax(sm)
        p0 = [np.nanpercentile(y, 10), 0, (sm[i] - np.nanpercentile(y, 10)) * 15 * 2.5, t[i], 15]
        try:
            p, _ = curve_fit(gauss, t, y, p0=p0, maxfev=20000,
                             bounds=([0, -0.05, 0, 60, 3], [np.inf, 0.05, np.inf, 230, 60]))
        except (RuntimeError, ValueError):
            continue
        pred = gauss(t, *p)
        r2 = 1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)
        b0, beta, h, tm, sigma = p
        out.append({"site": site, "year": int(year), "start_doy": tm - 1.96 * sigma, "peak_doy": tm,
                    "end_doy": tm + 1.96 * sigma, "duration_days": 2 * 1.96 * sigma,
                    "amplitude": h / (sigma * np.sqrt(2 * np.pi)), "r2": r2})
    ph = pd.DataFrame(out)
    base = ph[ph.year.between(*config.BASELINE_YEARS)].groupby("site")[["start_doy", "peak_doy", "amplitude"]].median()
    ph = ph.join(base, on="site", rsuffix="_normal")
    ph["start_anomaly_days"] = ph.start_doy - ph.start_doy_normal
    return ph


def main():
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    clim = build_climatology()
    b = site_bloom_series(clim)
    s = pd.read_parquet(config.PROCESSED / "site_series.parquet")
    df, ev = label_events(b, s)

    keep = ["date", "site", "chl", "chl_clim", "chl_anomaly_ratio", "bloom_fraction", "valid_fraction",
            "is_bloom", "observed_bloom"]
    df[keep].to_parquet(config.PROCESSED / "blooms.parquet", index=False)
    ev.to_parquet(config.PROCESSED / "bloom_events.parquet", index=False)
    ph = fit_phenology(df)
    ph.to_parquet(config.PROCESSED / "phenology.parquet", index=False)

    # ---- report ----
    print(f"\nbloom site-days: {int(df.is_bloom.sum())} of {int(df.chl.notna().sum())} with data")
    print("\nbloom days per month (all sites, all years):")
    m = df[df.is_bloom].groupby(df.date.dt.month).size()
    print("  " + "  ".join(f"{k}:{v}" for k, v in m.items()))
    print(f"\nevents: {len(ev)}")
    if len(ev):
        print(ev.groupby("site").agg(events=("start", "size"), mean_days=("days", "mean"),
                                     max_peak=("peak_chl", "max")).round(1).to_string())
        print("\nby type (2016+):")
        print(ev[ev.start.dt.year >= 2016].type.value_counts().to_string())
        print("\nby confidence:")
        print(ev.confidence.value_counts().to_string())
    good = ph[ph.r2 > 0.3]
    print(f"\nphenology fits with r2>0.3: {len(good)} of {len(ph)}")
    print(good.groupby("site")[["start_doy", "peak_doy", "duration_days"]].median().round(0).to_string())


if __name__ == "__main__":
    main()
