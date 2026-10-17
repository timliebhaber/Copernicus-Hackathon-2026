#!/usr/bin/env bash
# Run the whole pipeline.  ./run_all.sh sample   -> 2023-2024 only (~10 min, ~1 GB, data_sample/)
#                          ./run_all.sh full     -> 1998-2025 (~8 GB, data/)
set -euo pipefail
cd "$(dirname "$0")"
export ALGAE_PROFILE="${1:-sample}"
PY=.venv/bin/python
[ -x "$PY" ] || { echo "Create the env first: uv venv --python 3.12 .venv && uv pip install -p .venv -r requirements.txt"; exit 1; }
echo "== profile: $ALGAE_PROFILE"
$PY src/download_copernicus.py
$PY src/download_copernicus.py --streams chl300
$PY src/download_weather.py
$PY src/build_site_series.py
$PY src/detect_blooms.py
$PY src/scrape_hafro.py          # Hafró toxic-algae samples 2005-2019 (labels)
$PY src/validate.py              # documented events, Hafró vs satellite, bloom timing vs literature
if [ "$ALGAE_PROFILE" = full ]; then
  $PY src/train_toxic.py --lead 7   # toxic-algae forecast, needs 2005-2019 data
  $PY src/train_bloom.py            # 3/7-day bloom forecast + metrics vs baselines
  $PY src/predict.py                # walk-forward daily forecasts for replay in the app
fi
$PY src/make_tiles.py
echo "== done. Start the app with:  ALGAE_PROFILE=$ALGAE_PROFILE .venv/bin/uvicorn api.main:app"
