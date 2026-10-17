"""Render daily gridded layers to transparent PNG overlays for the Leaflet map.

Reads the yearly files for config.APP_YEARS. Rows are resampled to Web Mercator spacing
so the image lines up with the basemap.
Output: data/processed/tiles/<layer>/<YYYY-MM-DD>.png and tiles/manifest.json
Usage: python src/make_tiles.py [--layers chl,anomaly,bloom,chl_obs,sst,rrs555,chl300]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap, LogNorm, Normalize
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import common  # noqa: E402

TILES = config.PROCESSED / "tiles"
# diverging: blue (below normal) -> neutral gray -> red (above normal)
DIVERGING = LinearSegmentedColormap.from_list("anom", ["#184f95", "#86b6ef", "#f0efec", "#ec835a", "#a32b2b"])
CLIM = None


def clim_for(doys):
    global CLIM
    if CLIM is None:
        CLIM = 10 ** xr.open_dataset(config.PROCESSED / "chl_clim.nc")["log10_chl_clim"].values
    return CLIM[doys - 1]


def chl(ds):
    return ds["CHL"].values


def anomaly(ds):
    return ds["CHL"].values / clim_for(ds.time.dt.dayofyear.values)


def bloom(ds):
    c = ds["CHL"].values
    r = c / clim_for(ds.time.dt.dayofyear.values)
    out = np.where(np.isfinite(c), np.nan, np.nan)
    hit = (r >= config.BLOOM_RATIO) & (c >= config.BLOOM_MIN_CHL)
    out[hit] = r[hit]
    return out, np.isfinite(c).any(axis=(1, 2))  # also report which days had data


LAYERS = {
    "chl": dict(file="chl_l4", get=chl, cmap="Greens", norm=LogNorm(0.1, 20), units="mg/m³",
                label="Chlorophyll-a (gap-free, 4 km)", ticks=[0.1, 0.3, 1, 3, 10, 20]),
    "anomaly": dict(file="chl_l4", get=anomaly, cmap=DIVERGING, norm=LogNorm(0.25, 4), units="× normal",
                    label="Chlorophyll vs normal for the date", ticks=[0.25, 0.5, 1, 2, 4]),
    "bloom": dict(file="chl_l4", get=bloom, cmap="Reds", norm=LogNorm(config.BLOOM_RATIO, 10), units="× normal",
                  label=f"Detected bloom pixels (≥{config.BLOOM_RATIO}× normal and ≥{config.BLOOM_MIN_CHL:g} mg/m³)",
                  ticks=[2.5, 4, 6, 10]),
    "chl_obs": dict(file="pft_l3", get=chl, cmap="Greens", norm=LogNorm(0.1, 20), units="mg/m³",
                    label="Chlorophyll-a (observed only, 4 km)", ticks=[0.1, 0.3, 1, 3, 10, 20]),
    "sst": dict(file="sst_ostia", get=lambda ds: ds["analysed_sst"].values - 273.15, cmap="Oranges",
                norm=Normalize(0, 14), units="°C", label="Sea surface temperature (OSTIA satellite)",
                ticks=[0, 2, 4, 6, 8, 10, 12, 14]),
    "rrs555": dict(file="rrs_l3", get=lambda ds: ds["RRS555"].values, cmap="Blues", norm=Normalize(0, 0.012),
                   units="sr⁻¹", label="Reflectance 555 nm (milky water / coccolithophores)",
                   ticks=[0, 0.003, 0.006, 0.009, 0.012]),
}
CHL300 = dict(cmap="Greens", norm=LogNorm(0.1, 20), units="mg/m³",
              label="Chlorophyll-a (300 m, fjords, observed only)", ticks=[0.1, 0.3, 1, 3, 10, 20])


def merc_y(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def mercator_rows(lats, n_out):
    """Index into `lats` (ascending) for each output row, top (north) first."""
    y = np.linspace(merc_y(lats.max()), merc_y(lats.min()), n_out)
    target = np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)
    return np.abs(lats[None, :] - target[:, None]).argmin(axis=1)


def render(arr, cmap, norm):
    cm = colormaps[cmap] if isinstance(cmap, str) else cmap
    rgba = cm(norm(np.clip(arr, norm.vmin, norm.vmax)))
    rgba[..., 3] = np.where(np.isfinite(arr), 0.85, 0.0)
    return Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")


def bounds(ds):
    la, lo = ds.latitude.values, ds.longitude.values
    hy, hx = abs(la[1] - la[0]) / 2, abs(lo[1] - lo[0]) / 2
    return [[float(la.min() - hy), float(lo.min() - hx)], [float(la.max() + hy), float(lo.max() + hx)]]


def yearly(prefix):
    lo, hi = config.APP_YEARS
    return [f for f in sorted(config.RAW.glob(f"{prefix}_iceland_[0-9][0-9][0-9][0-9].nc"))
            if lo <= int(f.stem[-4:]) <= hi]


def write_days(ds, values, outdir, cmap, norm, has_data=None):
    """Write one PNG per day; days with no data are skipped. Returns dates written."""
    outdir.mkdir(parents=True, exist_ok=True)
    flip = ds.latitude.values[0] > ds.latitude.values[-1]
    lats = ds.latitude.values[::-1] if flip else ds.latitude.values
    rows = mercator_rows(lats, len(lats))
    dates = []
    for i, t in enumerate(pd.to_datetime(ds.time.values)):
        arr = values[i][::-1] if flip else values[i]
        ok = has_data[i] if has_data is not None else np.isfinite(arr).any()
        if not ok:
            continue
        d = t.strftime("%Y-%m-%d")
        f = outdir / f"{d}.png"
        if not f.exists():
            render(arr[rows, :], cmap, norm).save(f, optimize=True)
        dates.append(d)
    return dates


def legend(spec):
    n = spec["norm"]
    cm = colormaps[spec["cmap"]] if isinstance(spec["cmap"], str) else spec["cmap"]
    stops = np.linspace(0, 1, 9)
    vals = n.inverse(stops) if isinstance(n, LogNorm) else n.vmin + stops * (n.vmax - n.vmin)
    cols = ["#%02x%02x%02x" % tuple(int(c * 255) for c in cm(s)[:3]) for s in stops]
    return {"label": spec["label"], "units": spec["units"], "ticks": spec["ticks"],
            "log": isinstance(n, LogNorm), "min": float(n.vmin), "max": float(n.vmax),
            "stops": [{"v": float(v), "c": c} for v, c in zip(vals, cols)]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layers", default="chl,anomaly,bloom,chl_obs,sst,rrs555,chl300")
    a = ap.parse_args()
    wanted = a.layers.split(",")
    mf = TILES / "manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {"layers": {}}
    manifest["years"] = list(config.APP_YEARS)

    for key, spec in LAYERS.items():
        if key not in wanted:
            continue
        dates, bnds = [], None
        for f in yearly(spec["file"]):
            ds = xr.open_dataset(f)
            bnds = bnds or bounds(ds)
            v = spec["get"](ds)
            has = None
            if isinstance(v, tuple):
                v, has = v
            dates += write_days(ds, v.squeeze() if v.ndim == 4 else v, TILES / key, spec["cmap"], spec["norm"], has)
            ds.close()
        manifest["layers"][key] = {**legend(spec), "bounds": bnds, "dates": dates, "kind": "region"}
        print(f"{key}: {len(dates)} days", flush=True)

    if "chl300" in wanted:
        sites = {}
        for s in config.SITES:
            sl = common.slug(s["name"])
            dates, bnds = [], None
            for f in yearly(f"chl300_{sl}"):
                ds = xr.open_dataset(f)
                bnds = bnds or bounds(ds)
                dates += write_days(ds, ds["CHL"].values, TILES / "chl300" / sl, CHL300["cmap"], CHL300["norm"])
                ds.close()
            if dates:
                sites[sl] = {"bounds": bnds, "dates": dates}
            print(f"chl300/{sl}: {len(dates)} days", flush=True)
        manifest["layers"]["chl300"] = {**legend(CHL300), "sites": sites, "kind": "sites"}

    mf.write_text(json.dumps(manifest))
    print(f"saved {mf}")


if __name__ == "__main__":
    main()
