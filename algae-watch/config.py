"""Central configuration: regions, dates, sites, thresholds.

Two data profiles (set with the ALGAE_PROFILE environment variable, or ./run_all.sh sample):
  full   : 1998-2025, all streams (~8 GB, data/)
  sample : 2023-2024, enough to test the whole pipeline in ~10 min (~1 GB, data_sample/)
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = os.environ.get("ALGAE_PROFILE", "full")
if PROFILE not in ("full", "sample"):
    raise SystemExit(f"ALGAE_PROFILE must be 'full' or 'sample', not {PROFILE!r}")
DATA = ROOT / ("data" if PROFILE == "full" else "data_sample")
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
MODELS = ROOT / "models"
WEB = ROOT / "web"

# Iceland only: one box around the whole coast (Atlantic 1 km products stop at 66.0N,
# so we use the GLOBAL products, which cover the north coast and the Westfjords).
REGIONS = {
    "iceland": {"lat": (63.0, 67.0), "lon": (-25.0, -13.0)},
}

# Copernicus Marine datasets (chosen via `copernicusmarine describe`, not guessed)
DATASETS = {
    # gap-free daily chlorophyll, 4 km, whole Iceland -> model + climatology
    "chl_l4": ("cmems_obs-oc_glo_bgc-plankton_my_l4-gapfree-multi-4km_P1D", ["CHL"]),
    # daily chlorophyll + phytoplankton groups (diatoms, dinoflagellates, haptophytes...), 4 km, cloudy
    "pft_l3": ("cmems_obs-oc_glo_bgc-plankton_my_l3-multi-4km_P1D", None),
    # daily reflectance, 4 km -> coccolithophore (bright water) detection via RRS555
    "rrs_l3": ("cmems_obs-oc_glo_bgc-reflectance_my_l3-multi-4km_P1D", None),
    # daily OLCI 300 m chlorophyll, only small boxes around sites (resolves fjords)
    "chl_300m": ("cmems_obs-oc_glo_bgc-plankton_my_l3-olci-300m_P1D", ["CHL"]),
}

# Full record. Gap-free chlorophyll, satellite SST and physics: 1998-2025.
# Algae groups, reflectance, 300 m, turbidity, nutrients, waves: 2016-2025 (Sentinel-3 OLCI era).
if PROFILE == "full":
    START_DATE = "1998-01-01"
    END_DATE = "2025-12-31"
    BASELINE_YEARS = (1998, 2022)   # "normal" chlorophyll climatology is computed from these years
    APP_YEARS = (2016, 2025)        # years rendered as map layers in the web app
else:  # quick test: two years; the "normal" level is then only a 2-year median (rough)
    START_DATE = "2023-01-01"
    END_DATE = "2024-12-31"
    BASELINE_YEARS = (2023, 2024)
    APP_YEARS = (2023, 2024)
FIRST_YEAR, LAST_YEAR = int(START_DATE[:4]), int(END_DATE[:4])

HORIZONS = [3, 7]

# APPROXIMATE PLACEHOLDER farm locations (mid-fjord points), not real licence coordinates.
SITES = [
    # salmon farming fjords
    {"name": "Seyðisfjörður", "kind": "salmon", "lat": 65.27, "lon": -13.90},
    {"name": "Reyðarfjörður", "kind": "salmon", "lat": 65.02, "lon": -13.95},
    {"name": "Fáskrúðsfjörður", "kind": "salmon", "lat": 64.93, "lon": -13.85},
    {"name": "Berufjörður", "kind": "salmon", "lat": 64.72, "lon": -14.25},
    {"name": "Arnarfjörður", "kind": "salmon", "lat": 65.75, "lon": -23.45},
    {"name": "Dýrafjörður", "kind": "salmon", "lat": 65.90, "lon": -23.55},
    {"name": "Patreksfjörður", "kind": "salmon", "lat": 65.57, "lon": -24.05},
    # shellfish areas monitored for toxic algae (Hafró/MAST)
    {"name": "Breiðafjörður", "kind": "shellfish", "lat": 65.10, "lon": -22.80},
    {"name": "Hvalfjörður", "kind": "shellfish", "lat": 64.37, "lon": -21.60},
]
SITES_ARE_PLACEHOLDERS = True
SITE_RADIUS_KM = 10

# Bloom detection
BLOOM_RATIO = 2.5          # chl >= ratio * climatology
BLOOM_MIN_CHL = 3.0        # mg/m3 absolute floor
BLOOM_SITE_FRACTION = 0.10 # share of valid pixels in radius
CLIM_WINDOW_DAYS = 15

# Risk levels
RISK_LOW = 0.3
RISK_HIGH = 0.6


def sites_for(region="iceland"):
    return SITES
