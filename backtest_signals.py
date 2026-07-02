"""
Backtest d'espérance — les signaux sont-ils RENTABLES, et à partir de quelle
confiance ? Simule chaque trade jusqu'au bout avec la VRAIE gestion de sortie du
moteur (break-even +1R, prise partielle 50% +1R, trailing, SL/TP), sur des bougies
réelles, et calcule win-rate + espérance (R) + total, global ET par tranche de
confiance. Une position à la fois (comme une session).

Usage (VPS) :  venv/bin/python3 backtest_signals.py EUR_USD
"""
import sys
from signals import SignalEngine

try:
    from config import PHASE1 as _P1
except Exception:
    _P1 = {}


def _need(eng):
    return max(eng.ema_slow, 2 * eng.adx_period) + eng.rsi_period + 2


def backtest(candles, instrument="?", engine=None, regime_map=None):
    eng = engine or SignalEngine()
    be_trig = _P1.get("be_trigger_R", 1.0)
    pt_trig = _P1.get("partial_trigger_R", 1.0)
    pt_frac = _P1.get("partial_frac", 0.5)
    trail = _P1.get("trail_mult_R", 1.0)
    max_hold = 240 // 15  # ~240 min en bougies M15

    trades = []
    pos = None
    n = len(candles)
    for i in range(_need(eng), n):
        bar = candles[i]
        if pos:
            buy = pos["side"] == "buy"
            ru = pos["r_unit"]
            # extrêmes
            pos["hwm"] = max(pos["hwm"], bar["h"]) if buy else pos["hwm"]
            pos["lwm"] = min(pos["lwm"], bar["l"]) if not buy else pos["lwm"]
            fav=( (bar["h"]-pos["entry"]) if buy else (pos["entry"]-bar["l"]) ) / ru
            # prise partielle + break-even à +1R
            if not pos["partial_done"] and favorable(pos, bar, pt_trig):
                pos["partial_R"] += pt_frac * pt_trig
                pos["partial_done"] = True
            if not pos["be"] and favorable(pos, bar, be_trig):
                pos["stop"] = pos["entry"]; pos["be"] = True
            # trailing
            if pos["be"] and trail > 0:
                pos["stop"] = max(pos["stop"], pos["hwm"] - trail * ru) if buy \
                    else min(pos["stop"], pos["lwm"] + trail * ru)
            # sortie : stop d'abord (pessimiste), puis objectif, puis temps
            R = None
            if buy:
                if bar["l"] <= pos["stop"]:
                    R = (pos["stop"] - pos["entry"]) / ru
                elif bar["h"] >= pos["tp"]:
                    R = (pos["tp"] - pos["entry"]) / ru
            else:
                if bar["h"] >= pos["stop"]:
                    R = (pos["entry"] - pos["stop"]) / ru
                elif bar["l"] <= pos["tp"]:
                    R = (pos["entry"] - pos["tp"]) / ru
            if R is None and (i - pos["i"]) >= max_hold:
                R = ((bar["c"] - pos["entry"]) if buy else (pos["entry"] - bar["c"])) / ru
            if R is not None:
                rem = (1.0 - (pt_frac if pos["partial_done"] else 0.0))
                total_R = pos["partial_R"] + rem * R
                trades.append({"R": round(total_R, 3), "conf": pos["conf"], "side": pos["side"]})
                pos = None
        if not pos:
            _reg = regime_map.get(bar.get("t")) if regime_map else None
            sig = eng.evaluate(instrument, candles[:i + 1], regime=_reg)
            if sig.proposal:
                pr = sig.proposal
                ru = abs(pr.entry_price - pr.stop_loss)
                if ru > 0:
                    pos = {"side": pr.side, "entry": pr.entry_price, "stop": pr.stop_loss,
                           "tp": pr.take_profit, "r_unit": ru, "conf": sig.confidence,
                           "be": False, "partial_done": False, "partial_R": 0.0,
                           "hwm": pr.entry_price, "lwm": pr.entry_price, "i": i}
    return trades


def favorable(pos, bar, thr):
    ru = pos["r_unit"]
    fav = ((bar["h"] - pos["entry"]) if pos["side"] == "buy" else (pos["entry"] - bar["l"])) / ru
    return fav >= thr


def report(trades, instrument):
    print("\n=== Backtest %s ===" % instrument)
    if not trades:
        print("Aucun trade simulé.")
        return
    def stats(ts):
        n = len(ts)
        wins = sum(1 for t in ts if t["R"] > 0)
        totR = sum(t["R"] for t in ts)
        return n, (100.0 * wins / n if n else 0), (totR / n if n else 0), totR
    n, wr, exp, tot = stats(trades)
    print("Trades %d · win-rate %.0f%% · espérance %.3f R/trade · total %.1f R" % (n, wr, exp, tot))
    print("Par tranche de confiance :")
    buckets = [("< 0.60", 0.0, 0.60), ("0.60–0.65", 0.60, 0.65),
               ("0.65–0.70", 0.65, 0.70), ("0.70–0.75", 0.70, 0.75), ("≥ 0.75", 0.75, 1.01)]
    for label, lo, hi in buckets:
        ts = [t for t in trades if lo <= t["conf"] < hi]
        if ts:
            bn, bwr, bexp, btot = stats(ts)
            flag = "✅" if bexp > 0 else "❌"
            print("   %-11s n=%-4d win %3.0f%%  espérance %+.3f R  total %+.1f R  %s"
                  % (label, bn, bwr, bexp, btot, flag))
    print(">> Espérance > 0 = rentable. On place la bande là où l'espérance reste positive.")


def btc_regime_map(fetch=None, fast=50, slow=200):
    """Carte {ts: 'up'/'down'} de la tendance BTC (EMA rapide vs lente sur 15min)."""
    from indicators import ema
    fetch = fetch or _fetch
    c = fetch("BTC/USD")
    if not c or len(c) < slow + 2:
        return {}
    closes = [x["c"] for x in c]
    ef = ema(closes, fast); es = ema(closes, slow)
    out = {}
    for i in range(len(c)):
        t = c[i].get("t")
        if t is not None:
            out[t] = "up" if ef[i] > es[i] else "down"
    return out


def _fetch(instrument, count=5000):
    """Historique PROFOND pour un échantillon statistiquement valable.
    Crypto : Parquet du pipeline d'historique (mois de données) si présent, sinon
    Kraken (limité). Forex : OANDA jusqu'à 5000 bougies M15 (~52 jours)."""
    if "/" in instrument:
        from pathlib import Path
        f = Path("data/history") / (instrument.replace("/", "-") + "_15Min.parquet")
        if f.exists():
            import pandas as pd
            df = pd.read_parquet(f).sort_values("ts")
            return [{"t": r.ts, "o": float(r.o), "h": float(r.h), "l": float(r.l), "c": float(r.c),
                     "v": float(getattr(r, "volume", 0.0) or 0.0)}
                    for r in df.itertuples()]
        from kraken_data import KrakenData
        return KrakenData().get_history(instrument, interval=15)
    from oanda_client import OandaClient
    return OandaClient(account="practice").get_candles(instrument, granularity="M15", count=count)


if __name__ == "__main__":
    inst = sys.argv[1] if len(sys.argv) > 1 else "EUR_USD"
    try:
        candles = _fetch(inst)
    except Exception as e:
        print("bougies indisponibles:", e); sys.exit(1)
    print("(%d bougies M15)" % len(candles))
    report(backtest(candles, inst), inst)
