# Project plan: Algal bloom early warning for Icelandic fjords

## Context

We are building a hackathon prototype: a website that shows (1) past algal blooms detected from Copernicus satellite data and (2) a 3–7 day bloom risk forecast for areas around Icelandic fish farms.

Pitch: "A North Atlantic early-warning system for harmful algal blooms, starting in Iceland." Customers are fish and shellfish farms, plus authorities (MAST, Hafró).

Time budget: one weekend. Working end-to-end beats perfect. Get a thin version of every phase working first, then improve.

## Important limitations (keep these honest in the UI and README)

- We predict **high-biomass blooms** using chlorophyll-a as a proxy. Chlorophyll is not toxicity; some harmful species do not show up as high chlorophyll.
- Satellite products are ~300 m to ~1 km per pixel. Narrow fjords are only partly resolved.
- Iceland is cloudy. Use gap-filled (L4) products where available, and handle missing values.
- Labels are derived from a rule (see Phase 2), not from confirmed toxic events.

## Tech stack

- Python 3.11+
- Data: `copernicusmarine` (Copernicus Marine Toolbox), `cdsapi` (ERA5 from Copernicus Climate Data Store), `xarray`, `netCDF4`, `pandas`, `numpy`
- Model: `lightgbm` (or `xgboost`), `scikit-learn`
- Backend: `FastAPI` + `uvicorn`
- Frontend: single static page with `Leaflet.js` (loaded from a CDN), plain JS, no build step
- Storage: local files (NetCDF for raw data, Parquet for tables). No database needed.

## Repository structure

```
algae-watch/
  README.md
  PLAN.md
  requirements.txt
  .env.example              # credential variable names only, never real values
  config.py                 # regions, dates, thresholds, farm locations
  data/
    raw/                    # downloaded NetCDF (gitignored)
    processed/              # parquet tables (gitignored)
  src/
    download_copernicus.py
    download_era5.py
    detect_blooms.py
    build_features.py
    train_model.py
    predict.py
  api/
    main.py
  web/
    index.html
    app.js
    style.css
  notebooks/
    exploration.ipynb       # optional
```

## Configuration (config.py)

- **Regions** (bounding boxes, lat/lon, approximate; adjust if needed):
  - Eastfjords: lat 64.5 to 66.0, lon -15.0 to -13.0
  - Westfjords: lat 65.3 to 66.6, lon -24.6 to -21.3
- **Period:** 2016-05-01 to 2025-12-31 (Sentinel-3 OLCI era). Start with ONE region and TWO years for the first end-to-end run.
- **Forecast horizons:** 3 and 7 days.
- **Farm locations:** a list of named points (name, lat, lon). Start with placeholder points in Seyðisfjörður, Reyðarfjörður, Berufjörður, Arnarfjörður, Dýrafjörður, Patreksfjörður. Mark them clearly as approximate placeholders in the UI. They can be replaced later with real site coordinates from MAST licence records.
- **Bloom thresholds:** see Phase 2.

## Credentials

- Copernicus Marine: free account. Log in with `copernicusmarine login` (stores credentials locally). Do not hardcode credentials.
- Copernicus Climate Data Store (ERA5): free account, API key in `~/.cdsapirc`. The licence for ERA5 must be accepted on the CDS website once.
- If credentials are missing, the scripts should stop with a clear message explaining what to set up, not crash with a stack trace.

---

## Phase 1: Download data

### 1a. Chlorophyll-a (Copernicus Marine)

- Find the right product with `copernicusmarine describe` (search for ocean colour, chlorophyll, Arctic / North Atlantic, daily, L4 gap-free if available, plus an L3 version for comparison). Do NOT guess dataset IDs. Print the candidates and choose the one with the best spatial resolution and daily coverage over Iceland. Record the chosen dataset ID and the reason in README.md.
- Use `copernicusmarine subset` (or the Python API) to download only our bounding boxes, dates and the chlorophyll variable.
- Save to `data/raw/chl_<region>.nc`.

### 1b. Ocean physics (Copernicus Marine)

- Same approach: find a daily product covering Iceland with sea surface temperature, salinity, and surface currents (u, v). Prefer a reanalysis product for history.
- Save to `data/raw/phys_<region>.nc`.

### 1c. Weather (ERA5)

- Download daily (or hourly, then aggregate to daily) for the bounding boxes: 10 m wind u/v, 2 m air temperature, surface solar radiation downwards, total precipitation.
- Save to `data/raw/era5_<region>.nc`.

**Done when:** all three files exist for the first region and period, and a quick plot or printed summary shows sensible values (chlorophyll mostly 0.1–20 mg/m³, SST roughly 0–14 °C).

---

## Phase 2: Detect past blooms (labels)

`src/detect_blooms.py`

1. Load chlorophyll. Mask land and missing values.
2. Compute a **climatology**: for each pixel and each day-of-year (use a ±15 day window), the median chlorophyll over all years.
3. Mark a pixel-day as **bloom** if BOTH:
   - chlorophyll >= 2.5 × its climatology, AND
   - chlorophyll >= 3 mg/m³ (avoids flagging tiny absolute changes in clear water).
     Put both values in config.py so they are easy to tune.
4. Aggregate to the **analysis unit**: for each farm location, use the pixels within a 10 km radius. A farm-area-day is a bloom if >= 10% of its valid pixels are bloom pixels. Also keep the mean chlorophyll and the fraction of valid (non-cloud) pixels.
5. Save `data/processed/blooms.parquet` with columns: `date, site, chl_mean, chl_anomaly_ratio, bloom_fraction, valid_fraction, is_bloom`.
6. Also save a per-pixel bloom map for selected dates so the website can show historical blooms.

**Sanity check:** print bloom counts per month (expect peaks in spring and autumn). Look for a bloom around known events in Seyðisfjörður and note in README.md whether the detector finds it. Do not invent dates of known events; if no reliable date is available, skip this check and say so.

---

## Phase 3: Build features

`src/build_features.py`

For each `site` and `date`, compute:

- Chlorophyll: mean today, mean of last 3 and 7 days, change over last 3 days, anomaly ratio today, valid_fraction
- SST: today, 7-day mean, 7-day change, anomaly vs. climatology
- Salinity: today, 7-day change
- Currents: u, v, speed (mean over site area)
- Weather: wind speed and direction (7-day mean), solar radiation (7-day sum), air temperature (7-day mean), precipitation (7-day sum)
- Seasonality: sin and cos of day-of-year
- Site as a categorical feature

**Targets:** `bloom_in_3d` and `bloom_in_7d` = 1 if `is_bloom` is true on ANY day in the window (date+1 … date+H).

Rules:

- Use only information available on `date` for features. No future leakage.
- Keep rows with missing values; LightGBM handles NaN.
- Save `data/processed/features.parquet`.

---

## Phase 4: Train and evaluate the model

`src/train_model.py`

- Model: LightGBM binary classifier, one model per horizon.
- **Split by time**, never randomly: train on years up to and including 2023, validate on 2024, test on 2025 (adjust if the first run uses fewer years).
- Handle class imbalance with `scale_pos_weight` or class weights.
- Report on the test set: ROC-AUC, PR-AUC, precision and recall at a chosen threshold, and a confusion matrix.
- **Baselines to beat** (report side by side):
  1. Persistence: "bloom in the next H days if there is a bloom today"
  2. Seasonal: probability of bloom for that site and month from training years
- Save models to `models/bloom_3d.txt`, `models/bloom_7d.txt` and metrics to `models/metrics.json`.
- Save feature importance as a chart (PNG) for the pitch.

**Done when:** metrics are printed, compared against both baselines, and saved. If the model does not beat the baselines, say so plainly in README.md; that is a valid result to discuss, not something to hide.

---

## Phase 5: Prediction

`src/predict.py`

- For the latest available date in the data (or a chosen "demo date"), compute features and output a risk score (0–1) per site for 3 and 7 days.
- Risk levels: low < 0.3, medium 0.3–0.6, high > 0.6 (in config.py).
- Save `data/processed/latest_predictions.json`.
- Support a `--date YYYY-MM-DD` argument so we can replay a historical bloom in the demo ("Here is what our system would have said 5 days before the bloom").

---

## Phase 6: API

`api/main.py` (FastAPI)

- `GET /api/sites` → list of sites with name, lat, lon, and a flag saying they are approximate placeholders
- `GET /api/predictions?date=YYYY-MM-DD` → risk per site for 3 and 7 days, plus the main contributing features
- `GET /api/history?site=NAME` → time series of chlorophyll and bloom flags
- `GET /api/bloom-map?date=YYYY-MM-DD` → GeoJSON of bloom pixels for that date
- Serve the `web/` folder as static files at `/`.
- Enable CORS for local development.

---

## Phase 7: Website

`web/index.html`, `app.js`, `style.css`

- Full-screen Leaflet map centered on Iceland, OpenStreetMap tiles.
- Site markers colored by risk (green / amber / red); toggle between 3-day and 7-day forecast.
- Clicking a site opens a side panel with:
  - current risk score and level
  - a small chart of chlorophyll over the last 60 days with detected blooms highlighted (use a lightweight chart library from a CDN, e.g. Chart.js)
  - top 3 factors driving the risk (from feature contributions)
- Date picker to replay past dates.
- Layer toggle for "detected blooms" (bloom pixels for the selected date).
- A visible disclaimer: "Prototype. Predicts high-biomass blooms from satellite chlorophyll, not toxicity. Not for operational decisions."
- Works on a laptop screen and a phone.

---

## Phase 8: Demo and README

- README.md: what it does, data sources with dataset IDs, how to run each step, model results vs. baselines, limitations, next steps.
- A single command or script `run_all.sh` that runs Phases 1–5 for the demo region.
- Pick a demo date: a historical bloom the model predicted well. Use it for a "replay" in the pitch.
- Next steps to list: Sentinel-2 for narrow fjords, Sentinel-1 radar to handle clouds, bloom drift prediction using currents, adding Hafró/MAST toxin sampling data to move from biomass to toxicity, expansion to the Faroe Islands, Norway and Scotland.

---

## Instructions for Claude Code

- Work phase by phase. After each phase, run the code, show a short summary of results, and confirm the "Done when" criteria before moving on.
- Start small: one region, two years. Expand only after the full pipeline (Phases 1–7) works end to end.
- Cache downloads; never re-download data that already exists in `data/raw/`.
- Never fabricate data, dataset IDs, metrics or historical events. If something cannot be found or downloaded, stop and report it.
- Keep functions small and scripts runnable from the command line with `--region` and date arguments.
- Add `data/`, `models/` and credential files to `.gitignore`.
