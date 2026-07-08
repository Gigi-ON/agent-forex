"""
Backtest CARRY-PERPS (funding-only, v1) sur l'univers Kraken Futures.
Position delta-neutre : SHORT perp + LONG spot -> on encaisse le funding quand rate>0
(les longs paient les shorts). Le PnL prix ~ 0 (neutre), l'income ~ funding.
On mesure : carry annualise brut et NET de frais, dispersion, robustesse multi-epoques.
Lecture seule. VPS : venv python carry_backtest.py
"""
import glob
import os
import numpy as np
import pandas as pd

H_YEAR = 8760.0  # heures/an (funding Kraken PF_ = horaire)


def load_funding(data="data/funding", top=200, min_obs=2000):
    files = sorted(glob.glob(os.path.join(data, "*.parquet")),
                   key=os.path.getsize, reverse=True)
    cols = {}
    for f in files:
        sym = os.path.basename(f)[:-len(".parquet")]
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if "_manifest" in f or len(df) < min_obs:
            continue
        col = "relativeFundingRate" if "relativeFundingRate" in df.columns else (
              "fundingRate" if "fundingRate" in df.columns else "rate")
        s = df.set_index("ts")[col].astype(float)
        s = s[~s.index.duplicated(keep="last")]
        cols[sym] = s
        if len(cols) >= top:
            break
    return pd.DataFrame(cols).sort_index()


def per_symbol(mat):
    """Carry annualise brut par symbole (%/an) si on reste short en permanence."""
    ann = mat.mean() * H_YEAR * 100.0
    return ann.sort_values(ascending=False)


def always_short(mat, fee_bps=62.0, rebal_days=30):
    """Short tout l'univers en continu, rebalance rare (fee amorti). Income = mean funding."""
    per_hr = mat.mean(axis=1).dropna()            # income horaire moyen (equal-weight)
    gross_ann = per_hr.mean() * H_YEAR * 100.0
    n_rebal = len(per_hr) / (rebal_days * 24.0)
    fee_tot = n_rebal * (fee_bps / 1e4)
    net_ann = (per_hr.sum() - fee_tot) / len(per_hr) * H_YEAR * 100.0
    return {"gross_ann_pct": round(gross_ann, 1), "net_ann_pct": round(net_ann, 1),
            "hours_pos_pct": round((per_hr > 0).mean() * 100), "hours": len(per_hr)}


def selective_carry(mat, lookback=168, hold=168, q=0.2, fee_bps=62.0, min_names=8,
                    only=None, cost_map=None, cost_aware=False):
    """Rebalance non chevauchante : short le top-q par funding recent, encaisse le realise.
    only=set(symb) restreint l'univers ; cost_map={symb:bp} applique un cout reel par nom."""
    if only is not None:
        cols = [c for c in mat.columns if c in only]
        mat = mat[cols]
    trail = mat.rolling(lookback, min_periods=lookback // 2).mean()
    recs = []
    for i in range(lookback, len(mat) - hold, hold):
        t = trail.iloc[i].dropna()
        if cost_aware and cost_map is not None:
            # carry attendu NET du cout du nom : (taux horaire * hold) - cout aller-retour
            net_exp = t * hold - t.index.map(lambda c: cost_map.get(c, fee_bps) / 1e4)
            t = net_exp[net_exp > 0]                 # on ne garde que funding > cout
        else:
            t = t[t > 0]
        if len(t) < min_names:
            continue
        k = max(1, int(len(t) * q))
        chosen = t.sort_values(ascending=False).index[:k]
        realized = mat[chosen].iloc[i:i + hold].sum().mean()
        if cost_map is not None:
            cost = sum(cost_map.get(c, fee_bps) for c in chosen) / len(chosen) / 1e4
        else:
            cost = fee_bps / 1e4
        recs.append((mat.index[i], realized, realized - cost))
    return pd.DataFrame(recs, columns=["ts", "gross", "net"])


def load_exec(path="data/funding/_exec.json"):
    """Retourne (set_hedgeables, {symb: roundtrip_bp})."""
    import json as _j
    if not os.path.exists(path):
        return None, None
    d = _j.load(open(path))
    tab = d.get("table", {})
    only = {s for s, v in tab.items() if v.get("has_spot") and v.get("roundtrip_bp") is not None}
    cost = {s: v["roundtrip_bp"] for s, v in tab.items() if v.get("roundtrip_bp") is not None}
    return only, cost


def stats_periods(r, hold):
    if r is None or not len(r):
        return {"periods": 0}
    per_year = H_YEAR / hold
    g = r["gross"].mean() * per_year * 100.0
    n = r["net"].mean() * per_year * 100.0
    idx = np.array_split(np.arange(len(r)), 6)
    ep = [r["net"].iloc[ix].mean() for ix in idx if len(ix)]
    return {"periods": len(r), "gross_ann_pct": round(g, 1), "net_ann_pct": round(n, 1),
            "win_pct": round((r["net"] > 0).mean() * 100),
            "epochs_pos": sum(1 for m in ep if m > 0), "n_ep": len(ep)}


if __name__ == "__main__":
    mat = load_funding()
    print("Matrice funding : %d heures x %d perps (%s -> %s)"
          % (mat.shape[0], mat.shape[1], mat.index.min() if len(mat) else "-",
             mat.index.max() if len(mat) else "-"), flush=True)
    if not len(mat):
        raise SystemExit("Aucun parquet funding. Lance funding_fetch.py d'abord.")

    ps = per_symbol(mat)
    print("\n-- Carry annualise brut par perp (short permanent), top 10 / bottom 5 --")
    for s, v in list(ps.items())[:10]:
        print("  %-14s %+7.1f %%/an" % (s, v))
    print("   ...")
    for s, v in list(ps.items())[-5:]:
        print("  %-14s %+7.1f %%/an" % (s, v))

    print("\n-- Short tout l'univers en continu --")
    print("  ", always_short(mat))

    print("\n-- Carry selectif (short top-funding, rebalance hebdo/mensuel) --")
    for lb, h in [(168, 168), (168, 336), (720, 720)]:
        r = selective_carry(mat, lookback=lb, hold=h)
        s = stats_periods(r, h)
        print("  lookback=%dh hold=%dh -> gross %+.1f%%/an net %+.1f%%/an win %s%% robuste %s/%s (%d periodes)"
              % (lb, h, s.get("gross_ann_pct", 0), s.get("net_ann_pct", 0), s.get("win_pct", 0),
                 s.get("epochs_pos", 0), s.get("n_ep", 0), s.get("periods", 0)))
    only, cost = load_exec()
    if only:
        avg_rt = round(sum(cost[s] for s in only) / len(only), 1)
        print("\n== SOUS-UNIVERS EXECUTABLE (spot Kraken dispo, cout REEL par nom) ==")
        print("   Hedgeables: %d perps | cout aller-retour moyen: %.1f bp (vs 62 bp forfait)" % (len(only), avg_rt))
        for lb, h in [(168, 168), (168, 336), (720, 720)]:
            r = selective_carry(mat, lookback=lb, hold=h, only=only, cost_map=cost)
            st = stats_periods(r, h)
            print("   lookback=%dh hold=%dh -> gross %+.1f%%/an net %+.1f%%/an win %s%% robuste %s/%s (%d periodes)"
                  % (lb, h, st.get("gross_ann_pct", 0), st.get("net_ann_pct", 0), st.get("win_pct", 0),
                     st.get("epochs_pos", 0), st.get("n_ep", 0), st.get("periods", 0)))
        print("   >> Si le NET reste positif ICI, l'edge est EXECUTABLE (pas juste theorique).")
        print("\n== CORRECTION COUT-CONSCIENTE (classe par funding NET du cout, sous-univers executable) ==")
        for lb, h in [(168, 168), (168, 336), (720, 720)]:
            r = selective_carry(mat, lookback=lb, hold=h, only=only, cost_map=cost, cost_aware=True)
            st = stats_periods(r, h)
            print("   lookback=%dh hold=%dh -> gross %+.1f%%/an net %+.1f%%/an win %s%% robuste %s/%s (%d periodes)"
                  % (lb, h, st.get("gross_ann_pct", 0), st.get("net_ann_pct", 0), st.get("win_pct", 0),
                     st.get("epochs_pos", 0), st.get("n_ep", 0), st.get("periods", 0)))
        print("   >> Compare a la section precedente : si net ET robustesse remontent, la correction aide.")
    else:
        print("\n(_exec.json absent : lance exec_check.py pour le test d'executabilite.)")
    print("\n>> Lire le NET annualise. Si <=0 ou non robuste => carry mange par les frais/regimes.")
