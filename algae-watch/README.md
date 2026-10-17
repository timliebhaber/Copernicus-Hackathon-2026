# AlgaeWatch Iceland

An early-warning prototype for **algal blooms around Iceland**. It is built on Copernicus satellite and ocean-model data and aimed at salmon farms, shellfish growers and authorities (MAST, Hafró).

The web app shows:
- **Data:** daily maps and per-fjord time series of chlorophyll, algae groups, sea temperature, mixed-layer depth, waves and weather.
- **Blooms:** detected bloom events from 1998 to 2025, each with an algae type and a confidence level, plus the spring-bloom timing for each year.
- **Model / Forecast:** coming next. This will be a 3–7 day bloom-risk forecast per site.

> Prototype. The app detects **high-biomass blooms**, using satellite chlorophyll as the signal; it cannot measure toxicity. Toxic species (*Dinophysis*, *Alexandrium*, *Pseudo-nitzschia*) are only detected by Hafró/MAST water sampling. Do not use it for operational decisions. Site markers are approximate placeholder points (mid-fjord), not licensed farm coordinates.

## Which blooms matter in Iceland

| Bloom | Harmful? | Seen by satellite? |
|---|---|---|
| Spring diatom bloom (Apr–Jun, every year) | Usually not; it is the natural annual bloom | Yes: chlorophyll and the diatom fraction |
| Coccolithophores, *Emiliania huxleyi* (S/SW offshore every summer; Westfjords Sept 2022) | Not toxic, according to Hafró | Yes: milky water shows as high 555 nm reflectance |
| Fish-killing blooms near salmon pens (e.g. 2021 losses reported by MAST; Seyðisfjörður) | **Yes** | Partly: chlorophyll spike and the haptophyte fraction |
| Shellfish-toxin species (DSP/PSP/ASP) | **Yes**, for anyone eating the shellfish | **No**: too few cells to show up; only water sampling detects them |

## What is detected, and how
| Bloom type | Signal | When it counts as **confirmed** |
|---|---|---|
| **Spring bloom** (natural, yearly) | Shifted Gaussian fitted to Mar–Jul chlorophyll (Zhai et al. 2012) | Fit r² > 0.3 |
| **High-biomass bloom** (where fish-killing blooms would show) | Chlorophyll **above the 95th percentile** for that pixel and date (1998–2022, ±15 days), **≥ 2.5× the median and ≥ 3 mg/m³**, on ≥ 10% of the sea within 10 km. A fixed "2.5× normal" rule alone fired on 17.5% of summer days, because coastal chlorophyll varies about 2.4× from day to day. | Lasts **≥ 3 days**, is seen on **≥ 1 clear day** by a real (not gap-filled) observation, and the water is not murky (Secchi depth ≥ 5 m). Otherwise it is a *candidate*. |
| **Coccolithophore bloom** (milky water, *E. huxleyi*) | 555 nm reflectance **≥ 0.008 sr⁻¹ and ≥ 3×** the site's normal for that month, May–Oct | Seen on **≥ 2 clear days** |
| **Toxic algae** (*Dinophysis*, *Alexandrium*, *Pseudo-nitzschia*) | Not visible from space | A **7-day risk forecast** trained on Hafró samples (see below) |

The algae type (diatoms, haptophytes, dinoflagellates) comes from Copernicus' satellite algae-group product. It is an estimate, not an identification.

## Validation (`src/validate.py`)
Current result: confirmed blooms cover 3.8% of Apr–Sep days, about 1.6 per site per year.
- **Documented events** (`validation/documented_events.csv`): the Sep 2022 Westfjords *E. huxleyi* bloom, which Hafró sampled at 4.7–6.3 million cells/L, shows up clearly as milky water (555 nm reflectance 0.013–0.022 against a normal of ~0.002) from 3 Sep onward. Chlorophyll alone misses it, which is why it gets its own detector.
- **Detector check:** the coccolithophore detector flags Arnarfjörður from 3 Sep 2022 as a **confirmed** milky-water bloom. Spring 2021 (salmon losses, fjords not named) shows only as a candidate.
- **Hafró toxic samples vs satellite blooms:** a satellite bloom appeared near 10% of toxic samples and 9% of clean ones. Toxic algae and chlorophyll blooms are different things, so a separate forecast is needed.
- **Spring-bloom timing vs literature:** median peak around the end of May across 82 site-years. Gudmundsson (1998) found the bloom usually peaks in May.

## Toxic-algae forecast (`src/scrape_hafro.py`, `src/train_toxic.py`)
- **Labels:** 917 Hafró monitoring samples from 7 stations (2005–2019). The scraper parses Hafró's Icelandic text to find which genus was above the shellfish-poisoning threshold.
- **Inputs:** conditions at the station **7 days before** each sample: sea temperature and its anomaly, salinity, mixed layer, chlorophyll and its anomaly, wind, sunlight, rain, season, and the last known result.
- **Test:** trained on 2005–2014 and tested on 2015–2019, against seasonal and persistence baselines, with a bootstrap 95% interval.
- **First result (any genus above threshold):** ROC-AUC 0.76, against 0.72 for the seasonal baseline and 0.65 for persistence. The gain is **not yet statistically significant** (interval −0.03 to +0.12). For *Dinophysis*, the model is worse than the seasonal baseline.

## Data (Iceland box: 63–67°N, 25–13°W)
| Stream | Copernicus dataset ID | Resolution | Years |
|---|---|---|---|
| Chlorophyll, gap-free, + uncertainty | `cmems_obs-oc_glo_bgc-plankton_my_l4-gapfree-multi-4km_P1D` | 4 km, daily | 1998–2025 |
| Chlorophyll + algae groups (observed) | `cmems_obs-oc_glo_bgc-plankton_my_l3-multi-4km_P1D` | 4 km | 2016–2025 |
| Chlorophyll, fjord detail | `cmems_obs-oc_glo_bgc-plankton_my_l3-olci-300m_P1D` | 300 m | 2016–2025 |
| Reflectance (443, 555 nm) | `cmems_obs-oc_glo_bgc-reflectance_my_l3-multi-4km_P1D` | 4 km | 2016–2025 |
| Turbidity (Secchi, KD490) | `cmems_obs-oc_glo_bgc-transp_my_l4-gapfree-multi-4km_P1D` | 4 km | 2016–2025 |
| Sea surface temperature (satellite) | `METOFFICE-GLO-SST-L4-REP-OBS-SST` (OSTIA) | 0.05° | 1998–2025 |
| Temperature, salinity, currents, mixed layer | `cmems_mod_glo_phy_my_0.083deg_P1D-m` (GLORYS) | 1/12° | 1998–2025 |
| Nutrients, oxygen, primary production (model) | `cmems_mod_glo_bgc_my_0.25deg_P1D-m` | 0.25° | 2016–2025 |
| Waves | `cmems_mod_glo_wav_my_0.2deg_PT3H-i` | 0.2°, 3-hourly | 2016–2025 |
| Surface salinity (observations) | `cmems_obs-mob_glo_phy-sss_my_multi_P1D` | 0.125° | 2016–2025 |
| Weather (wind, sunlight, air temperature, rain) | Open-Meteo archive (ERA5) | ~25 km | 1998–2025 |

The Atlantic 1 km ocean-colour products stop at 66.0°N, which would cut off the north coast and the Westfjords, so this project uses the global products. Note that the ocean-colour dataset version `202603` is scheduled to be retired on 2026-11-24.

### How precise the data is (measured over Iceland, Mar–Oct; `src/data_quality.py`)
- **Chlorophyll:** the product's uncertainty field gives a typical value of **±42%**. A Hafró study found satellite chlorophyll explained only ~20% of the variation in Icelandic water samples. For this reason, blooms are detected relative to each pixel's own normal level, never by an absolute threshold alone.
- **Gap-filled chlorophyll** differs from the next day's real observation by about ×1.56; two real observations a day apart differ by ×1.80. Gap-filling adds no extra noise but smooths out short peaks.
- **300 m chlorophyll** reads ×1.28 higher than 4 km inside the fjords (r = 0.76). Clouds hide each site on 50–80% of days.
- **SST:** the OSTIA error is ±0.5 °C. The GLORYS model differs from OSTIA by RMSE 1.2 °C.
- **Salinity:** coastal satellite salinity is unreliable (±0.9). The BGC model's chlorophyll has no day-to-day skill (r ≈ 0), so its nutrients are used only as seasonal context.
- **Murky water:** Hvalfjörður (Secchi depth 3.2 m) is murky water, so its chlorophyll readings have low confidence.

## Setup
```bash
uv venv --python 3.12 .venv && uv pip install -p .venv -r requirements.txt
.venv/bin/copernicusmarine login          # free account: https://data.marine.copernicus.eu/register
```

## Quick test: a small sample of the data (~10 min, ~1 GB)
```bash
./run_all.sh sample                                   # 2023–2024 only, written to data_sample/
ALGAE_PROFILE=sample .venv/bin/uvicorn api.main:app   # open http://localhost:8000
```
With only two years, the "normal" chlorophyll level is a rough 2-year median, so bloom detection is less reliable than with the full record. Everything else works the same.

## Full data (1998–2025, ~8 GB)
```bash
./run_all.sh full
.venv/bin/uvicorn api.main:app
```
To limit a download, add `--streams chl_l4,phys` and/or `--years 2020-2022` to `src/download_copernicus.py`.

## Run the pipeline step by step
Prefix each command with `ALGAE_PROFILE=sample` to work on the sample instead.
```bash
.venv/bin/python src/download_copernicus.py                 # ~8 GB; cached; --streams/--years to limit
.venv/bin/python src/download_copernicus.py --streams chl300
.venv/bin/python src/download_weather.py --start 1998-01-01 --end 2025-12-31
.venv/bin/python src/build_site_series.py                   # per-site daily series
.venv/bin/python src/detect_blooms.py                       # climatology, bloom events, phenology
.venv/bin/python src/make_tiles.py                          # map overlays for the web app
.venv/bin/python src/data_quality.py                        # precision report (optional)
```

## Run the app
```bash
.venv/bin/uvicorn api.main:app --reload
# open http://localhost:8000   (deep link: /?tab=blooms&site=Arnarfjörður&date=2023-04-20)
```

## Layout
```
config.py            regions, sites, dates, thresholds, dataset IDs
src/                 download, processing, bloom detection, map tiles, data quality
api/main.py          FastAPI: /api/sites, /api/manifest, /api/timeseries, /api/events,
                     /api/phenology, /api/calendar, /api/snapshot + static web app
web/                 Leaflet + Chart.js single page, no build step
data/                raw NetCDF + processed parquet/PNG (gitignored)
```

## References
- Gudmundsson, K. (1998). Long-term variation in phytoplankton productivity during spring in Icelandic waters. *ICES J. Mar. Sci.* 55(4):635–643.
- Zhai, L. et al. (2012). Phytoplankton phenology and production around Iceland and Faroes. *Cont. Shelf Res.* 37:15–25.
- Guðmundsson, K. et al. (2016). A regional correction model for satellite surface chlorophyll concentrations, based on measurements from sea water samples collected around Iceland. *Methods in Oceanography* 17:83–96.
- Hafró, toxic algae monitoring: https://www.hafogvatn.is/is/rannsoknir/voktun-eiturthorunga
