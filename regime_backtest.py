"""
Backtest carry sur funding MULTI-ANNEES -> verdict de REGIME.
Rejoue le carry selectif (short top-funding, cout realiste forfaitaire majors) sur
plusieurs annees et donne le NET par ANNEE CIVILE : on veut voir si ca survit au bear 2022.
Lecture seule. VPS : venv python regime_backtest.py
"""
import glob
import os
import json
import numpy as np
import pandas as pd

DATA = "data/funding_multi"
COST_BP = 50.0     # aller-retour majors (perp+spot liquides) ~0.5%


def load(min_obs=500):
    cols = {}
    for f in sorted(glob.glob(os.path.join(DATA, "*.parquet"))):
        sym = os.path.basename(f)[:-len(".parquet")]
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if "rate" not in df.columns or len(df) < min_obs:
            continue
        s = df.set_index("ts")["rate"].astype(float)
        cols[sym] = s[~s.index.duplicated(keep="last")]
    return pd.DataFrame(cols).sort_index()


def interval_h(mat):
    d = pd.Series(mat.index).diff().dt.total_seconds().median()
    return max(1.0, round(d / 3600.0))


def selective(mat, lookback, hold, q=0.2, cost_bp=COST_BP, min_names=8,
              only=None, cost_map=None, cost_aware=False):
    if only is not None:
        mat = mat[[c for c in mat.columns if c in only]]
    trail = mat.rolling(lookback, min_periods=lookback // 2).mean()
    recs = []
    for i in range(lookback, len(mat) - hold, hold):
        t = trail.iloc[i].dropna()
        if cost_aware and cost_map is not None:
            net_exp = t * hold - t.index.map(lambda c: cost_map.get(c, cost_bp) / 1e4)
            t = net_exp[net_exp > 0]
        else:
            t = t[t > 0]
        if len(t) < min_names:
            continue
        k = max(1, int(len(t) * q))
        chosen = t.sort_values(ascending=False).index[:k]
        realized = mat[chosen].iloc[i:i + hold].sum().mean()
        if cost_map is not None:
            cost = sum(cost_map.get(c, cost_bp) for c in chosen) / len(chosen) / 1e4
        else:
            cost = cost_bp / 1e4
        recs.append((mat.index[i], realized, realized - cost))
    return pd.DataFrame(recs, columns=["ts", "gross", "net"])


def load_exec_usdt(path="data/funding_multi/_exec_usdt.json"):
    if not os.path.exists(path):
        return None, None, None
    d = json.load(open(path))
    tab = d.get("table", {})
    only = {s for s, v in tab.items() if v.get("roundtrip_bp") is not None}
    cost = {s: v["roundtrip_bp"] for s, v in tab.items() if v.get("roundtrip_bp") is not None}
    return only, cost, d.get("avg_roundtrip_bp")


def annual(series_net, per_year):
    return series_net.mean() * per_year * 100.0


if __name__ == "__main__":
    mat = load()
    if not len(mat):
        raise SystemExit("Aucun funding multi-annees. Lance funding_multi.py d'abord.")
    ih = interval_h(mat)
    ppy = 8760.0 / ih
    print("Funding multi : %d periodes x %d perps | intervalle ~%.0fh | %s -> %s"
          % (mat.shape[0], mat.shape[1], ih, mat.index.min(), mat.index.max()), flush=True)

    # lookback/hold ~ 1 semaine et ~ 1 mois, exprimes en periodes de funding
    only, cost, avg = load_exec_usdt()
    if only:
        print("Cout USDT reel par nom : %d hedgeables, AR moyen %s bp (vs 344 bp Kraken) -> classement cout-conscient" % (len(only), avg))
    else:
        print("(_exec_usdt.json absent : cout forfaitaire %.0f bp)" % COST_BP)
    wk = max(3, int(round(168 / ih))); mo = max(6, int(round(720 / ih)))
    for lb, h, lab in [(wk, wk, "hebdo"), (wk, 2 * wk, "bi-hebdo"), (mo, mo, "mensuel")]:
        r = selective(mat, lb, h, only=only, cost_map=cost, cost_aware=bool(cost))
        if not len(r):
            continue
        net_ann = annual(r["net"], ppy / h)
        win = (r["net"] > 0).mean() * 100
        print("\n== %s (lookback=%d hold=%d periodes) : net %+.1f%%/an global, win %.0f%% =="
              % (lab, lb, h, net_ann, win))
        r = r.copy(); r["year"] = pd.DatetimeIndex(r["ts"]).year
        by = {int(y): g for y, g in r.groupby("year")}
        y0, y1 = mat.index.min().year, mat.index.max().year
        for y in range(y0, y1 + 1):
            g = by.get(y)
            if g is None or not len(g):
                print("   %d : AUCUNE position (funding <=0 -> reste en cash, ni gain ni perte)" % y)
            else:
                print("   %d : net %+7.1f%%/an  (win %.0f%%, %d periodes)"
                      % (y, annual(g["net"], ppy / h), (g["net"] > 0).mean() * 100, len(g)))
    print("\n>> VERDICT REGIME : si 2022 (bear) reste positif, l'edge tient hors marche haussier.")
    print("   Si 2022 s'effondre, le carry est un artefact de regime haussier -> pas un edge robuste.")
