"""
Point d'entrée appelé par cron (voir DEPLOIEMENT_HOSTINGER.md).

Usage :
    python run_routine.py session_research
    python run_routine.py execution_scan
    python run_routine.py monitor
    python run_routine.py end_of_day
    python run_routine.py weekly_review

En prod, ce script câble les vrais fournisseurs (OANDA, news, journal).
Il reste en MODE PRACTICE : aucune routine ne place d'ordre réel tant que
config.LIVE_TRADING n'est pas explicitement activé. Les routines ne font
qu'enchaîner notre moteur déterministe et journaliser/notifier.
"""

import sys

import config
from risk_manager import Profile
from journal import JournalStore
from routines import TradingDay
import recap

INSTRUMENTS = config.INSTRUMENTS
PROFILE = Profile.RESERVE   # prudent par défaut en exécution planifiée


def _providers():
    """Câble les fournisseurs réels. Importés ici pour éviter le réseau hors-ligne."""
    from oanda_data import OandaData
    from frankfurter import FrankfurterData
    cache_data = OandaData()
    return cache_data, FrankfurterData()


def main():
    if len(sys.argv) < 2:
        print("Usage : python run_routine.py <routine>")
        sys.exit(1)
    routine = sys.argv[1]

    store = JournalStore()
    day = TradingDay(profile=PROFILE, journal_store=store)

    if routine in ("end_of_day", "weekly_review"):
        summary = day.weekly_review() if routine == "weekly_review" else day.end_of_day()
        recap.send(summary, to_file="data/recaps.log")
        return

    # routines nécessitant des données de marché -> fournisseurs réels
    oanda, frank = _providers()
    # news réelles à brancher (NewsAggregator) ; vide pour l'instant
    news_items = []

    if routine == "session_research":
        regime = frank.regime("EUR_USD", start="2020-01-01")
        posture = day.session_research(news_items, regime=regime)
        recap.send(recap.compose_daily(posture, "(pas de post-mortem ici)"),
                   to_file="data/recaps.log")

    elif routine == "execution_scan":
        equity = oanda.get_equity() if hasattr(oanda, "get_equity") else 5000.0
        intents = []
        for pair in INSTRUMENTS:
            candles = oanda.get_history(pair, "M15")
            q2a, b2a = oanda.conversion_rates(pair)
            intents.append(day.execution_scan(
                pair, candles, news_items, equity, q2a, b2a))
        posture = day.session_research(news_items)
        recap.send(recap.compose_daily(posture, day.end_of_day(), intents),
                   to_file="data/recaps.log")

    elif routine == "monitor":
        equity = 5000.0
        print(day.monitor(equity))

    else:
        print(f"Routine inconnue : {routine}")
        sys.exit(1)


if __name__ == "__main__":
    main()
