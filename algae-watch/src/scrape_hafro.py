"""Collect Hafró toxic-algae monitoring entries (voktun.hafro.is) as labels.

Each station page is a list of dated Icelandic text entries (2005-2019) saying which toxic
genera were found and whether their density was above the shellfish-poisoning threshold
(Dinophysis -> DSP, Alexandrium -> PSP, Pseudo-nitzschia -> ASP). This script caches the
pages, splits them into entries and parses each entry with simple rules.

Output: <data>/processed/hafro_toxic.parquet (one row per station and sampling date)
Usage: python src/scrape_hafro.py [--refresh]
"""
import argparse
import html
import re
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

BASE = "https://voktun.hafro.is/"
STATIONS = {st["page"]: (st["name"], st["lat"], st["lon"]) for st in config.HAFRO_STATIONS}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst", "september", "október", "nóvember", "desember"])}
DATE_RE = re.compile(r"(\d{1,2})\.\s*(" + "|".join(MONTHS) + r")\s*(\d{4})", re.I)
GENERA = {
    "dinophysis": (re.compile(r"dinophys|\bDSP\b", re.I)),
    "alexandrium": (re.compile(r"alexandri|\bPSP\b", re.I)),
    "pseudo_nitzschia": (re.compile(r"pseudo-?\s?nitz|\bASP\b", re.I)),
}
ABOVE = re.compile(r"yfir\s+viðmið", re.I)
BELOW = re.compile(r"undir\s+viðmið|innan\s+við\s+viðmið|neðan\s+viðmið", re.I)
PREVIOUS = re.compile(r"síðustu|seinustu|fyrri\s+talning", re.I)  # "in the last count" -> not this sample
NONE_FOUND = re.compile(r"engar\s+(tegundir\s+)?eitur|fundust\s+ekki|ekki\s+vart", re.I)
WARNING = re.compile(r"varað\s+(er\s+)?við\s+neyslu", re.I)
CELLS = re.compile(r"(\d{1,3}(?:\.\d{3})*|\d+)\s*(?:frumur|frumna)", re.I)
DOMINANT = {"diatoms": re.compile(r"kísilþör\w*\s+(voru\s+)?ríkjandi|ríkjandi\s+\w*\s*kísil", re.I),
            "dinoflagellates": re.compile(r"skoruþör\w*\s+(voru\s+)?ríkjandi", re.I)}


def page_text(key, refresh):
    cache = config.RAW / "hafro" / f"{key}.html"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not cache.exists():
        r = requests.get(BASE + key + ".htm", timeout=60)
        r.raise_for_status()
        cache.write_bytes(r.content)
    t = cache.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def split_entries(text):
    """Split page text at date headings; keep the first date of each block."""
    marks = list(DATE_RE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        d, mon, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
        try:
            date = pd.Timestamp(y, mon, d)
        except ValueError:
            continue
        out.append((date, body.strip()))
    return out


NEGATED = re.compile(r"\b(engin|engar|ekkert|ekki)\b", re.I)
SAMPLE_DATE = re.compile(r"tek\w*[^.]{0,40}?(\d{1,2})\.\s*(" + "|".join(MONTHS) + r")", re.I)


def sample_date(header_date, body):
    """Prefer the sampling date written in the text ('Sýni var tekið 28. september')."""
    m = SAMPLE_DATE.search(body[:200])
    if not m:
        return header_date
    d, mon = int(m.group(1)), MONTHS[m.group(2).lower()]
    y = header_date.year - (1 if mon > header_date.month + 1 else 0)
    try:
        return pd.Timestamp(y, mon, d)
    except ValueError:
        return header_date


def parse_entry(body):
    res = {f"{g}_{k}": False for g in GENERA for k in ("present", "above")}
    max_cells = {g: None for g in GENERA}
    clauses = [c for c in re.split(r"(?<=[.;])\s+", body) if c.strip()]
    for c in clauses:
        if PREVIOUS.search(c):
            continue
        # genus mentions, skipping negated parts ("Engin tegund sem getur valdið DSP ... var greind")
        for part in re.split(r",|\s+en\s+", c):
            if NEGATED.search(part):
                continue
            for g, rx in GENERA.items():
                if rx.search(part):
                    res[f"{g}_present"] = True
        # each "above threshold" goes to the genus named closest before it
        for m in ABOVE.finditer(c):
            before = c[:m.start()]
            best = None
            for g, rx in GENERA.items():
                hits = list(rx.finditer(before))
                if hits and (best is None or hits[-1].start() > best[1]):
                    best = (g, hits[-1].start())
            if best:
                res[f"{best[0]}_above"] = True
                res[f"{best[0]}_present"] = True
            else:  # "density of the toxic species was above..." -> all genera found in this entry
                for g in GENERA:
                    if res[f"{g}_present"]:
                        res[f"{g}_above"] = True
        for m in CELLS.finditer(c):
            n = int(m.group(1).replace(".", ""))
            before = c[:m.start()]
            near = [(list(rx.finditer(before))[-1].start(), g) for g, rx in GENERA.items() if rx.search(before)]
            if near:
                g = max(near)[1]
                max_cells[g] = max(max_cells[g] or 0, n)
    res["none_found"] = bool(NONE_FOUND.search(body)) and not any(res[f"{g}_present"] for g in GENERA)
    res["any_above"] = any(res[f"{g}_above"] for g in GENERA)
    res["warning"] = bool(WARNING.search(body)) and not re.search(r"ekki\s+(er\s+)?(lengur\s+)?varað|ekki\s+ástæða", body, re.I)
    res["dominant"] = next((k for k, rx in DOMINANT.items() if rx.search(body)), None)
    for g in GENERA:
        res[f"{g}_cells_per_l"] = max_cells[g]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download the pages")
    a = ap.parse_args()
    rows = []
    for key, (name, lat, lon) in STATIONS.items():
        entries = split_entries(page_text(key, a.refresh))
        for date, body in entries:
            if len(body) < 40:  # a bare date in a heading, not a sample entry
                continue
            rows.append({"station": name, "lat": lat, "lon": lon, "date": sample_date(date, body),
                         "posted": date, **parse_entry(body),
                         "text": body[:600]})
        print(f"{name:26s} {len(entries):4d} entries")
    df = pd.DataFrame(rows).drop_duplicates(["station", "date"]).sort_values(["station", "date"])
    df = df[(df.date.dt.year >= 2000) & (df.date.dt.year <= 2026)]
    out = config.PROCESSED / "hafro_toxic.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"\nsaved {out}: {len(df)} sampling entries, {df.date.min().date()} .. {df.date.max().date()}")
    print("\nentries above threshold (per genus):")
    print(df.groupby("station")[[f"{g}_above" for g in GENERA] + ["any_above"]].sum().to_string())
    print("\nany toxic genus above threshold, by month (all stations):")
    m = df.groupby(df.date.dt.month).any_above.agg(["sum", "size"])
    print("  " + "  ".join(f"{k}:{r['sum']}/{r['size']}" for k, r in m.iterrows()))


if __name__ == "__main__":
    main()
