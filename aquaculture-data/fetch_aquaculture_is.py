#!/usr/bin/env python3
"""
Fetch all aquaculture sites in Iceland from MAST's public WFS (GeoServer)
and upsert them into a SQLite database.

Source: Matvælastofnun (MAST), https://geoserver.mast.is/geoserver/wfs
Attribution required: "Data: Matvælastofnun (MAST)".

Stdlib only (Python 3.10+).

Usage:
    python fetch_aquaculture_is.py                       # fetch -> aquaculture_is.db
    python fetch_aquaculture_is.py --db /path/x.db
    python fetch_aquaculture_is.py --include-applications
    python fetch_aquaculture_is.py --inspect             # print attribute keys per layer, no DB write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

DEFAULT_BASE_URL = "https://geoserver.mast.is/geoserver/wfs"
USER_AGENT = "aquaculture-is-fetcher/1.0 (+contact: set-your-email-here)"

# Layer -> category. Sea layer with company info is used instead of view_3_all_sites
# to avoid duplicate sea sites and to get operator names.
CORE_LAYERS: dict[str, str] = {
    "mast:view_1_company_and_site_info": "sea",
    "mast:view_landsites": "land",
    "mast:view_1_shellfish_company_and_site_info": "shellfish",
}
APPLICATION_LAYERS: dict[str, str] = {
    "mast:view_6_applications": "sea_application",
    "mast:view_shellfish_application": "shellfish_application",
}

# Generous Iceland bbox (incl. Westfjords / Eastfjords offshore sites)
LON_MIN, LON_MAX = -25.5, -12.5
LAT_MIN, LAT_MAX = 62.5, 67.5

# Attribute-name hints (lowercased substring match). MAST field names are not
# documented publicly; run --inspect once and tighten these if needed.
COMPANY_HINTS = ("company", "fyrirtaeki", "fyrirtæki", "leyfishafi", "rekstraradili",
                 "rekstraraðili", "operator", "owner", "eigandi")
SITE_HINTS = ("site_name", "sitename", "eldissvaedi", "eldissvæði", "svaedi", "svæði",
              "starfsstod", "starfsstöð", "location", "stadur", "staður", "name", "nafn")

# Safety rail: if a layer returns far fewer features than last time, don't
# deactivate the missing rows (likely a partial/broken response).
MAX_DROP_RATIO = 0.5

log = logging.getLogger("aquaculture_is")


# ---------------------------------------------------------------- fetching ---

def build_url(base_url: str, layer: str) -> str:
    # WFS 1.0.0 + srsName=EPSG:4326 => GeoServer returns lon/lat (x/y) order.
    params = {
        "service": "WFS",
        "version": "1.0.0",
        "request": "GetFeature",
        "typeName": layer,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def http_get_json(url: str, retries: int = 4, timeout: int = 60) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                # GeoServer returns XML ServiceExceptionReport on errors, often with HTTP 200
                snippet = raw[:300].decode("utf-8", "replace")
                raise RuntimeError(f"Non-JSON response (WFS exception?): {snippet}") from e
            if data.get("type") != "FeatureCollection":
                raise RuntimeError(f"Unexpected payload type: {data.get('type')!r}")
            return data
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as e:
            last_err = e
            # 4xx (except 429) won't fix itself
            if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429:
                break
            if attempt == retries:
                break
            wait = min(2 ** attempt, 30)
            log.warning("Attempt %d/%d failed for %s: %s — retrying in %ss",
                        attempt, retries, url, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"Giving up on {url}: {last_err}")


# ---------------------------------------------------------------- geometry ---

def _iter_positions(coords: Any) -> Iterable[tuple[float, float]]:
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        yield float(coords[0]), float(coords[1])
    elif isinstance(coords, (list, tuple)):
        for c in coords:
            yield from _iter_positions(c)


def _ring_centroid(ring: list) -> tuple[float, float, float] | None:
    """Area-weighted centroid of a ring (planar; fine at site scale). Returns (x, y, area)."""
    a = cx = cy = 0.0
    n = len(ring)
    if n < 3:
        return None
    for i in range(n - 1 if ring[0] == ring[-1] else n):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-15:
        return None
    a *= 0.5
    return cx / (6 * a), cy / (6 * a), abs(a)


def representative_point(geom: dict | None) -> tuple[float, float] | None:
    """Return (lon, lat). Point as-is, polygons -> area-weighted centroid, else vertex mean."""
    if not geom or "coordinates" not in geom:
        return None
    gtype, coords = geom.get("type"), geom["coordinates"]
    if gtype == "Point":
        return float(coords[0]), float(coords[1])
    if gtype in ("Polygon", "MultiPolygon"):
        polys = [coords] if gtype == "Polygon" else coords
        parts = [c for p in polys if p and (c := _ring_centroid(p[0]))]
        total = sum(p[2] for p in parts)
        if total > 0:
            return (sum(p[0] * p[2] for p in parts) / total,
                    sum(p[1] * p[2] for p in parts) / total)
    pts = list(_iter_positions(coords))
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def in_iceland(lon: float, lat: float) -> bool:
    return LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX


def fix_axis(lon: float, lat: float) -> tuple[float, float, bool]:
    """Detect swapped axis order (a classic WFS/EPSG:4326 pitfall)."""
    if not in_iceland(lon, lat) and in_iceland(lat, lon):
        return lat, lon, True
    return lon, lat, False


# -------------------------------------------------------------- attributes ---

def pick_field(props: dict[str, Any], hints: tuple[str, ...]) -> str | None:
    lowered = {k.lower(): k for k in props}
    for hint in hints:                       # hint order = priority
        for lk, k in lowered.items():
            if hint in lk:
                v = props[k]
                if v not in (None, ""):
                    return str(v).strip()
    return None


def feature_id(layer: str, feat: dict) -> str:
    fid = feat.get("id")
    if fid:
        return f"{layer}:{fid}"
    digest = hashlib.sha1(json.dumps([feat.get("geometry"), feat.get("properties")],
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return f"{layer}:sha1:{digest[:16]}"


# --------------------------------------------------------------- database ----

SCHEMA = """
CREATE TABLE IF NOT EXISTS aquaculture_sites (
    id              TEXT PRIMARY KEY,
    layer           TEXT NOT NULL,
    category        TEXT NOT NULL,
    company         TEXT,
    site_name       TEXT,
    lat             REAL,
    lon             REAL,
    in_bounds       INTEGER NOT NULL DEFAULT 1,
    geom_type       TEXT,
    geometry_json   TEXT,
    properties_json TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sites_category ON aquaculture_sites(category);
CREATE INDEX IF NOT EXISTS idx_sites_company  ON aquaculture_sites(company);
CREATE INDEX IF NOT EXISTS idx_sites_latlon   ON aquaculture_sites(lat, lon);

CREATE TABLE IF NOT EXISTS fetch_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    layer       TEXT NOT NULL,
    status      TEXT NOT NULL,
    n_features  INTEGER,
    n_skipped   INTEGER,
    message     TEXT
);

CREATE VIEW IF NOT EXISTS companies AS
SELECT company,
       COUNT(*)                  AS n_sites,
       GROUP_CONCAT(DISTINCT category) AS categories,
       AVG(lat)                  AS mean_lat,
       AVG(lon)                  AS mean_lon
FROM aquaculture_sites
WHERE active = 1 AND company IS NOT NULL
GROUP BY company;
"""

UPSERT = """
INSERT INTO aquaculture_sites
    (id, layer, category, company, site_name, lat, lon, in_bounds, geom_type,
     geometry_json, properties_json, active, first_seen, last_seen)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    category        = excluded.category,
    company         = excluded.company,
    site_name       = excluded.site_name,
    lat             = excluded.lat,
    lon             = excluded.lon,
    in_bounds       = excluded.in_bounds,
    geom_type       = excluded.geom_type,
    geometry_json   = excluded.geometry_json,
    properties_json = excluded.properties_json,
    active          = 1,
    last_seen       = excluded.last_seen
"""


@dataclass
class LayerResult:
    rows: list[tuple]
    skipped: int
    swapped: int
    out_of_bounds: int


def transform(layer: str, category: str, fc: dict, now: str) -> LayerResult:
    rows, skipped, swapped, oob = [], 0, 0, 0
    for feat in fc.get("features", []):
        geom = feat.get("geometry")
        pt = representative_point(geom)
        if pt is None:
            skipped += 1
            continue
        lon, lat, was_swapped = fix_axis(*pt)
        swapped += was_swapped
        ok = in_iceland(lon, lat)
        oob += not ok
        props = feat.get("properties") or {}
        rows.append((
            feature_id(layer, feat), layer, category,
            pick_field(props, COMPANY_HINTS), pick_field(props, SITE_HINTS),
            round(lat, 7), round(lon, 7), int(ok),
            geom.get("type") if geom else None,
            json.dumps(geom, ensure_ascii=False),
            json.dumps(props, ensure_ascii=False, default=str),
            now, now,
        ))
    return LayerResult(rows, skipped, swapped, oob)


def sync_layer(con: sqlite3.Connection, layer: str, res: LayerResult, now: str) -> int:
    """Upsert rows and deactivate rows of this layer that disappeared. Returns #deactivated."""
    prev_active = con.execute(
        "SELECT COUNT(*) FROM aquaculture_sites WHERE layer = ? AND active = 1", (layer,)
    ).fetchone()[0]
    con.executemany(UPSERT, res.rows)

    if prev_active and len(res.rows) < prev_active * (1 - MAX_DROP_RATIO):
        log.warning("%s: got %d features vs %d active before — skipping deactivation "
                    "(possible partial response)", layer, len(res.rows), prev_active)
        return 0
    cur = con.execute(
        "UPDATE aquaculture_sites SET active = 0 WHERE layer = ? AND last_seen < ? AND active = 1",
        (layer, now),
    )
    return cur.rowcount


# ------------------------------------------------------------------- main ----

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="aquaculture_is.db", help="SQLite file (default: %(default)s)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--include-applications", action="store_true",
                    help="Also fetch sites still in the licensing process")
    ap.add_argument("--inspect", action="store_true",
                    help="Print attribute keys + a sample feature per layer; no DB writes")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    layers = dict(CORE_LAYERS)
    if args.include_applications:
        layers.update(APPLICATION_LAYERS)

    if args.inspect:
        for layer in layers:
            fc = http_get_json(build_url(args.base_url, layer))
            feats = fc.get("features", [])
            print(f"\n== {layer}: {len(feats)} features")
            if feats:
                print("keys:", sorted((feats[0].get("properties") or {}).keys()))
                print("geometry:", (feats[0].get("geometry") or {}).get("type"))
                print("sample:", json.dumps(feats[0].get("properties"), ensure_ascii=False)[:800])
        return 0

    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)
    failures = 0

    for layer, category in layers.items():
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            fc = http_get_json(build_url(args.base_url, layer))
            res = transform(layer, category, fc, started)
            with con:  # one transaction per layer: all-or-nothing
                deactivated = sync_layer(con, layer, res, started)
                con.execute(
                    "INSERT INTO fetch_runs (started_at, finished_at, layer, status, n_features, n_skipped, message) "
                    "VALUES (?, ?, ?, 'ok', ?, ?, ?)",
                    (started, datetime.now(timezone.utc).isoformat(timespec="seconds"), layer,
                     len(res.rows), res.skipped,
                     f"swapped={res.swapped} out_of_bounds={res.out_of_bounds} deactivated={deactivated}"),
                )
            log.info("%-45s %4d sites (skipped %d, axis-swapped %d, out-of-bounds %d, deactivated %d)",
                     layer, len(res.rows), res.skipped, res.swapped, res.out_of_bounds, deactivated)
            if res.rows and not any(r[3] for r in res.rows):
                log.warning("%s: no company field matched — run --inspect and adjust COMPANY_HINTS", layer)
        except Exception as e:  # keep other layers going; existing data stays untouched
            failures += 1
            log.error("%s failed: %s", layer, e)
            with con:
                con.execute(
                    "INSERT INTO fetch_runs (started_at, finished_at, layer, status, message) "
                    "VALUES (?, ?, ?, 'error', ?)",
                    (started, datetime.now(timezone.utc).isoformat(timespec="seconds"), layer, str(e)[:1000]),
                )

    total = con.execute("SELECT COUNT(*) FROM aquaculture_sites WHERE active = 1").fetchone()[0]
    log.info("Done. %d active sites in %s (%d layer failure(s))", total, args.db, failures)
    con.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
