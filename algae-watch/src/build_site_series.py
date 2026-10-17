"""Daily time series per site: mean of ocean pixels around each site, for every data stream.

Loops over the yearly files so memory stays small.
Output: <data>/processed/site_series.parquet (one row per site and date).
Usage: python src/build_site_series.py
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import common  # noqa: E402

warnings.simplefilter("ignore", RuntimeWarning)

# stream -> (radius_km, {variable: output column}, valid-fraction column or None)
STREAMS = {
    "chl_l4": (10, {"CHL": "chl", "CHL_uncertainty": "chl_unc"}, None),
    "pft_l3": (10, {"CHL": "chl_obs", "DIATO": "diatoms", "DINO": "dinoflagellates", "HAPTO": "haptophytes"}, "chl_obs_valid"),
    "rrs_l3": (10, {"RRS555": "rrs555", "RRS443": "rrs443"}, None),
    "transp": (10, {"ZSD": "secchi", "KD490": "kd490"}, None),
    "sst_ostia": (10, {"analysed_sst": "sst", "analysis_error": "sst_err"}, None),
    "phys": (10, {"thetao": "sst_model", "so": "salinity", "uo": "current_u", "vo": "current_v",
                  "mlotst": "mixed_layer"}, None),
    "bgc": (25, {"no3": "nitrate", "si": "silicate", "po4": "phosphate", "o2": "oxygen", "nppv": "npp_model"}, None),
    "waves": (15, {"VHM0": "wave_height"}, None),
    "sss": (20, {"sos": "sss_obs"}, None),
}


def yearly_files(prefix):
    return sorted(config.RAW.glob(f"{prefix}_iceland_[0-9][0-9][0-9][0-9].nc"))


def stream_site_means(prefix, radius, varmap, valid_col):
    """DataFrame indexed by (site, date) with the stream's columns."""
    files = yearly_files(prefix)
    if not files:
        print(f"  (no files for {prefix}, skipped)")
        return None
    masks = None
    frames = []
    for f in files:
        ds = xr.open_dataset(f)
        if masks is None:
            masks = {s["name"]: common.site_mask(ds, s, radius_km=radius) for s in config.SITES}
        t = pd.to_datetime(ds.time.values)
        for name, m in masks.items():
            cols = {}
            for var, col in varmap.items():
                v = ds[var].squeeze().values[:, m]
                cols[col] = np.nanmean(v, axis=1)
                if valid_col and col == list(varmap.values())[0]:
                    cols[valid_col] = np.isfinite(v).mean(axis=1)
            df = pd.DataFrame(cols, index=t)
            if prefix == "waves":  # 3-hourly -> daily mean and max
                g = df.resample("1D")
                df = g.mean().join(g.max().add_suffix("_max"))
            df.index = df.index.normalize()
            df["site"] = name
            frames.append(df)
        ds.close()
    out = pd.concat(frames)
    out.index.name = "date"
    print(f"  {prefix}: {len(files)} years, pixels/site {[int(m.sum()) for m in masks.values()]}")
    return out.reset_index().set_index(["site", "date"])


def chl300_means():
    frames = []
    for s in config.SITES:
        files = sorted(config.RAW.glob(f"chl300_{common.slug(s['name'])}_iceland_[0-9][0-9][0-9][0-9].nc"))
        for f in files:
            ds = xr.open_dataset(f)
            m = common.site_mask(ds, s, radius_km=5)
            v = ds["CHL"].squeeze().values[:, m]
            df = pd.DataFrame({"chl_300m": np.nanmean(v, axis=1), "chl_300m_valid": np.isfinite(v).mean(axis=1)},
                              index=pd.to_datetime(ds.time.values).normalize())
            df["site"] = s["name"]
            frames.append(df)
            ds.close()
    if not frames:
        return None
    out = pd.concat(frames)
    out.index.name = "date"
    print(f"  chl300: {len(frames)} site-years")
    return out.reset_index().set_index(["site", "date"])


def main():
    dates = pd.date_range(config.START_DATE, config.END_DATE, freq="D")
    idx = pd.MultiIndex.from_product([[s["name"] for s in config.SITES], dates], names=["site", "date"])
    out = pd.DataFrame(index=idx)
    for prefix, (radius, varmap, valid_col) in STREAMS.items():
        df = stream_site_means(prefix, radius, varmap, valid_col)
        if df is not None:
            out = out.join(df[~df.index.duplicated()], how="left")
    c = chl300_means()
    if c is not None:
        out = out.join(c[~c.index.duplicated()], how="left")

    out = out.reset_index()
    out["sst"] = out["sst"] - 273.15  # OSTIA is in Kelvin
    out["current_speed"] = np.hypot(out.current_u, out.current_v)
    w = pd.read_parquet(config.RAW / f"weather_iceland_{config.START_DATE[:4]}_{config.END_DATE[:4]}.parquet")
    out = out.merge(w, on=["date", "site"], how="left")

    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    p = config.PROCESSED / "site_series.parquet"
    out.to_parquet(p, index=False)
    print(f"saved {p} rows={len(out)} cols={len(out.columns)}")


if __name__ == "__main__":
    main()
