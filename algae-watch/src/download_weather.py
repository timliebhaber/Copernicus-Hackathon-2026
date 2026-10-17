"""Daily weather per site from the Open-Meteo historical archive (ERA5-based, no key).

Usage: python src/download_weather.py [--start 2023-01-01 --end 2024-12-31]
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY = ["wind_speed_10m_mean", "wind_direction_10m_dominant", "temperature_2m_mean",
         "shortwave_radiation_sum", "precipitation_sum"]


def fetch_site(site, start, end, tries=6):
    for i in range(tries):
        r = requests.get(URL, timeout=120, params={
            "latitude": site["lat"], "longitude": site["lon"],
            "start_date": start, "end_date": end,
            "daily": ",".join(DAILY), "timezone": "GMT", "wind_speed_unit": "ms",
        })
        if r.status_code != 429:
            break
        wait = 30 * (i + 1)
        print(f"  rate limited, waiting {wait}s", flush=True)
        time.sleep(wait)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["daily"]).rename(columns={"time": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df["site"] = site["name"]
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="iceland")
    ap.add_argument("--start", default=config.START_DATE)
    ap.add_argument("--end", default=config.END_DATE)
    a = ap.parse_args()
    out = config.RAW / f"weather_{a.region}_{a.start[:4]}_{a.end[:4]}.parquet"
    if out.exists():
        print(f"[cached] {out.name}")
        return
    frames = []
    for s in config.POINTS:
        part = config.RAW / f".weather_{s['lat']}_{s['lon']}_{a.start}_{a.end}.parquet"
        if part.exists():
            frames.append(pd.read_parquet(part))
            continue
        print(f"[weather] {s['name']}", flush=True)
        df = fetch_site(s, a.start, a.end)
        df.to_parquet(part, index=False)
        frames.append(df)
        time.sleep(5)
    pd.concat(frames).to_parquet(out, index=False)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
