"""
Sweep REGIME BTC (etape 2c) — au meilleur reglage (adx=20, pull=1.0), teste si
n'entrer QUE dans le sens de la tendance BTC (input croise) ajoute un edge sur
les ALTS. Multi-epoques, paires profondes (hors BTC). Lecture seule (backtest).
VPS : python3 sweep_selectivity.py
"""
import os
from signals import SignalEngine
from backtest_signals import backtest, _fetch, btc_regime_map

FIX_ADX = float(os.environ.get("FIX_ADX", "20"))
FIX_PULL = float(os.environ.get("FIX_PULL", "1.0"))
GATE_GRID = [0, 1]                    # 0 = sans regime (reference) ; 1 = aligne BTC
WINDOW = int(os.environ.get("SWEEP_WINDOW", "3000"))
SWEEP_PAIRS = int(os.environ.get("SWEEP_PAIRS", "12"))
FRACS = (0.45, 0.72, 0.92)
MIN_TRADES = 15


def auto_instruments(n=SWEEP_PAIRS, data="data/history"):
    import glob
    fs = glob.glob(os.path.join(data, "*_15Min.parquet"))
    fs.sort(key=lambda f: os.path.getsize(f), reverse=True)
    out = []
    for f in fs:
        sym = os.path.basename(f)[:-len("_15Min.parquet")].replace("-", "/")
        if sym.split("/")[0].upper() in ("BTC", "XBT"):
            continue                  # le gate n'affecte pas BTC -> on l'exclut
        out.append(sym)
        if len(out) >= n:
            break
    return out or ["ETH/USD"]


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


def sweep(instruments, fetch=None, rmap=None, gate_grid=GATE_GRID, window=WINDOW, fracs=FRACS):
    fetch = fetch or _fetch
    rmap = rmap if rmap is not None else btc_regime_map(fetch)
    series = {}
    for inst in instruments:
        try:
            series[inst] = _windows(fetch(inst), window, fracs)
        except Exception:
            series[inst] = []
    rows = []
    for gate in gate_grid:
        print("  [%d/%d] regime BTC = %s ..." % (gate + 1, len(gate_grid), "ON" if gate else "off"), flush=True)
        pooled, run_esp = [], []
        for inst in instruments:
            for w in series[inst]:
                e = SignalEngine(use_store=False)
                e.adx_min = FIX_ADX; e.pullback_atr_mult = FIX_PULL; e.btc_regime_gate = gate
                tr = backtest(w, inst, engine=e, regime_map=(rmap if gate else None))
                pooled += tr
                if tr:
                    run_esp.append(sum(t["R"] for t in tr) / len(tr))
        s = _stats(pooled); s["gate"] = gate
        s["robust"] = sum(1 for x in run_esp if x > 0); s["nruns"] = len(run_esp)
        s["base"] = (gate == 0)
        rows.append(s)
    return rows


def render(rows):
    base = next((r for r in rows if r["base"]), None)
    out = ["regime BTC |   n    win%   esp(R)  total(R)   PF    robuste",
           "-----------+-----------------------------------------------"]
    for r in sorted(rows, key=lambda r: r["gate"]):
        out.append(" %-9s | %5d   %3d   %+.3f  %+.2f   %-5s %d/%d%s" % (
            "ON" if r["gate"] else "off", r["n"], r["win"], r["esp"], r["tot"],
            (str(r["pf"]) if r["pf"] is not None else "-"), r["robust"], r["nruns"],
            "  <= sans regime" if r["base"] else ""))
    on = next((r for r in rows if r["gate"] == 1), None)
    out += [""]
    if base and on:
        d = on["esp"] - base["esp"]
        out.append("Delta regime BTC : esp %+.3f R (%+.3f vs sans) | PF %s->%s | robuste %d/%d -> %d/%d | trades %d -> %d"
                   % (on["esp"], d, base["pf"], on["pf"], base["robust"], base["nruns"], on["robust"], on["nruns"], base["n"], on["n"]))
        out.append(">> Si 'ON' remonte nettement esperance/PF/robustesse au-dessus de 'off', le regime BTC apporte un edge. Sinon non.")
    return "\n".join(out)


if __name__ == "__main__":
    inst = auto_instruments()
    print("adx=%.0f pull=%.1f fixes | regime BTC off/ON | %d alts x %d epoques : %s"
          % (FIX_ADX, FIX_PULL, len(inst), len(FRACS), inst), flush=True)
    print(render(sweep(inst)))
