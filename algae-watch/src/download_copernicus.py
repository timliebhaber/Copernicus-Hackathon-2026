"""Download Copernicus Marine data for Iceland, one NetCDF file per stream per year.

Years are clipped to the active profile (config.START_DATE..END_DATE):
  ALGAE_PROFILE=sample python src/download_copernicus.py   # 2023-2024 only, quick test
Usage:
  python src/download_copernicus.py                     # all streams, their full periods
  python src/download_copernicus.py --streams chl_l4,phys --years 2023-2024
  python src/download_copernicus.py --streams chl300    # 300 m boxes around each site
  python src/download_copernicus.py --years 2026 --refresh-current   # update the running year
Files already in data/raw/ are never re-downloaded, except the running year with --refresh-current.

The running year (config.CURRENT_YEAR) is built from the reprocessed archive up to its end date,
then continued with the near-real-time / analysis product (CONTINUATION), regridded onto the
archive grid, so it reaches close to today.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import common  # noqa: E402

LONG = (1998, config.LAST_YEAR)   # gap-free chlorophyll, satellite SST, physics: long baseline
OLCI = (2016, config.LAST_YEAR)   # Sentinel-3 OLCI era: algae groups, 300 m, extras

# name -> (dataset_id, variables, surface_only, years). IDs found with `copernicusmarine describe`.
STREAMS = {
    "chl_l4": ("cmems_obs-oc_glo_bgc-plankton_my_l4-gapfree-multi-4km_P1D", ["CHL", "CHL_uncertainty"], False, LONG),
    "sst_ostia": ("METOFFICE-GLO-SST-L4-REP-OBS-SST", ["analysed_sst", "analysis_error"], False, LONG),
    "phys": ("cmems_mod_glo_phy_my_0.083deg_P1D-m", ["thetao", "so", "uo", "vo", "mlotst"], True, LONG),
    # observed (cloud-free) chlorophyll before the OLCI era, to confirm older blooms
    "chl_l3_early": ("cmems_obs-oc_glo_bgc-plankton_my_l3-multi-4km_P1D", ["CHL"], False, (1998, 2015)),
    "pft_l3": ("cmems_obs-oc_glo_bgc-plankton_my_l3-multi-4km_P1D", ["CHL", "DIATO", "DINO", "HAPTO"], False, OLCI),
    "rrs_l3": ("cmems_obs-oc_glo_bgc-reflectance_my_l3-multi-4km_P1D", ["RRS443", "RRS555"], False, OLCI),
    "transp": ("cmems_obs-oc_glo_bgc-transp_my_l4-gapfree-multi-4km_P1D", ["KD490", "ZSD"], False, OLCI),
    "bgc": ("cmems_mod_glo_bgc_my_0.25deg_P1D-m", ["no3", "po4", "si", "o2", "nppv", "chl"], True, OLCI),
    "waves": ("cmems_mod_glo_wav_my_0.2deg_PT3H-i", ["VHM0"], False, OLCI),
    "sss": ("cmems_obs-mob_glo_phy-sss_my_multi_P1D", ["sos", "sos_error"], False, (2016, 2024)),
}
# near-real-time continuation for the running year: stream -> datasets merged into one time segment
CONTINUATION = {
    "chl_l4": [("cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D", ["CHL", "CHL_uncertainty"], False)],
    "sst_ostia": [("METOFFICE-GLO-SST-L4-NRT-OBS-SST-V2", ["analysed_sst", "analysis_error"], False)],
    "phys": [("cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m", ["thetao"], True),
             ("cmems_mod_glo_phy-so_anfc_0.083deg_P1D-m", ["so"], True),
             ("cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m", ["uo", "vo"], True),
             ("cmems_mod_glo_phy_anfc_0.083deg_P1D-m", ["mlotst"], False)],
    "waves": [("cmems_mod_glo_wav_anfc_0.083deg_PT3H-i", ["VHM0"], False)],
}
CHL300 = ("cmems_obs-oc_glo_bgc-plankton_my_l3-olci-300m_P1D", ["CHL"], OLCI)


def check_login():
    cred = Path.home() / ".copernicusmarine" / ".copernicusmarine-credentials"
    if not cred.exists():
        sys.exit("No Copernicus Marine credentials found.\n"
                 "Create a free account at https://data.marine.copernicus.eu/register\n"
                 "then run: .venv/bin/copernicusmarine login")


def fetch(out, dataset_id, variables, lat, lon, year, depth=False):
    import copernicusmarine as cm

    if out.exists():
        print(f"[cached] {out.name}", flush=True)
        return out
    print(f"[download] {out.name}: {dataset_id} {variables}", flush=True)
    kwargs = dict(
        dataset_id=dataset_id, variables=variables,
        minimum_latitude=lat[0], maximum_latitude=lat[1],
        minimum_longitude=lon[0], maximum_longitude=lon[1],
        start_datetime=f"{year}-01-01T00:00:00", end_datetime=f"{year}-12-31T23:59:59",
        output_directory=str(out.parent), output_filename=out.stem + ".part.nc",
    )
    if depth:  # surface layer only
        kwargs.update(minimum_depth=0, maximum_depth=1)
    # Time-series chunks (small area, long time) are ~25x faster than daily map chunks for us.
    for service in ("arco-time-series", None):
        try:
            cm.subset(**kwargs, **({"service": service} if service else {}))
            (out.parent / (out.stem + ".part.nc")).rename(out)
            return out
        except Exception as e:  # try the default service, then report and continue
            err = e
    print(f"[failed] {out.name}: {err}", flush=True)
    return None


def subset_to(path, dataset_id, variables, lat, lon, start, end, depth=False):
    import copernicusmarine as cm
    kw = dict(dataset_id=dataset_id, variables=variables, minimum_latitude=lat[0], maximum_latitude=lat[1],
              minimum_longitude=lon[0], maximum_longitude=lon[1], start_datetime=start, end_datetime=end,
              output_directory=str(path.parent), output_filename=path.name, coordinates_selection_method="inside")
    if depth:
        kw.update(minimum_depth=0, maximum_depth=1)
    for service in ("arco-time-series", None):
        try:
            cm.subset(**kw, **({"service": service} if service else {}))
            return path
        except Exception as e:  # noqa: BLE001
            err = e
    raise err


def fetch_current(name, out, lat, lon, year):
    """Running year: archive part + near-real-time continuation, on the archive grid."""
    import pandas as pd
    import xarray as xr
    ds_id, vars_, depth, _ = STREAMS[name]
    tmp = out.parent / f"{out.stem}.my.part.nc"
    print(f"[current] {out.name}: archive {ds_id}", flush=True)
    subset_to(tmp, ds_id, vars_, lat, lon, f"{year}-01-01T00:00:00", f"{year}-12-31T23:59:59", depth)
    base = xr.open_dataset(tmp).load()
    last = pd.Timestamp(base.time.values[-1])
    print(f"          archive ends {last.date()}", flush=True)
    parts = [base]
    today = pd.Timestamp.today().normalize()
    end = min(pd.Timestamp(f"{year}-12-31T23:59:59"), today + pd.Timedelta(hours=23, minutes=59)).strftime("%Y-%m-%dT%H:%M:%S")
    if name in CONTINUATION:
        start = (last + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        segs = []
        for i, (cid, cvars, cdepth) in enumerate(CONTINUATION[name]):
            ctmp = out.parent / f"{out.stem}.nrt{i}.part.nc"
            try:
                subset_to(ctmp, cid, cvars, lat, lon, start, end, cdepth)  # no model forecasts past today
                segs.append(xr.open_dataset(ctmp).load())
            except Exception as e:  # noqa: BLE001
                print(f"          continuation {cid} failed: {str(e)[:120]}", flush=True)
            ctmp.unlink(missing_ok=True)
        if segs:
            nrt = xr.merge(segs, compat="override", join="inner")
            if "depth" in base.dims and "depth" in nrt.dims:
                nrt = nrt.isel(depth=[0]).assign_coords(depth=base.depth.values)
            nrt = nrt.interp(latitude=base.latitude, longitude=base.longitude, method="nearest")
            nrt = nrt[[v for v in base.data_vars if v in nrt.data_vars]]
            nrt = nrt.sel(time=nrt.time > base.time.values[-1])
            if nrt.sizes.get("time", 0):
                print(f"          + continuation {pd.Timestamp(nrt.time.values[0]).date()} .. "
                      f"{pd.Timestamp(nrt.time.values[-1]).date()}", flush=True)
                parts.append(nrt.astype({v: base[v].dtype for v in nrt.data_vars}))
    full = xr.concat(parts, dim="time") if len(parts) > 1 else base
    full.attrs["current_year_note"] = f"archive to {last.date()}, then near-real-time continuation"
    full.to_netcdf(out)
    tmp.unlink(missing_ok=True)
    return out


def years_for(period, override):
    lo, hi = max(period[0], config.FIRST_YEAR), min(period[1], config.LAST_YEAR)
    if override:
        a, b = (int(x) for x in (override.split("-") if "-" in override else (override, override)))
        lo, hi = max(lo, a), min(hi, b)
    return range(lo, hi + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="iceland")
    ap.add_argument("--streams", default=",".join(STREAMS), help=f"comma list from {list(STREAMS) + ['chl300']}")
    ap.add_argument("--years", default=None, help="limit, e.g. 2023-2024 or 2026")
    ap.add_argument("--refresh-current", action="store_true",
                    help=f"re-download the running year ({config.CURRENT_YEAR}) to pick up new days")
    a = ap.parse_args()
    check_login()
    print(f"profile={config.PROFILE}  years={config.FIRST_YEAR}-{config.LAST_YEAR}  -> {config.RAW}", flush=True)
    config.RAW.mkdir(parents=True, exist_ok=True)
    box = config.REGIONS[a.region]

    for name in a.streams.split(","):
        if name == "chl300":
            ds_id, vars_, period = CHL300
            for y in years_for(period, a.years):
                hy, hx = config.CHL300_HALF
                for s in config.CHL300_BOXES:
                    out = common.raw_year(f"chl300_{common.slug(s['name'])}", y, a.region)
                    fetch(out, ds_id, vars_, (s["lat"] - hy, s["lat"] + hy), (s["lon"] - hx, s["lon"] + hx), y)
            continue
        ds_id, vars_, depth, period = STREAMS[name]
        for y in years_for(period, a.years):
            out = common.raw_year(name, y, a.region)
            if y == config.CURRENT_YEAR:
                if out.exists() and not a.refresh_current:
                    print(f"[cached] {out.name} (use --refresh-current to update)", flush=True)
                    continue
                try:
                    fetch_current(name, out, box["lat"], box["lon"], y)
                except Exception as e:  # noqa: BLE001
                    print(f"[failed] {out.name}: {str(e)[:200]}", flush=True)
                continue
            fetch(out, ds_id, vars_, box["lat"], box["lon"], y, depth)
    print("done", flush=True)


if __name__ == "__main__":
    main()
