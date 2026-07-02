"""
Sweep VOLUME (etape 2b) — au meilleur reglage de seuils (adx=20, pull=1.0),
teste l'effet d'un FILTRE DE VOLUME (n'entrer que si volume >= X x sa moyenne),
sur plusieurs epoques et les paires les + profondes. Objectif : le volume
ajoute-t-il un edge ? Lecture seule (backtest). VPS : python3 sweep_selectivity.py
"""
import os
from signals import SignalEngine
from backtest_signals import backtest, _fetch

FIX_ADX = float(os.environ.get("FIX_ADX", "20"))
FIX_PULL = float(os.environ.get("FIX_PULL", "1.0"))
VOL_GRID = [0.0, 1.0, 1.25, 1.5, 2.0, 3.0]          # 0 = pas de filtre (reference)
WINDOW = int(os.environ.get("SWEEP_WINDOW", "3000"))
SWEEP_PAIRS = int(os.environ.get("SWEEP_PAIRS", "12"))
FRACS = (0.45, 0.72, 0.92)
MIN_TRADES = 15


def auto_instruments(n=SWEEP_PAIRS, data="data/history"):
    import glob
    fs = glob.glob(os.path.join(data, "*_15Min.parquet"))
    fs.sort(key=lambda f: os.path.getsize(f), reverse=True)
    return [os.path.basename(f)[:-len("_15Min.parquet")].replace("-", "/") for f in fs[:n]] or ["BTC/USD", "ETH/USD"]


def _stats(trades):
    n = len(trades)
    if not n:
        return {"n": 0, "win": 0, "esp": 0.0, "tot": 0.0, "pf": None}
    wins = [t["R"] for t in trades if t["R"] > 0]; losses = [t["R"] for t in trades if t["R"] <= 0]
    tot = sum(t["R"] for t in trades); gw = sum(wins); gl = abs(sum(losses))
    return {"n": n, "win": round(100 * len(wins) / n), "esp": round(tot / n, 3),
            "tot": round(tot, 2), "pf": (round(gw / gl, 2) if gl > 0 else None)}


def _windows(candles, W, fracs):
    L = len(candles)
    if L < 250:
        return []
    if L <= W:
        return [candles]
    return [candles[max(0, min(int(L * f), L - W)):max(0, min(int(L * f), L - W)) + W] for f in fracs]


def sweep(instruments, fetch=None, vol_grid=VOL_GRID, window=WINDOW, fracs=FRACS):
    fetch = fetch or _fetch
    series = {}
    for inst in instruments:
        try:
            series[inst] = _windows(fetch(inst), window, fracs)
        except Exception:
            series[inst] = []
    rows = []
    for k, vol in enumerate(vol_grid, 1):
        print("  [%d/%d] filtre volume = %.2fx ..." % (k, len(vol_grid), vol), flush=True)
        pooled, run_esp = [], []
        for inst in instruments:
            for w in series[inst]:
                e = SignalEngine(use_store=False)
                e.adx_min = FIX_ADX; e.pullback_atr_mult = FIX_PULL; e.vol_min_ratio = vol
                tr = backtest(w, inst, engine=e)
                pooled += tr
                if tr:
                    run_esp.append(sum(t["R"] for t in tr) / len(tr))
        s = _stats(pooled); s["vol"] = vol
        s["robust"] = sum(1 for x in run_esp if x > 0); s["nruns"] = len(run_esp)
        s["base"] = (vol == 0.0)
        rows.append(s)
    return rows


def render(rows):
    base = next((r for r in rows if r["base"]), None)
    out = ["volume |   n    win%   esp(R)  total(R)   PF    robuste",
           "-------+---------------------------------------------------"]
    for r in sorted(rows, key=lambda r: r["vol"]):
        out.append("%5.2fx | %5d   %3d   %+.3f  %+.2f   %-5s %d/%d%s" % (
            r["vol"], r["n"], r["win"], r["esp"], r["tot"],
            (str(r["pf"]) if r["pf"] is not None else "-"), r["robust"], r["nruns"],
            "  <= sans filtre" if r["base"] else ""))
    elig = [r for r in rows if r["n"] >= MIN_TRADES]
    top = sorted(elig, key=lambda r: (-(r["robust"]), -r["esp"]))[:3]
    out += ["", "== TOP 3 (robustesse puis esperance ; n>=%d) ==" % MIN_TRADES]
    for r in top:
        d = (" | Delta %+.3f vs sans filtre" % (r["esp"] - base["esp"])) if base else ""
        out.append("  volume>=%.2fx -> esp %+.3f R | %d trades | win %d%% | PF %s | +sur %d/%d epoques%s"
                   % (r["vol"], r["esp"], r["n"], r["win"], r["pf"], r["robust"], r["nruns"], d))
    out += ["", ">> Si aucun seuil de volume ne remonte nettement l'esperance/PF au-dessus de 'sans filtre', le volume n'apporte pas d'edge ici."]
    return "\n".join(out)


if __name__ == "__main__":
    inst = auto_instruments()
    print("adx=%.0f pull=%.1f fixes | filtre volume %s | %d paires x %d epoques : %s"
          % (FIX_ADX, FIX_PULL, VOL_GRID, len(inst), len(FRACS), inst), flush=True)
    print(render(sweep(inst)))
