# Aquagentic

**Early warning for algal blooms around Iceland's fish and shellfish farms, built on Copernicus satellite and ocean-model data.**

Copernicus Hackathon 2026 · [Live demo](https://aquagentic.netlify.app) · [Pitch deck](Presentation/Aquagentic%20%E2%80%94%20Hackathon%20Pitch.pptx)

---

## The problem

Algal blooms can kill farmed salmon (gill damage, low oxygen) and close shellfish harvests (DSP, PSP and ASP toxins). Farmers and authorities (MAST, Hafró) mostly learn about them after the damage is done. Iceland is cloudy, its fjords are narrow, and toxic species cannot be seen from space, so a simple "chlorophyll map" is not enough.

## What Aquagentic does

Everything is organised around the **82 licensed marine farms** in MAST's register (56 sea-pen sites, 27 shellfish sites, minus one expired licence). Each farm is an analysis point: the sea within 10 km of it.

| Mode | What you see |
|---|---|
| **Ocean data** | One forecast input at a time (chlorophyll, sea temperature, salinity, currents, waves, milky water, …) with the value under the cursor. Click a farm for all inputs and 60-day sparklines. |
| **Past blooms** | Detected blooms day by day; farms pulse when a bloom reaches them. The farm card shows *how we know*: the bloom test applied check by check, plus the farm's bloom history since 1998. |
| **Forecast** | Bloom risk at each farm for the next 3 and 7 days, plus a predicted-bloom map over the whole sea. The card explains *why* and *how sure we are* (e.g. "when we say 60–80%, a bloom followed 69% of the time"). |
| **How it works** | A three-step explainer: what we measure, the bloom rule, the forecast and its test scores. |

Keyboard controls (←/→ step a day, space plays), farm search (⌘K), and deep links such as `/?mode=past&date=2022-09-05&farm=Haganes`.

## Results

Tested out of sample on 2019–2025 (trained on 1998–2015, validated on 2016–2018):

| Forecast horizon | Model ROC-AUC | Persistence | Seasonal |
|---|---|---|---|
| 3 days | **0.86** | 0.73 | 0.65 |
| 7 days | **0.82** | 0.67 | 0.66 |

- **Validated against a real event:** Hafró's Sept 2022 Westfjords *E. huxleyi* bloom is detected as confirmed milky water at 12 Arnarfjörður/Dýrafjörður farms from 3 Sep 2022.
- **Honest negatives are reported too:** a dedicated "new bloom onset" model did not beat the current one, and the toxic-algae forecast (ROC-AUC 0.77) is not statistically better than a seasonal baseline. Details are in the [algae-watch README](algae-watch/README.md).

## Copernicus data used

Ocean colour (chlorophyll, algae groups, reflectance, turbidity; 4 km and 300 m), OSTIA sea surface temperature, GLORYS physics (temperature, salinity, currents, mixed layer), BGC and wave models, and satellite salinity. Complemented by ERA5 weather via Open-Meteo, the MAST aquaculture register and Hafró phytoplankton monitoring. The full table with dataset IDs is in the [algae-watch README](algae-watch/README.md#data-iceland-box-6367n-2513w).

## Repository layout

```
algae-watch/         the product: data pipeline, models, FastAPI backend, web app
  src/               download, bloom detection, forecasting, validation, static export
  api/               FastAPI service
  web/               Leaflet + Chart.js single page, no build step
  reference/         licensed farm sites (GeoJSON from MAST)
  validation/        documented bloom events used for checks
aquaculture-data/    MAST register download script + SQLite snapshot
Presentation/        hackathon pitch deck
plan.md              original project plan
Whiteboard.excalidraw  team whiteboard
```

## Quick start

Needs Python 3.12 and a free [Copernicus Marine account](https://data.marine.copernicus.eu/register).

```bash
cd algae-watch
uv venv --python 3.12 .venv && uv pip install -p .venv -r requirements.txt
.venv/bin/copernicusmarine login

./run_all.sh sample                                   # 2023–2024 only, ~10 min, ~1 GB
ALGAE_PROFILE=sample .venv/bin/uvicorn api.main:app   # open http://localhost:8000
```

Use `./run_all.sh full` for the complete 1998–2025 record (~8 GB). The pipeline can also be run step by step, and the app exports to a fully static site for Netlify; see the [algae-watch README](algae-watch/README.md#setup).

## Limitations

This is a prototype. It detects **high-biomass blooms** using satellite chlorophyll as the signal and **cannot measure toxicity**. *Dinophysis*, *Alexandrium* and *Pseudo-nitzschia* are only found by water sampling, so shellfish farms show the typical monthly share of toxic Hafró samples instead. Clouds hide each site on 50–80% of days, and coastal satellite chlorophyll is uncertain (about ±42%). **Do not use it for operational decisions.**

## Credits

- Copernicus Marine Service and the Copernicus programme for the satellite and model data.
- Farm locations: *Data: Matvælastofnun (MAST)*.
- Toxic-algae sample data: Hafrannsóknastofnun (Hafró).
- Weather: Open-Meteo (ERA5).
