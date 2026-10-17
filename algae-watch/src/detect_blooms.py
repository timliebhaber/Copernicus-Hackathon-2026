"""Detect algal blooms around Iceland by combining data streams.

1. Climatology: per 4 km pixel and day of year, median log10 chlorophyll (+-15 days) over
   config.BASELINE_YEARS.                                    -> data/processed/chl_clim.nc
2. Bloom pixel: chl above the pixel's BLOOM_PERCENTILE for the date (rare: ~1 in 20 days) AND
   >= BLOOM_RATIO x climatology AND >= BLOOM_MIN_CHL.
   Site-day bloom: >= BLOOM_SITE_FRACTION of valid pixels around the site are bloom pixels.
3. Events, two kinds:
   - high-biomass: runs of bloom days. CONFIRMED only if it lasts >= 3 days, is seen on >= 1 clear
     day by a real observation (4 km L3 or 300 m), and the water is not murky; else CANDIDATE.
     Type (diatoms/haptophytes/dinoflagellates) is the satellite algae-group estimate (2016+).
   - coccolithophore: milky water (555 nm reflectance >= 0.008 and >= 3x normal) on >= 2 clear
     days. Chlorophyll alone misses these (checked on Hafró's Sep 2022 Westfjords bloom).
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
TURBID_SECCHI_M = 5.0      # median Secchi depth below this -> murky water, chlorophyll unreliable
MIN_EVENT_DAYS = 3         # a confirmed bloom lasts at least this long ...
#                            ... and is seen on >= 1 clear day by a real (non gap-filled) observation
COCCO_MIN_RRS555 = 0.008   # sr^-1, milky water (Hafró's Sep 2022 E. huxleyi bloom: 0.013-0.022)
COCCO_RATIO = 3.0          # and >= 3x the site's normal 555 nm reflectance for that month
COCCO_MONTHS = (5, 10)
SPRING_DOY = (60, 196)          # 1 Mar - 15 Jul: window for the spring-bloom Gaussian fit
SPRING_PEAK_MAX_DOY = 181       # spring-bloom peak must be before 30 Jun
GROUPS = ["diatoms", "haptophytes", "dinoflagellates"]


def chl_files(years=None):
    files = sorted(config.RAW.glob("chl_l4_iceland_[0-9][0-9][0-9][0-9].nc"))
    if years:
        files = [f for f in files if years[0] <= int(f.stem[-4:]) <= years[1]]
    return files


# ---------- 1. climatology ----------
def build_climatology():
    if CLIM_FILE.exists():
        c = xr.open_dataset(CLIM_FILE)
        if "log10_chl_high" in c and c.attrs.get("high_percentile") == config.BLOOM_PERCENTILE:
            print(f"[cached] {CLIM_FILE.name}")
            return c
        c.close()
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
    high = np.full((366, len(lat), len(lon)), np.nan, np.float32)
    w = config.CLIM_WINDOW_DAYS
    for d in range(1, 367):
        dist = np.abs(doy - d)
        sel = np.minimum(dist, 366 - dist) <= w
        block = data[sel]
        if np.isfinite(block).any():
            clim[d - 1] = np.nanmedian(block, axis=0)
            high[d - 1] = np.nanpercentile(block, config.BLOOM_PERCENTILE, axis=0)
    out = xr.Dataset({"log10_chl_clim": (("doy", "latitude", "longitude"), clim),
                      "log10_chl_high": (("doy", "latitude", "longitude"), high)},
                     coords={"doy": np.arange(1, 367), "latitude": lat, "longitude": lon},
                     attrs={"baseline_years": str(config.BASELINE_YEARS), "window_days": w,
                            "high_percentile": config.BLOOM_PERCENTILE})
    out.to_netcdf(CLIM_FILE)
    print(f"saved {CLIM_FILE}")
    return out


# ---------- 2. bloom flags per site ----------
def site_bloom_series(clim):
    clim_lin = 10 ** clim["log10_chl_clim"].values
    high_lin = 10 ** clim["log10_chl_high"].values
    rows = []
    masks = None
    for f in chl_files():
        ds = xr.open_dataset(f)
        if masks is None:
            masks = {s["name"]: common.site_mask(ds, s) for s in config.POINTS}
        chl = ds["CHL"].values
        c = clim_lin[ds.time.dt.dayofyear.values - 1]
        thr = np.maximum(high_lin[ds.time.dt.dayofyear.values - 1], config.BLOOM_RATIO * c)
        bloom = (chl >= thr) & (chl >= config.BLOOM_MIN_CHL)
        valid = np.isfinite(chl)
        t = pd.to_datetime(ds.time.values).normalize()
        for name, m in masks.items():
            nvalid = valid[:, m].sum(axis=1)
            rows.append(pd.DataFrame({
                "date": t, "site": name,
                "chl_clim": np.nanmean(c[:, m], axis=1),
                "chl_threshold": np.nanmean(thr[:, m], axis=1),
                "bloom_fraction": np.where(nvalid > 0, bloom[:, m].sum(axis=1) / np.maximum(nvalid, 1), np.nan),
                "valid_fraction": nvalid / m.sum(),
            }))
        ds.close()
        print(f"  flags {f.stem[-4:]}", end="\r", flush=True)
    print()
    return pd.concat(rows, ignore_index=True)


# ---------- 3. events ----------
def group_runs(dates, max_gap):
    """Group sorted dates into runs where consecutive dates are <= max_gap days apart."""
    gap = dates.diff().dt.days.fillna(max_gap + 1) > max_gap
    return gap.cumsum()


def bloom_type(e):
    """Dominant algae group from the satellite algae-group product (an estimate, not an ID)."""
    if e.date.min().year < 2016:
        return "unknown (before 2016)", {}
    obs = e.dropna(subset=GROUPS)
    total = obs[GROUPS].sum(axis=1)
    obs = obs[total > 0]
    if obs.empty:
        return "unknown (cloudy)", {}
    # share of each group among the three (same pixels, so they are comparable)
    shares = {k: float((obs[k] / total[obs.index]).median()) for k in GROUPS}
    best = max(shares, key=shares.get)
    return (best if shares[best] >= 0.5 else "mixed"), shares


def label_events(b, s):
    df = b.merge(s, on=["site", "date"], how="left").sort_values(["site", "date"]).reset_index(drop=True)
    for c in ["chl_obs", "chl_300m", "rrs555", "secchi", "sst"] + GROUPS:  # streams not downloaded yet
        if c not in df:
            df[c] = np.nan
    df["chl_anomaly_ratio"] = df.chl / df.chl_clim
    df["candidate_bloom"] = df.bloom_fraction >= config.BLOOM_SITE_FRACTION

    def confirmed(x):  # a real (not gap-filled) observation also shows the bloom
        return (x >= config.BLOOM_MIN_CHL) & (x >= df.chl_threshold)
    df["observed_bloom"] = confirmed(df.chl_obs) | confirmed(df.chl_300m)

    season = df[df.date.dt.month.between(3, 10)]
    secchi = season.groupby("site").secchi.median()
    turbid = {k: v < TURBID_SECCHI_M for k, v in secchi.items()}

    # coccolithophore days: milky water, 555 nm reflectance far above the site's normal for that
    # month. May-Oct only: E. huxleyi blooms follow the spring diatom bloom, and March/April
    # reflectance at 65N is unreliable (low sun).
    month = df.date.dt.month
    rrs_normal = df.groupby(["site", month]).rrs555.transform("median")
    df["rrs555_ratio"] = df.rrs555 / rrs_normal
    df["cocco_day"] = (month.between(*COCCO_MONTHS) & (df.rrs555 >= COCCO_MIN_RRS555) &
                       (df.rrs555_ratio >= COCCO_RATIO) &
                       ~df.site.map(turbid).fillna(False).astype(bool))

    events, df["is_bloom"], df["is_cocco"] = [], False, False
    # --- high-biomass (chlorophyll) events ---
    for site, g in df[df.candidate_bloom].groupby("site"):
        for _, e in g.groupby(group_runs(g.date, 3)):
            days = int((e.date.max() - e.date.min()).days + 1)
            n_obs = int(e.observed_bloom.sum())
            reasons = []
            if days < MIN_EVENT_DAYS:
                reasons.append(f"short (<{MIN_EVENT_DAYS} days)")
            if n_obs == 0:
                reasons.append("not seen on a clear day")
            if turbid.get(site, False):
                reasons.append("murky water")
            status = "confirmed" if not reasons else "candidate"
            if status == "confirmed":  # every day from start to end is "in a bloom" (short cloudy dips included)
                span = df[(df.site == site) & df.date.between(e.date.min(), e.date.max())].index
                df.loc[span, "is_bloom"] = True
            btype, shares = bloom_type(e)
            events.append({
                "site": site, "kind": "high-biomass", "start": e.date.min(), "end": e.date.max(), "days": days,
                "peak_chl": float(e.chl.max()), "peak_ratio": float(e.chl_anomaly_ratio.max()),
                "peak_date": e.loc[e.chl.idxmax(), "date"], "observed_days": n_obs,
                "mean_bloom_fraction": float(e.bloom_fraction.mean()), "type": btype,
                "status": status, "reason": ", ".join(reasons) or "lasted ≥3 days and seen on a clear day",
                "confidence": "high" if status == "confirmed" else "candidate",
                "sst_mean": float(e.sst.mean()),
                **{f"share_{k}": v for k, v in shares.items()},
            })
    # --- coccolithophore (milky water) events ---
    for site, g in df[df.cocco_day].groupby("site"):
        for _, e in g.groupby(group_runs(g.date, 7)):  # cloudy gaps of up to a week
            n_obs = len(e)
            status = "confirmed" if n_obs >= 2 else "candidate"
            if status == "confirmed":
                span = df[(df.site == site) & df.date.between(e.date.min(), e.date.max())].index
                df.loc[span, "is_cocco"] = True
            events.append({
                "site": site, "kind": "coccolithophore", "start": e.date.min(), "end": e.date.max(),
                "days": int((e.date.max() - e.date.min()).days + 1), "peak_chl": float(e.chl.max()),
                "peak_ratio": float(e.rrs555_ratio.max()),
                "peak_date": e.loc[e.rrs555.idxmax(), "date"], "observed_days": n_obs,
                "mean_bloom_fraction": None, "type": "coccolithophores (milky water)",
                "status": status,
                "reason": "milky water on ≥2 clear days" if status == "confirmed" else "milky water on 1 clear day only",
                "confidence": "high" if status == "confirmed" else "candidate",
                "sst_mean": float(e.sst.mean()),
            })
    return df, pd.DataFrame(events)


# ---------- 4. phenology ----------
def gauss(t, b0, beta, h, tm, sigma):
    return b0 + beta * t + h / (sigma * np.sqrt(2 * np.pi)) * np.exp(-((t - tm) ** 2) / (2 * sigma ** 2))


def fit_phenology(df):
    """Fit on an 8-day running median (as in 8-day composites), reject fits stuck at a bound."""
    out = []
    # spring bloom only: fit Mar-mid Jul, peak must fall Mar-Jun (later peaks are summer blooms)
    lo_b, hi_b = [0, -0.05, 0, SPRING_DOY[0], 6], [np.inf, 0.05, np.inf, SPRING_PEAK_MAX_DOY, 45]
    for (site, year), g in df.groupby([df.site, df.date.dt.year]):
        g = g[g.date.dt.dayofyear.between(*SPRING_DOY)].dropna(subset=["chl"])
        if len(g) < 80:
            continue
        t = g.date.dt.dayofyear.values.astype(float)
        y = pd.Series(g.chl.values).rolling(8, center=True, min_periods=4).median().values
        ok = np.isfinite(y)
        t, y = t[ok], y[ok]
        base = np.nanpercentile(y, 10)
        i = np.argmax(np.where(t <= SPRING_PEAK_MAX_DOY, y, -np.inf))
        p0 = [base, 0, (y[i] - base) * 20 * 2.5, t[i], 20]
        try:
            p, _ = curve_fit(gauss, t, y, p0=p0, maxfev=20000, bounds=(lo_b, hi_b))
        except (RuntimeError, ValueError):
            continue
        b0, beta, h, tm, sigma = p
        if sigma < lo_b[4] * 1.05 or sigma > hi_b[4] * 0.95 or tm < lo_b[3] + 2 or tm > hi_b[3] - 2:
            continue  # stuck at a bound: not a real spring bloom shape
        pred = gauss(t, *p)
        r2 = 1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)
        out.append({"site": site, "year": int(year), "start_doy": tm - 1.96 * sigma, "peak_doy": tm,
                    "end_doy": tm + 1.96 * sigma, "duration_days": 2 * 1.96 * sigma,
                    "amplitude": h / (sigma * np.sqrt(2 * np.pi)), "r2": r2})
    ph = pd.DataFrame(out)
    if ph.empty:
        return ph
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

    keep = ["date", "site", "chl", "chl_clim", "chl_threshold", "chl_anomaly_ratio", "bloom_fraction", "valid_fraction",
            "candidate_bloom", "is_bloom", "observed_bloom", "cocco_day", "is_cocco"]
    common.add_coords(df[keep]).to_parquet(config.PROCESSED / "blooms.parquet", index=False)
    # events have their own "kind" (high-biomass / coccolithophore): the farm type goes in "site_kind"
    common.add_coords(ev, kind_col="site_kind").to_parquet(config.PROCESSED / "bloom_events.parquet", index=False)
    ph = fit_phenology(df)
    common.add_coords(ph).to_parquet(config.PROCESSED / "phenology.parquet", index=False)

    # ---- report ----
    print(f"\ncandidate bloom site-days: {int(df.candidate_bloom.sum())}, in confirmed events: "
          f"{int(df.is_bloom.sum())}, coccolithophore days: {int(df.is_cocco.sum())} "
          f"(of {int(df.chl.notna().sum())} site-days with data)")
    print("\nbloom days per month (all sites, all years):")
    m = df[df.is_bloom].groupby(df.date.dt.month).size()
    print("  " + "  ".join(f"{k}:{v}" for k, v in m.items()))
    print(f"\nevents: {len(ev)}")
    if len(ev):
        print(pd.crosstab([ev.site], [ev.kind, ev.status]).to_string())
        conf = ev[ev.status == "confirmed"]
        print("\nconfirmed high-biomass events by type (2016+):")
        print(conf[(conf.kind == "high-biomass") & (conf.start.dt.year >= 2016)].type.value_counts().to_string())
        print("\nwhy candidates were not confirmed:")
        print(ev[ev.status == "candidate"].reason.value_counts().head(6).to_string())
    good = ph[ph.r2 > 0.3]
    print(f"\nphenology fits with r2>0.3: {len(good)} of {len(ph)}")
    print(good.groupby("site")[["start_doy", "peak_doy", "duration_days"]].median().round(0).to_string())


if __name__ == "__main__":
    main()
