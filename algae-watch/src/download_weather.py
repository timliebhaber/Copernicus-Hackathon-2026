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
    ap.add_argument("--offline", action="store_true",
                    help="no downloads: give each grid cell the nearest already-downloaded series (Open-Meteo has a daily quota)")
    a = ap.parse_args()
    out = config.RAW / f"weather_{a.region}_{a.start[:4]}_{a.end[:4]}.parquet"
    if out.exists():
        print(f"[cached] {out.name}")
        return
    # ERA5 is a 0.25 deg grid: fetch each grid cell once and give it to every point inside
    cells = {}
    for p in config.POINTS:
        cells.setdefault((round(p["lat"] * 4) / 4, round(p["lon"] * 4) / 4), []).append(p["name"])
    print(f"{len(config.POINTS)} points in {len(cells)} weather grid cells", flush=True)
    cached = {}
    for f in config.RAW.glob(f".weather_*_{a.start}_{a.end}.parquet"):
        parts = f.name.split("_")
        cached[(float(parts[-4]), float(parts[-3]))] = f
    frames, borrowed = [], []
    for (lat, lon), names in cells.items():
        part = config.RAW / f".weather_cell_{lat}_{lon}_{a.start}_{a.end}.parquet"
        if part.exists():
            df = pd.read_parquet(part)
        elif a.offline:
            near = min(cached, key=lambda c: config._km({"lat": lat, "lon": lon}, {"lat": c[0], "lon": c[1]}))
            dist = config._km({"lat": lat, "lon": lon}, {"lat": near[0], "lon": near[1]})
            borrowed.append((lat, lon, round(dist), len(names)))
            df = pd.read_parquet(cached[near]).drop(columns=["site"], errors="ignore")
        else:
            print(f"[weather] cell {lat},{lon} ({len(names)} points)", flush=True)
            df = fetch_site({"lat": lat, "lon": lon, "name": ""}, a.start, a.end).drop(columns=["site"])
            df.to_parquet(part, index=False)
            time.sleep(5)
        for n in names:
            frames.append(df.assign(site=n))
    if borrowed:
        print(f"borrowed nearest weather for {len(borrowed)} cells (lat, lon, km away, points):", borrowed)
    pd.concat(frames).to_parquet(out, index=False)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
