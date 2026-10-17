# AlgaeWatch Iceland

An early-warning prototype for **algal blooms around Iceland**. It is built on Copernicus satellite and ocean-model data and aimed at salmon farms, shellfish growers and authorities (MAST, Hafró).

Everything is organised around the **82 licensed marine farms** in MAST's register: 56 sea-pen sites and 27 shellfish sites, minus one expired licence. Each farm is an analysis point (the sea within 10 km of it). Neighbouring farms are grouped into 15 areas, such as Arnarfjörður or Ísafjarðardjúp.

The web app has three modes:
- **Ocean data:** pick one forecast input (chlorophyll, clear-sky chlorophyll, chlorophyll vs normal, sea temperature, salinity, surface-layer depth, currents, waves, milky water). Only that layer is drawn, with its value under the cursor. Click a farm to see all inputs at that farm, each with a 60-day sparkline.
- **Past blooms:** the detected blooms day by day. A farm **pulses** when a bloom reaches it: red for a chlorophyll bloom, blue for milky water. The farm card shows **how we know**: the bloom test applied on the bloom's peak day, check by check, plus the farm's bloom history since 1998.
- **Forecast:** bloom risk at each farm for the next 3 and 7 days. The farm card shows **why**, in plain language, and **how sure we are**: for example, "when we say 60–80%, a bloom followed 69% of the time" (from past forecasts). It also shows what really happened afterwards.
- **How it works:** a three-step explainer covering what we measure, the bloom rule and the forecast with its test scores.

Design: a full-screen ocean map with dark glass panels, a timeline with an activity strip, a farm search (⌘K) and keyboard controls (←/→ step a day, space plays). Deep links work, e.g. `/?mode=past&date=2022-09-05&farm=Haganes`.

> Prototype. The app detects **high-biomass blooms**, using satellite chlorophyll as the signal; it cannot measure toxicity. Toxic species (*Dinophysis*, *Alexandrium*, *Pseudo-nitzschia*) are only detected by Hafró/MAST water sampling. Shellfish farms show the typical monthly share of toxic Hafró samples instead. Do not use it for operational decisions.

## Algae in Iceland (background; also the app's "Algae guide")
| Group | Main genera | Effect | Affects | Seen from space? | Icelandic record |
|---|---|---|---|---|---|
| Spring diatoms | *Skeletonema, Chaetoceros, Thalassiosira* | Natural yearly bloom; dense or spiny blooms can irritate gills, and decay lowers oxygen | Salmon farms (rarely) | Yes | Every year; our median peak is early June |
| Fish-killing flagellates (haptophytes) | *Chrysochromulina, Phaeocystis* | Gill-damaging toxins, foam, low oxygen | Farmed salmon | Partly (chlorophyll, haptophyte share) | Spring 2021 losses **reported** by a MAST vet (species not named); Seyðisfjörður over 20 years ago (**reported** by a local farmer) |
| Coccolithophores | *Emiliania huxleyi* | Milky water; not toxic | Nothing harmful known | Yes, very clearly | Every summer S/SW; Westfjords Sept 2022 (sampled by Hafró) |
| *Dinophysis* (dinoflagellate) | *D. acuminata, D. norvegica, D. acuta, D. rotundata* | DSP: diarrhoea | Mussels → people | No | Most common cause of warnings; Hvalfjörður warnings 2016–2019 |
| *Alexandrium* (dinoflagellate) | *A. tamarense, A. ostenfeldii* | PSP: nerve toxin | Mussels, scallops → people | No | Above the limit most summers (June–Aug); PSP toxin found in mussels (Eyjafjörður, Breiðafjörður) |
| *Pseudo-nitzschia* (diatom) | *P. seriata, P. delicatissima* | ASP (domoic acid) | Shellfish | No | Cells often above the limit, but **ASP toxin has never been found in Icelandic shellfish** (Hafró) |
| Cyanobacteria (freshwater) | *Dolichospermum, Aphanizomenon* | Scum, can be toxic | Lakes (Mývatn) | Yes, but not with our marine data | Recurring summer blooms; out of scope |

From our Hafró parse (917 samples, 7 stations, 2005–2019), the counts of samples above the safe limit were: *Dinophysis* ~124, *Alexandrium* ~110, *Pseudo-nitzschia* ~91. Most were in June–August, a few in April–November. Hafró stopped monitoring Hvalfjörður in June 2019. HAEDAT describes Iceland's record as incomplete, so the true number of events is likely higher.

**What this means for the app:** satellites can follow high-biomass blooms, which matter for sea-pen farms. Toxins, which matter for shellfish farms, need water samples. So shellfish farm cards lead with toxin information: this month's share of Hafró samples above the limit for DSP, PSP and ASP, plus the nearest Hafró station's history. A farm-type filter (All / Sea pens / Shellfish) is available in every mode.

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

## Bloom forecast (`src/train_bloom.py`, `src/predict.py`)
- **Target:** a *confirmed* high-biomass bloom at the site within the next **3 or 7 days**.
- **Inputs, only what is known on the forecast day:**
  - real cloud-free observations (4 km and 300 m)
  - gap-filled chlorophyll **delayed by 5 days**, because the reprocessed archive can fill a day using later images. Without the delay, PR-AUC was 0.51; with it, 0.46.
  - sea temperature and its anomaly, salinity, mixed layer, currents, waves, wind, sunlight, rain, season, site
- **Test:** trained 1998–2015, validated 2016–2018, tested on **2019–2025**. The 95% intervals resample whole site-years.

| Within | Model ROC-AUC / PR-AUC | Persistence (bloom seen last week) | Seasonal | New blooms only (model vs seasonal AUC) |
|---|---|---|---|---|
| 3 days | **0.86 / 0.46** | 0.73 / 0.20 | 0.65 / 0.12 | 0.77 vs 0.66 |
| 7 days | **0.82 / 0.45** | 0.67 / 0.22 | 0.66 / 0.18 | 0.75 vs 0.67 |

All gains are statistically significant. For brand-new blooms, precision is still modest (PR-AUC 0.11–0.17).
- **Replay:** `predict.py` makes **walk-forward** forecasts. Each year is predicted by a model trained only on earlier years, so the app's Forecast tab shows what the system would really have said on any day from 2005 to 2025, including the top 3 factors behind each forecast. Example: `python src/predict.py --date 2025-05-16`.
- **Coordinates:** every table in `data/processed/` has `lat`, `lon` and `kind` columns. These are the site or station coordinates (approximate placeholders); values are averaged over the ocean pixels around each point.

## Toxic-algae forecast (`src/scrape_hafro.py`, `src/train_toxic.py`)
- **Labels:** 917 Hafró monitoring samples from 7 stations (2005–2019). The scraper parses Hafró's Icelandic text to find which genus was above the shellfish-poisoning threshold.
- **Inputs:** conditions at the station **7 days before** each sample: sea temperature and its anomaly, salinity, mixed layer, chlorophyll and its anomaly, wind, sunlight, rain, season, and the last known result.
- **Test:** trained on 2005–2014 and tested on 2015–2019, against seasonal and persistence baselines, with a bootstrap 95% interval.
- **Result with the full data (any genus above threshold, 7 days ahead):** ROC-AUC **0.77**, against 0.72 for the seasonal baseline and 0.65 for persistence. The gain is **not statistically significant** (95% interval −0.02 to +0.14; only 194 test samples). Adding salinity and mixed-layer depth changed little. For *Dinophysis*, the model is significantly **worse** than the seasonal baseline. In short, these conditions add at most a small amount over "it's July at this station".

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
| Weather (wind, sunlight, air temperature, rain) | Open-Meteo archive (ERA5), fetched once per 0.25° grid cell | ~25 km | 1998–2025 |
| Licensed aquaculture sites (sea pens, shellfish, land farms) | MAST register, WFS `geoserver.mast.is` (not Copernicus) | licence polygons / points | current |

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
.venv/bin/python src/download_weather.py --start 1998-01-01 --end 2025-12-31   # --offline: reuse nearest cached cell
.venv/bin/python src/build_site_series.py                   # per-site daily series
.venv/bin/python src/detect_blooms.py                       # climatology, bloom events, phenology
.venv/bin/python src/import_farms.py                        # licensed farm sites -> reference/mast_farms.geojson; --refresh re-downloads
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
                     /api/phenology, /api/calendar, /api/snapshot, /api/farms + static web app
web/                 Leaflet + Chart.js single page, no build step
reference/           mast_farms.geojson: licensed farm sites (Data: Matvælastofnun (MAST))
../aquaculture-data/ fetch_aquaculture_is.py + aquaculture_is.db: MAST register download (SQLite)
data/                raw NetCDF + processed parquet/PNG (gitignored)
```

## References
- Gudmundsson, K. (1998). Long-term variation in phytoplankton productivity during spring in Icelandic waters. *ICES J. Mar. Sci.* 55(4):635–643.
- Zhai, L. et al. (2012). Phytoplankton phenology and production around Iceland and Faroes. *Cont. Shelf Res.* 37:15–25.
- Guðmundsson, K. et al. (2016). A regional correction model for satellite surface chlorophyll concentrations, based on measurements from sea water samples collected around Iceland. *Methods in Oceanography* 17:83–96.
- Hafró, toxic algae monitoring: https://www.hafogvatn.is/is/rannsoknir/voktun-eiturthorunga

### The running year (2026)
2026 is filled close to today. Each stream uses its reprocessed archive while it lasts, then continues with the near-real-time or analysis product, regridded onto the archive grid:

| Stream | Archive until | Then |
|---|---|---|
| Chlorophyll (gap-free) | 9 Oct | NRT gap-free, to 16 Oct |
| Algae groups, milky water, turbidity, 300 m | 9 Oct | none |
| Sea temperature | 30 Jun | OSTIA NRT, to 16 Oct |
| Salinity, surface layer, currents | 25 Aug | GLORYS analysis, capped at today |
| Waves | 31 Jul | Wave analysis, capped at today |

To update with new days: `python src/download_copernicus.py --years 2026 --refresh-current`, then rerun the pipeline. Forecasts are only issued up to the latest satellite day.

**2026 so far:** a quiet year for high-chlorophyll blooms at farms. Around Iceland, chlorophyll was normal on average (95th percentile 3.2 mg/m³) but had the fewest extreme patches of the decade (99th percentile 5.8, against 6.4–9.1 in 2016–2025). The year's story is milky-water (coccolithophore) blooms: 56 confirmed events, peaking at 16 farms on 11–12 July. Inside the fjords, the 300 m data shows high chlorophyll on many days that the 4 km data misses, and the bloom rule only uses the 4 km data.

### Notes on the farm-based run
- Open-Meteo has a daily quota. For 1998–2025, 22 of the 33 weather grid cells still borrow the nearest downloaded series (`--offline`): at most 53 km away, usually under 30 km. 2026 has all 33 cells. Rerun without `--offline` on another day to fill in the rest.
- 300 m fjord chlorophyll is available for 49 of the 89 points (farms inside the nine 300 m boxes); the others use 4 km data only.
- Validation with farms: Hafró's Sep 2022 Westfjords coccolithophore bloom is detected as **confirmed** at 12 Arnarfjörður/Dýrafjörður farms from 3 Sep 2022. The spring 2021 losses show a confirmed bloom at two Arnarfjörður farms from 2 May 2021.
- Bloom forecast with farms (test 2019–2025, 148,527 farm-days): ROC-AUC 0.85 for 3 days and 0.80 for 7 days, against 0.74/0.69 for persistence and 0.62/0.63 for seasonal. The bootstrap resamples whole area-years, because farms in one fjord share the same water.
