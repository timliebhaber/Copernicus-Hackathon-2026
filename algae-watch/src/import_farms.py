"""Licensed aquaculture sites (MAST register) -> GeoJSON for the web app.

Reads the active rows of the SQLite database written by ../aquaculture-data/fetch_aquaculture_is.py,
keeps the useful fields (in English, without the operators' ID numbers), and links each farm to the
nearest analysis point (config.POINTS) within config.SITE_RADIUS_KM, i.e. the area whose data applies.
Output: reference/mast_farms.geojson (same for both profiles). Data: Matvælastofnun (MAST).
Usage: python src/import_farms.py [--refresh] [--db PATH]
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

STATUS = {"Í gildi": "valid", "Umsókn": "application", "Útgefið": "issued", "Fallið úr gildi": "expired"}
FARMING = {"Sjókvíaeldi": "sea pens", "Landeldi með frárennsli í sjó": "land-based, outflow to sea",
           "Landeldi með frárennsli í ferskvatn": "land-based, outflow to freshwater"}


def km(lat0, lon0, lat1, lon1):
    dy = (lat1 - lat0) * 111.2
    dx = (lon1 - lon0) * 111.2 * np.cos(np.radians((lat0 + lat1) / 2))
    return float(np.hypot(dx, dy))


def nearest_point(lat, lon):
    best = min(config.POINTS, key=lambda p: km(lat, lon, p["lat"], p["lon"]))
    d = km(lat, lon, best["lat"], best["lon"])
    return (best["name"], round(d, 1)) if d <= config.SITE_RADIUS_KM else (None, round(d, 1))


def feature(row):
    category, company, lat, lon, geom, props = row
    p = json.loads(props)
    licences = json.loads(p.get("lisense_json") or "[]")
    species = sorted({x["Species"] for x in licences if x.get("Species")})
    area, dist = nearest_point(lat, lon)
    return {"type": "Feature", "geometry": json.loads(geom), "properties": {
        "category": category,
        "name": p.get("SiteName") or p.get("Description") or company,
        "company": company,
        "fjord": p.get("FjordName"),
        "region": p.get("RegionName"),
        "licence": p.get("Code"),
        "status": STATUS.get(p.get("RegistrationState"), p.get("RegistrationState")),
        "in_use": p.get("IsInUse"),
        "farming": FARMING.get(p.get("FarmingType"), p.get("FarmingType")),
        "biomass_limit_t": p.get("SiteBiomassLimit") or p.get("BiomassLimit"),
        "species": species or None,
        "valid_until": (p.get("ValidUntil") or "")[:10] or None,
        "lat": lat, "lon": lon,
        "analysis_site": area, "analysis_km": dist,
    }}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=config.FARMS_DB)
    ap.add_argument("--refresh", action="store_true", help="re-download the MAST register into --db first")
    args = ap.parse_args()

    if args.refresh:
        fetcher = args.db.parent / "fetch_aquaculture_is.py"
        subprocess.run([sys.executable, str(fetcher), "--db", str(args.db)], check=True)
    if not args.db.exists():
        if config.FARMS_FILE.exists():
            print(f"no {args.db}; keeping existing {config.FARMS_FILE.name}")
            return
        raise SystemExit(f"no {args.db}: run ../aquaculture-data/fetch_aquaculture_is.py or pass --refresh")

    con = sqlite3.connect(args.db)
    rows = con.execute("SELECT category, company, lat, lon, geometry_json, properties_json FROM aquaculture_sites "
                       "WHERE active = 1 AND in_bounds = 1 ORDER BY category, lat").fetchall()
    con.close()
    feats = [feature(r) for r in rows]

    config.FARMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.FARMS_FILE.write_text(json.dumps({
        "type": "FeatureCollection",
        "attribution": "Data: Matvælastofnun (MAST), https://geoserver.mast.is",
        "features": feats}, ensure_ascii=False))
    print(f"saved {config.FARMS_FILE}: {len(feats)} sites",
          dict(Counter(f["properties"]["category"] for f in feats)))

    linked = Counter(f["properties"]["analysis_site"] for f in feats if f["properties"]["analysis_site"])
    for p in config.POINTS:
        print(f"  {p['name']:<26} {linked.get(p['name'], 0):3d} farm sites within {config.SITE_RADIUS_KM} km")
    marine = [f["properties"] for f in feats if f["properties"]["category"] != "land"]
    loose = Counter(f["fjord"] or f["region"] for f in marine if not f["analysis_site"])
    print(f"  sea/shellfish sites outside every analysis area: {sum(loose.values())}", dict(loose.most_common()))


if __name__ == "__main__":
    main()
