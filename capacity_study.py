"""
Etude MAGNITUDE + CAPACITE du carry (LECTURE SEULE).
Funding 8h reel ; chaque position plafonnee a cap_frac * open_interest ; slippage croissant
avec la taille. Repond : quelle taille $ tenable, pour quel rendement NET %/an, et tenue par annee.
VPS : venv python capacity_study.py
"""
import glob
import json
import os
import numpy as np
import pandas as pd

DATA = "data/capacity"
SLIP_COEF = 3.0     # bp de slippage par 0.1% d'OI consomme (approx, ajustable)
Q = 0.2
MIN_NAMES = 6


def load():
    fund, oi = {}, {}
    for f in sorted(glob.glob(os.path.join(DATA, "*.parquet"))):
        b = os.path.basename(f)[:-len(".parquet")]
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if "rate" not in df.columns or len(df) < 200:
            continue
        df = df.set_index("ts")
        fund[b] = df["rate"].astype(float)
        if "oi_usd" in df.columns:
            oi[b] = df["oi_usd"].astype(float)
    return pd.DataFrame(fund).sort_index(), pd.DataFrame(oi).sort_index()


def load_cost(path="data/funding_multi/_exec_usdt.json"):
    if not os.path.exists(path):
        return {}
    d = json.load(open(path)); t = d.get("table", {})
    return {s: v["roundtrip_bp"] for s, v in t.items() if v.get("roundtrip_bp") is not None}


def study(fund, oi, cost, cap_frac, hold, lookback, ppy):
    trail = fund.rolling(lookback, min_periods=lookback // 2).mean()
    recs = []
    for i in range(lookback, len(fund) - hold, hold):
        t = trail.iloc[i].dropna()
        net_exp = t * hold - t.index.map(lambda c: cost.get(c, 50.0) / 1e4)
        t = net_exp[net_exp > 0]
        if len(t) < MIN_NAMES:
            continue
        k = max(1, int(len(t) * Q))
        chosen = t.sort_values(ascending=False).index[:k]
        dep = 0.0; pnl = 0.0
        for c in chosen:
            oi_c = oi[c].iloc[i] if c in oi.columns and not pd.isna(oi[c].iloc[i]) else np.nan
            if pd.isna(oi_c) or oi_c <= 0:
                continue
            cap = cap_frac * oi_c
            fsum = fund[c].iloc[i:i + hold].sum()             # funding encaisse (short)
            slip = SLIP_COEF * (cap_frac / 0.001)             # bp, croit avec la taille
            cst = (cost.get(c, 50.0) + slip) / 1e4
            pnl += cap * (fsum - cst)
            dep += cap
        if dep > 0:
            recs.append((fund.index[i], dep, pnl / dep))
    if not recs:
        return None
    r = pd.DataFrame(recs, columns=["ts", "deployed", "ret"])
    r["year"] = pd.DatetimeIndex(r["ts"]).year
    net_ann = r["ret"].mean() * (ppy / hold) * 100
    by_year = {int(y): g["ret"].mean() * (ppy / hold) * 100 for y, g in r.groupby("year")}
    return {"cap_usd": r["deployed"].mean(), "net_ann_pct": net_ann,
            "worst_year": min(by_year.values()), "by_year": by_year, "periods": len(r)}


if __name__ == "__main__":
    fund, oi = load()
    if not len(fund):
        raise SystemExit("Aucune donnee capacity. Lance capacity_fetch.py d'abord.")
    ih = (pd.Series(fund.index).diff().dt.total_seconds().median()) / 3600.0
    ppy = 8760.0 / max(1.0, ih)
    print("Capacity : %d periodes x %d perps | intervalle ~%.0fh | %s -> %s"
          % (fund.shape[0], fund.shape[1], ih, fund.index.min(), fund.index.max()), flush=True)
    print("OI dispo sur %d/%d perps" % (oi.shape[1], fund.shape[1]))
    wk = max(3, int(round(168 / ih))); mo = max(6, int(round(720 / ih)))
    for lab, hold in [("hebdo", wk), ("mensuel", mo)]:
        print("\n== Rebalance %s ==" % lab)
        print("  cap/nom |  capacite tenable $ | net %/an |  pire annee")
        for cf in [0.001, 0.005, 0.01, 0.02]:
            s = study(fund, oi, load_cost(), cf, hold, wk, ppy)
            if not s:
                continue
            print("   %4.1f%% | %18s | %+7.1f%% | %+7.1f%% (%s)"
                  % (cf * 100, "{:,.0f}".format(s["cap_usd"]), s["net_ann_pct"], s["worst_year"],
                     " ".join("%d:%+.0f" % (y, v) for y, v in sorted(s["by_year"].items()))))
    print("\n>> Lire : capacite $ tenable a chaque plafond, rendement NET realiste, et tenue chaque annee.")
    print("   C'est le chiffre deployable honnete (funding 8h, plafond liquidite, slippage).")
