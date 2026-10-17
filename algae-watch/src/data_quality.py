"""Measure how precise each data stream is over Iceland (Mar-Oct, the ocean-colour season).

Output: data/processed/data_quality.json and a printed report.
Usage: python src/data_quality.py
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import common  # noqa: E402

warnings.simplefilter("ignore", RuntimeWarning)
SEASON = [3, 4, 5, 6, 7, 8, 9, 10]


YEARS = (config.LAST_YEAR - 1, config.LAST_YEAR)


def stream(prefix):
    return common.open_stream(prefix, years=YEARS).load()


def season(ds):
    return ds.sel(time=ds.time.dt.month.isin(SEASON))


def site_values(da, ds_for_mask, radius_km=10):
    """{site: daily area-mean series} over the season."""
    out = {}
    for s in config.FARMS:
        m = common.site_mask(ds_for_mask, s, radius_km=radius_km)
        v = da.squeeze().values[:, m]
        out[s["name"]] = pd.Series(np.nanmean(v, axis=1), index=pd.to_datetime(da.time.values).normalize())
    return out


def compare(a, b, log=False):
    """Bias, RMSE, correlation between two aligned series (b - a)."""
    df = pd.concat([a, b], axis=1).dropna()
    if log:
        df = np.log10(df[(df > 0).all(axis=1)])
    if len(df) < 5:
        return None
    d = df.iloc[:, 1] - df.iloc[:, 0]
    return {"n": int(len(df)), "bias": float(d.mean()), "rmse": float(np.sqrt((d ** 2).mean())),
            "r": float(df.corr().iloc[0, 1])}


def pooled(results):
    rs = [r for r in results.values() if r]
    if not rs:
        return None
    n = sum(r["n"] for r in rs)
    return {"n": n, "bias": sum(r["bias"] * r["n"] for r in rs) / n,
            "rmse": float(np.sqrt(sum(r["rmse"] ** 2 * r["n"] for r in rs) / n)),
            "r": float(np.median([r["r"] for r in rs]))}


def main():
    Q = {}

    # 1. Chlorophyll: product's own per-pixel uncertainty (%)
    unc = season(stream("chl_l4"))
    u = unc["CHL_uncertainty"]
    Q["chl_uncertainty_pct"] = {
        "all_ocean_median": float(u.median(skipna=True)),
        "by_site_median": {k: float(v.median()) for k, v in site_values(u, unc).items()},
    }

    # 2. Gap-filling error. L4 equals L3 where observed, so test filled pixels against the
    #    NEXT day's real observation, and compare with observed->next-day (natural change).
    l4 = np.log10(season(stream("chl_l4"))["CHL"].values)
    l3 = np.log10(season(stream("pft_l3"))["CHL"].values)
    obs_now, obs_next = np.isfinite(l3[:-1]), np.isfinite(l3[1:])
    filled = ~obs_now & np.isfinite(l4[:-1]) & obs_next
    seen = obs_now & obs_next
    d_fill = l4[:-1][filled] - l3[1:][filled]
    d_seen = l3[:-1][seen] - l3[1:][seen]
    rm = lambda d: float(np.sqrt(np.nanmean(d ** 2)))  # noqa: E731
    Q["chl_gapfill_vs_observed"] = {
        "n_filled": int(filled.sum()), "n_observed": int(seen.sum()),
        "rmsd_log10_filled_vs_nextday": rm(d_fill), "rmsd_log10_observed_vs_nextday": rm(d_seen),
        "bias_log10_filled": float(np.nanmean(d_fill)),
        "typical_factor_filled": 10 ** rm(d_fill), "typical_factor_observed": 10 ** rm(d_seen)}

    # 3. Fjord (300 m) vs offshore 4 km chlorophyll at each site, same day
    s = pd.read_parquet(config.PROCESSED / "site_series.parquet")
    s = s[s.date.dt.month.isin(SEASON) & s.site.isin([x["name"] for x in config.FARMS]) &
          s.date.dt.year.between(*YEARS)]
    per = {}
    for name, g in s.groupby("site"):
        per[name] = compare(g.set_index("date").chl_obs, g.set_index("date").chl_300m, log=True)
    Q["chl_300m_vs_4km"] = {"pooled": pooled(per), "by_site": per}

    # 4. Cloud cover: share of season days with an observation at the site
    Q["cloud_free_days_pct"] = {
        name: {"chl_4km": float(np.isfinite(g.chl_obs).mean() * 100),
               "chl_300m": float(np.isfinite(g.chl_300m).mean() * 100)}
        for name, g in s.groupby("site")}

    # 5. SST: OSTIA (satellite) error field, and GLORYS model vs OSTIA
    ost = stream("sst_ostia")
    sst_obs = ost["analysed_sst"] - 273.15
    err = ost["analysis_error"]
    phys = stream("phys")
    obs_site = site_values(sst_obs, ost)
    mod_site = site_values(phys["thetao"], phys)
    per = {k: compare(obs_site[k], mod_site[k]) for k in obs_site}
    Q["sst"] = {"ostia_analysis_error_median_K": float(err.median(skipna=True)),
                "ostia_error_by_site_K": {k: float(v.median()) for k, v in site_values(err, ost).items()},
                "glorys_minus_ostia": {"pooled": pooled(per), "by_site": per}}

    # 6. Salinity: satellite/in situ SSS error field, and GLORYS vs SSS product
    sss = stream("sss")
    obs_site = site_values(sss["sos"], sss, radius_km=20)
    mod_site = site_values(phys["so"], phys)
    per = {k: compare(obs_site[k], mod_site[k]) for k in obs_site}
    Q["salinity"] = {"sss_error_median": float(sss["sos_error"].median(skipna=True)),
                     "glorys_minus_sss": {"pooled": pooled(per), "by_site": per}}

    # 7. BGC model chlorophyll vs satellite chlorophyll (log10): is the model usable?
    bgc = stream("bgc")
    mod_site = site_values(bgc["chl"], bgc, radius_km=25)
    sat_site = {name: g.set_index("date").chl for name, g in s.groupby("site")}
    per = {k: compare(sat_site[k], mod_site[k][mod_site[k].index.month.isin(SEASON)], log=True) for k in sat_site}
    Q["bgc_model_chl_vs_satellite"] = {"pooled": pooled(per), "by_site": per}

    # 8. Turbidity: water clarity at each site (murky water inflates satellite chlorophyll)
    tr = season(stream("transp"))
    Q["secchi_depth_m_median"] = {k: float(v.median()) for k, v in site_values(tr["ZSD"], tr).items()}

    out = config.PROCESSED / "data_quality.json"
    out.write_text(json.dumps(Q, indent=1, default=float))
    report(Q)
    print(f"\nsaved {out}")


def report(Q):
    f = lambda x, d=2: "–" if x is None else f"{x:.{d}f}"  # noqa: E731
    print("== Chlorophyll uncertainty (product field, %): median over Iceland ocean "
          f"{f(Q['chl_uncertainty_pct']['all_ocean_median'], 0)}%")
    for k, v in Q["chl_uncertainty_pct"]["by_site_median"].items():
        print(f"   {k:16s} {f(v, 0)}%")
    g = Q["chl_gapfill_vs_observed"]
    print(f"\n== Gap-filled chlorophyll vs next-day observation: RMSD {f(g['rmsd_log10_filled_vs_nextday'], 3)} log10 "
          f"(×{f(g['typical_factor_filled'])}, n={g['n_filled']}); observed vs next-day: "
          f"{f(g['rmsd_log10_observed_vs_nextday'], 3)} (×{f(g['typical_factor_observed'])}, n={g['n_observed']}); "
          f"bias filled {f(g['bias_log10_filled'], 3)}")
    p = Q["chl_300m_vs_4km"]["pooled"]
    print(f"\n== Fjord 300 m vs 4 km chlorophyll (log10): n={p['n']} bias={f(p['bias'], 3)} "
          f"(300 m is ×{f(10 ** p['bias'])} of 4 km) RMSD={f(p['rmse'], 3)} r={f(p['r'])}")
    print("\n== Cloud-free days at site, Mar–Oct (%)            4 km   300 m   Secchi depth (m)")
    for k, v in Q["cloud_free_days_pct"].items():
        print(f"   {k:16s}                                {f(v['chl_4km'], 0):>4s}   {f(v['chl_300m'], 0):>4s}   "
              f"{f(Q['secchi_depth_m_median'].get(k), 1)}")
    s = Q["sst"]
    p = s["glorys_minus_ostia"]["pooled"]
    print(f"\n== SST: OSTIA analysis error median {f(s['ostia_analysis_error_median_K'])} K; "
          f"GLORYS model − OSTIA at sites: bias {f(p['bias'])} °C, RMSE {f(p['rmse'])} °C, r={f(p['r'], 3)}")
    sa = Q["salinity"]
    p = sa["glorys_minus_sss"]["pooled"]
    print(f"== Salinity: SSS product error median {f(sa['sss_error_median'], 3)}; GLORYS − SSS at sites: "
          f"bias {f(p['bias'], 3)}, RMSE {f(p['rmse'], 3)}, r={f(p['r'])}")
    p = Q["bgc_model_chl_vs_satellite"]["pooled"]
    print(f"== BGC model chlorophyll vs satellite (log10): bias {f(p['bias'], 3)} (model ×{f(10 ** p['bias'])}), "
          f"RMSD {f(p['rmse'], 3)}, r={f(p['r'])}")


if __name__ == "__main__":
    main()
