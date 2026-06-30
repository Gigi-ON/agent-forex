"""
Diagnostic d'entrées — POURQUOI le moteur déterministe ne tire (presque) pas.

Passe SignalEngine sur une série de bougies réelles et compte, par bougie, la
raison du non-trade (régime ADX / confluence HTF / trop étendu / RSI extrême /
pas d'alignement) + le nombre d'ENTRÉES. On voit ainsi quel filtre affame le flux.

Usage (sur le VPS) :
    venv/bin/python3 diagnose_entries.py EUR_USD     # forex (OANDA)
    venv/bin/python3 diagnose_entries.py BTC/USD     # crypto (Kraken)
"""
import sys
from collections import Counter
from signals import SignalEngine


def _categorize(sig):
    if sig.proposal:
        return "ENTREE"
    note = (sig.notes[-1] if sig.notes else "").lower()
    if "pas assez" in note:
        return "donnees_insuffisantes"
    if "range" in note or "adx" in note:
        return "regime_ADX"
    if "supérieur" in note or "superieur" in note:
        return "confluence_HTF"
    if "étendu" in note or "etendu" in note:
        return "trop_etendu_pullback"
    if "suracheté" in note or "survendu" in note or "surachete" in note:
        return "RSI_extreme"
    if "alignement" in note:
        return "pas_alignement"
    if "atr nul" in note:
        return "ATR_nul"
    return "autre"


def diagnose(candles, engine=None, instrument="?"):
    engine = engine or SignalEngine()
    need = max(engine.ema_slow, 2 * engine.adx_period) + engine.rsi_period + 2
    cnt = Counter()
    n = len(candles)
    if n < need + 1:
        return {"instrument": instrument, "bougies": n, "evaluables": 0,
                "raisons": {}, "entrees": 0, "taux_entree_pct": 0.0,
                "note": "pas assez de bougies (%d/%d)" % (n, need)}
    evals = 0
    confs = []
    for end in range(need, n + 1):
        sig = engine.evaluate(instrument, candles[:end])
        cat = _categorize(sig)
        cnt[cat] += 1
        evals += 1
        if cat == "ENTREE":
            confs.append(sig.confidence)
    entrees = cnt.get("ENTREE", 0)
    conf = {}
    if confs:
        confs.sort()
        med = confs[len(confs) // 2]
        pct = lambda thr: round(100.0 * sum(1 for c in confs if c >= thr) / len(confs), 1)
        conf = {"min": round(min(confs), 2), "mediane": round(med, 2), "max": round(max(confs), 2),
                "pct>=0.55": pct(0.55), "pct>=0.60": pct(0.60), "pct>=0.65": pct(0.65),
                "pct>=0.70": pct(0.70), "pct>=0.75": pct(0.75)}
    return {"instrument": instrument, "bougies": n, "evaluables": evals,
            "raisons": dict(cnt.most_common()),
            "entrees": entrees, "conf_entrees": conf,
            "taux_entree_pct": round(100.0 * entrees / evals, 2) if evals else 0.0}


def _fetch(instrument):
    if "/" in instrument:                       # crypto -> Kraken
        from kraken_data import KrakenData
        return KrakenData().get_history(instrument, interval=15)
    from oanda_client import OandaClient        # forex -> OANDA
    return OandaClient(account="practice").get_candles(instrument, granularity="M15", count=500)


def _print(d):
    print("\n=== Diagnostic d'entrées : %s ===" % d["instrument"])
    print("bougies=%d  évaluables=%d  ENTRÉES=%d  (taux %.2f%%)"
          % (d["bougies"], d["evaluables"], d["entrees"], d["taux_entree_pct"]))
    print("Répartition des décisions :")
    for k, v in d["raisons"].items():
        pct = 100.0 * v / d["evaluables"] if d["evaluables"] else 0
        print("   %-26s %5d  (%5.1f%%)" % (k, v, pct))
    c = d.get("conf_entrees") or {}
    if c:
        print("Confiance des ENTRÉES : min %.2f · médiane %.2f · max %.2f" % (c["min"], c["mediane"], c["max"]))
        print("   %% d'entrées au-dessus du seuil d'auto-validation :")
        for k in ("pct>=0.55", "pct>=0.60", "pct>=0.65", "pct>=0.70", "pct>=0.75"):
            print("      confiance %s : %5.1f%%" % (k.replace("pct>=", "≥ "), c[k]))
        band70 = c["pct>=0.70"]
        print(">> Bande UI par défaut = 70–90%%. Seules ~%.0f%% des entrées atteignent 70%% -> "
              "le reste expire en auto. Si c'est faible, on baisse accept_min." % band70)
    if d["entrees"] == 0:
        worst = next((k for k in d["raisons"] if k != "ENTREE"), None)
        print(">> Aucune entrée signal. Filtre dominant : %s." % worst)


if __name__ == "__main__":
    inst = sys.argv[1] if len(sys.argv) > 1 else "EUR_USD"
    try:
        candles = _fetch(inst)
    except Exception as e:
        print("Récupération des bougies impossible (%s) :" % inst, e)
        sys.exit(1)
    _print(diagnose(candles, instrument=inst))
