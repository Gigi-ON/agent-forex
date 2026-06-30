"""
Backtest AVANT/APRÈS — les changements PHASE1 proposés par Grok-Analyste
améliorent-ils vraiment l'espérance ? Compare le moteur ACTUEL au moteur PROPOSÉ
(adx_min 25, pullback_atr_mult 1.2, stop_min_atr 2.5) sur les mêmes bougies.

Usage (VPS) :  venv/bin/python3 backtest_compare.py BTC/USD
"""
import sys
from backtest_signals import backtest, _fetch
from signals import SignalEngine


def _proposed():
    e = SignalEngine()
    e.adx_min = 25.0
    e.pullback_atr_mult = 1.2
    e.stop_min_atr = 2.5
    return e


def _stats(trades):
    n = len(trades)
    if not n:
        return (0, 0.0, 0.0, 0.0)
    wins = sum(1 for t in trades if t["R"] > 0)
    tot = sum(t["R"] for t in trades)
    return (n, 100.0 * wins / n, tot / n, tot)


def compare(candles, instrument):
    cur = _stats(backtest(candles, instrument))
    prop = _stats(backtest(candles, instrument, engine=_proposed()))
    print("\n=== %s (%d bougies) ===" % (instrument, len(candles)))
    print("                 trades   win    espérance   total")
    print("  ACTUEL         %5d   %3.0f%%   %+.3f R   %+.1f R" % (cur[0], cur[1], cur[2], cur[3]))
    print("  PROPOSÉ (Grok) %5d   %3.0f%%   %+.3f R   %+.1f R" % (prop[0], prop[1], prop[2], prop[3]))
    better = "✅ PROPOSÉ meilleur" if prop[2] > cur[2] else "❌ PROPOSÉ pas mieux"
    print("  -> espérance : %s (%+.3f R/trade)" % (better, prop[2] - cur[2]))
    if prop[0] < cur[0] * 0.5:
        print("  ⚠️ le proposé coupe >50%% des trades (moins de flux)")
    return cur, prop


if __name__ == "__main__":
    inst = sys.argv[1] if len(sys.argv) > 1 else "EUR_USD"
    try:
        candles = _fetch(inst)
    except Exception as e:
        print("bougies indisponibles:", e); sys.exit(1)
    compare(candles, inst)
