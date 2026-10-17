"""Download Copernicus Marine data for Iceland, one NetCDF file per stream per year.

Years are clipped to the active profile (config.START_DATE..END_DATE):
  ALGAE_PROFILE=sample python src/download_copernicus.py   # 2023-2024 only, quick test
Usage:
  python src/download_copernicus.py                     # all streams, their full periods
  python src/download_copernicus.py --streams chl_l4,phys --years 2023-2024
  python src/download_copernicus.py --streams chl300    # 300 m boxes around each site
Files already in data/raw/ are never re-downloaded.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import common  # noqa: E402

LONG = (1998, 2025)   # gap-free chlorophyll, satellite SST, physics: long baseline
OLCI = (2016, 2025)   # Sentinel-3 OLCI era: algae groups, 300 m, extras

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
    "sss": ("cmems_obs-mob_glo_phy-sss_my_multi_P1D", ["sos", "sos_error"], False, OLCI),
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


def years_for(period, override):
    lo, hi = max(period[0], config.FIRST_YEAR), min(period[1], config.LAST_YEAR)
    if override:
        a, b = (int(x) for x in override.split("-"))
        lo, hi = max(lo, a), min(hi, b)
    return range(lo, hi + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="iceland")
    ap.add_argument("--streams", default=",".join(STREAMS), help=f"comma list from {list(STREAMS) + ['chl300']}")
    ap.add_argument("--years", default=None, help="limit, e.g. 2023-2024")
    a = ap.parse_args()
    check_login()
    print(f"profile={config.PROFILE}  years={config.FIRST_YEAR}-{config.LAST_YEAR}  -> {config.RAW}", flush=True)
    config.RAW.mkdir(parents=True, exist_ok=True)
    box = config.REGIONS[a.region]

    for name in a.streams.split(","):
        if name == "chl300":
            ds_id, vars_, period = CHL300
            for y in years_for(period, a.years):
                for s in config.SITES:
                    out = common.raw_year(f"chl300_{common.slug(s['name'])}", y, a.region)
                    fetch(out, ds_id, vars_, (s["lat"] - 0.2, s["lat"] + 0.2),
                          (s["lon"] - 0.45, s["lon"] + 0.45), y)
            continue
        ds_id, vars_, depth, period = STREAMS[name]
        for y in years_for(period, a.years):
            fetch(common.raw_year(name, y, a.region), ds_id, vars_, box["lat"], box["lon"], y, depth)
    print("done", flush=True)


if __name__ == "__main__":
    main()
