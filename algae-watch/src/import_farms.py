"""Licensed aquaculture sites (MAST register) -> GeoJSON for the web app.

Reads the active rows of the SQLite database written by ../aquaculture-data/fetch_aquaculture_is.py,
keeps the useful fields (in English, without the operators' ID numbers). Every marine farm (sea pens,
shellfish) is its own analysis point (config.FARMS); "point" is its unique name in the data tables and
"area" groups neighbouring farms (e.g. a fjord).
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


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

STATUS = {"Í gildi": "valid", "Umsókn": "application", "Útgefið": "issued", "Fallið úr gildi": "expired"}
FARMING = {"Sjókvíaeldi": "sea pens", "Landeldi með frárennsli í sjó": "land-based, outflow to sea",
           "Landeldi með frárennsli í ferskvatn": "land-based, outflow to freshwater"}


def feature(row):
    category, company, lat, lon, geom, props = row
    p = json.loads(props)
    licences = json.loads(p.get("lisense_json") or "[]")
    species = sorted({x["Species"] for x in licences if x.get("Species")})
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
    # analysis point name + area for every marine farm (same logic the pipeline uses)
    points = iter(config.farms_from_features(feats))
    for f in feats:
        pr = f["properties"]
        marine = pr["category"] in ("sea", "shellfish") and pr["status"] != "expired"
        pt = next(points) if marine else None
        pr["point"], pr["area"] = (pt["name"], pt["area"]) if pt else (None, None)

    config.FARMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.FARMS_FILE.write_text(json.dumps({
        "type": "FeatureCollection",
        "attribution": "Data: Matvælastofnun (MAST), https://geoserver.mast.is",
        "features": feats}, ensure_ascii=False))
    print(f"saved {config.FARMS_FILE}: {len(feats)} sites",
          dict(Counter(f["properties"]["category"] for f in feats)))

    areas = Counter(f["properties"]["area"] for f in feats if f["properties"]["area"])
    print(f"  {sum(areas.values())} marine farms in {len(areas)} areas:", dict(areas.most_common()))


if __name__ == "__main__":
    main()
