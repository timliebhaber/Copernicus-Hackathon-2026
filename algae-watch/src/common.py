"""Shared helpers: file names, site slugs, pixels around a site."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

_IS = str.maketrans({"ð": "d", "þ": "th", "æ": "ae", "ö": "o", "á": "a", "é": "e",
                     "í": "i", "ó": "o", "ú": "u", "ý": "y"})


def slug(name):
    return name.lower().translate(_IS).replace(" ", "-")


def tag(region="iceland", start=config.START_DATE, end=config.END_DATE):
    return f"{region}_{start[:4]}_{end[:4]}"


def raw(prefix, t=None):
    return config.RAW / f"{prefix}_{t or tag()}.nc"


def raw_year(prefix, year, region="iceland"):
    return config.RAW / f"{prefix}_{region}_{year}.nc"


def open_stream(prefix, years=None, region="iceland"):
    """Open all yearly files of a stream as one dataset (optionally limited to years=(lo, hi))."""
    import xarray as xr

    files = sorted(config.RAW.glob(f"{prefix}_{region}_[0-9][0-9][0-9][0-9].nc"))
    if years:
        files = [f for f in files if years[0] <= int(f.stem[-4:]) <= years[1]]
    if not files:
        raise FileNotFoundError(f"no yearly files for {prefix}; run src/download_copernicus.py")
    return xr.open_mfdataset(files, combine="by_coords", parallel=False)


def site_mask(ds, site, radius_km=config.SITE_RADIUS_KM, fallback_km=30):
    """Boolean (lat, lon) mask of ocean pixels within radius of a site.

    Fjord sites often have no 4 km / 9 km ocean pixel within 10 km, so fall back
    to the nearest few ocean pixels within `fallback_km`.
    """
    lat = ds.latitude.values[:, None]
    lon = ds.longitude.values[None, :]
    dy = (lat - site["lat"]) * 111.2
    dx = (lon - site["lon"]) * 111.2 * np.cos(np.radians(site["lat"]))
    dist = np.hypot(dx, dy)
    first = ds[list(ds.data_vars)[0]]
    ocean = np.isfinite(first.squeeze()).any("time").values
    m = ocean & (dist <= radius_km)
    if m.sum() == 0:
        d = np.where(ocean, dist, np.inf)
        near = np.argsort(d, axis=None)[:4]
        m = np.zeros_like(ocean)
        m.flat[near] = d.flat[near] <= fallback_km
    return m
