"""
Recherche d'unité de temps — même logique de signal, terrain moins bruité.
Télécharge H1 puis H4 pour EUR_USD et lance le test de robustesse sur chaque.
AUCUNE optimisation de paramètres : on teste une hypothèse, on accepte le verdict.

    ./venv/bin/python research_timeframe.py
"""
from datetime import datetime, timedelta, timezone

from oanda_data import OandaData, rfc3339
from backtest import robustness_report

PAIR = "EUR_USD"
# granularité -> profondeur d'historique (jours). Plus c'est lent, plus on a de trades.
PLAN = {"H1": 365, "H4": 730}


def main():
    od = OandaData()
    now = datetime.now(timezone.utc)
    for gran, days in PLAN.items():
        start = rfc3339(now - timedelta(days=days))
        print(f"\n# Téléchargement {PAIR} {gran} (~{days} jours)…")
        try:
            od.fetch_history(PAIR, gran, start=start)
        except Exception as e:
            print(f"  échec du téléchargement : {e}")
            continue
        candles = od.cache.get_candles(PAIR, gran)
        print(f"===== {PAIR} {gran} ({len(candles)} bougies) =====")
        if len(candles) < 200:
            print("  (peu de bougies : verdict statistiquement faible)")
        print(robustness_report(PAIR, candles))

    print("\nRappel : un edge n'est crédible que s'il reste positif sous ×2 "
          "friction ET sur les trois segments. Sinon, on n'y croit pas.")


if __name__ == "__main__":
    main()
