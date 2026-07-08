"""
Backtest STAT-ARB cross-sectionnel sur l'univers profond (400 paires).
Hypothese testee : reversion cross-sectionnelle a court terme (les paires qui ont
trop monte redescendent). A chaque rebalance : LONG les perdants recents / SHORT les
gagnants recents (panier neutre en $), on mesure le rendement forward, net de couts.
Lecture seule. VPS (venv python) : python3 statarb.py
"""
import glob
import os
import numpy as np
import pandas as pd


def load_matrix(gran="1H", data="data/history", top=120, min_obs=1500):
    """Matrice large : index=ts, colonnes=paires, valeurs=close. Les N paires les + profondes."""
    files = sorted(glob.glob(os.path.join(data, "*_%s.parquet" % gran)),
                   key=os.path.getsize, reverse=True)
    cols = {}
    for f in files:
        sym = os.path.basename(f)[:-len("_%s.parquet" % gran)].replace("-", "/")
        try:
            df = pd.read_parquet(f, columns=["ts", "c"])
        except Exception:
            continue
        if len(df) < min_obs:
            continue
        s = df.set_index("ts")["c"]
        s = s[~s.index.duplicated(keep="last")]
        cols[sym] = s
        if len(cols) >= top:
            break
    return pd.DataFrame(cols).sort_index()


def xsec_backtest(mat, lookback=6, hold=6, q=0.2, cost_bps=10.0, min_names=15):
    """LONG bottom-q du rendement passe / SHORT top-q ; rebalance non chevauchante (pas de hold)."""
    past = mat.pct_change(lookback)
    fwd = mat.shift(-hold) / mat - 1.0
    recs = []
    for i in range(lookback, len(mat) - hold, hold):
        p = past.iloc[i].dropna()
        fr = fwd.iloc[i].dropna()
        common = p.index.intersection(fr.index)
        if len(common) < min_names:
            continue
        p = p[common]; fr = fr[common]
        k = max(1, int(len(common) * q))
        ranked = p.sort_values()
        longs = ranked.index[:k]        # perdants passes -> long (reversion)
        shorts = ranked.index[-k:]      # gagnants passes -> short
        gross = fr[longs].mean() - fr[shorts].mean()
        cost = 2.0 * (cost_bps / 1e4)   # round-trip approx (2 jambes)
        recs.append((mat.index[i], gross, gross - cost))
    return pd.DataFrame(recs, columns=["ts", "gross", "net"])


def stats(r):
    if r is None or not len(r):
        return {"periods": 0}
    g, n = r["gross"], r["net"]
    sh = (n.mean() / n.std() * np.sqrt(len(r))) if n.std() > 0 else 0.0
    return {"periods": len(r),
            "gross_bp": round(g.mean() * 1e4, 2), "net_bp": round(n.mean() * 1e4, 2),
            "net_tot_pct": round(n.sum() * 100, 1),
            "win_net": round((n > 0).mean() * 100), "sharpe_net": round(sh, 2)}


def sweep(mat, grid_lb=(3, 6, 12, 24), grid_hold=(3, 6, 12), q=0.2, cost_bps=10.0):
    rows = []
    for lb in grid_lb:
        for h in grid_hold:
            s = stats(xsec_backtest(mat, lookback=lb, hold=h, q=q, cost_bps=cost_bps))
            s["lb"] = lb; s["hold"] = h
            rows.append(s)
            print("  lb=%2d hold=%2d -> gross %+.2fbp net %+.2fbp net_tot %+.1f%% win %s%% sharpe %s (%d periodes)"
                  % (lb, h, s.get("gross_bp", 0), s.get("net_bp", 0), s.get("net_tot_pct", 0),
                     s.get("win_net", 0), s.get("sharpe_net", 0), s.get("periods", 0)), flush=True)
    return rows


def epochs_robust(mat, lookback, hold, q=0.2, cost_bps=10.0, n_epochs=6):
    """Esperance nette par epoque (periode decoupee en n) -> robustesse."""
    r = xsec_backtest(mat, lookback=lookback, hold=hold, q=q, cost_bps=cost_bps)
    if not len(r):
        return {"epochs_pos": 0, "n": 0, "net_bp": 0.0}
    idx = np.array_split(np.arange(len(r)), n_epochs)
    means = [r["net"].iloc[ix].mean() for ix in idx if len(ix)]
    pos = sum(1 for m in means if m > 0)
    return {"epochs_pos": pos, "n": len(means),
            "net_bp": round(r["net"].mean() * 1e4, 2),
            "per_epoch_bp": [round(float(m) * 1e4, 2) for m in means]}


if __name__ == "__main__":
    import sys
    grans = sys.argv[1:] or ["1H", "15Min"]
    for gran in grans:
        print("\n=== Univers %s ===" % gran, flush=True)
        mat = load_matrix(gran=gran)
        if not len(mat):
            print("  (aucune paire %s trouvee)" % gran); continue
        print("Matrice : %d pas x %d paires (%s -> %s)" % (mat.shape[0], mat.shape[1],
              mat.index.min(), mat.index.max()), flush=True)
        rows = sweep(mat)
        best = max(rows, key=lambda r: r.get("net_bp", -1e9))
        rob = epochs_robust(mat, best["lb"], best["hold"])
        print("\n>> Meilleur %s : lb=%d hold=%d | net %+.2f bp/periode | sharpe %s | robuste %d/%d epoques"
              % (gran, best["lb"], best["hold"], best.get("net_bp", 0), best.get("sharpe_net", 0),
                 rob["epochs_pos"], rob["n"]))
        print("   par epoque (bp): %s" % rob["per_epoch_bp"])
        verdict = ("EDGE PLAUSIBLE" if best.get("net_bp", 0) > 0 and rob["epochs_pos"] >= rob["n"] - 1
                   else "PAS D'EDGE (net<=0 ou non robuste)")
        print("   VERDICT %s : %s" % (gran, verdict))
