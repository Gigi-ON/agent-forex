"""
DÉMO hors-ligne de l'orchestrateur (aucun réseau).

Exerce la chaîne complète d'une routine :
  recherche de session -> scan d'exécution -> récap quotidien

Lancer :  python routines_demo.py
"""

from datetime import datetime, timezone

from routines import TradingDay
from risk_manager import Profile
from journal import JournalStore
from news import NewsItem, Trust
from backtest import Backtester
from data_demo import make_m15
import recap

NOW = datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc)  # chevauchement Londres/NY


def main():
    # Journal pré-rempli via un backtest (pour un post-mortem non vide).
    candles = make_m15()
    store = JournalStore(db_path="data/demo_routines.db")
    store.conn.execute("DELETE FROM trades")
    for t in Backtester(profile=Profile.DOUX).run("EUR_USD", candles).trade_log:
        store.record(t)

    # News fictives : un événement BCE imminent -> blackout EUR_USD.
    news_items = [
        NewsItem("BCE / ECB", Trust.OFFICIAL, "Décision de taux BCE",
                 NOW, currencies=["EUR"], impact="high", is_event=True,
                 event_time=NOW.replace(minute=20)),
    ]

    day = TradingDay(profile=Profile.RESERVE, journal_store=store)

    # 1) Posture de session
    posture = day.session_research(news_items, regime="haussier", now=NOW)

    # 2) Scan d'exécution sur les deux paires
    intents = []
    for pair in ("EUR_USD", "EUR_CAD"):
        q2a = 1.36 if pair == "EUR_USD" else 1.00
        intents.append(day.execution_scan(
            pair, candles, news_items, equity=5000.0,
            quote_to_account=q2a, base_to_account=1.47, now=NOW))

    # 3) Récap quotidien (posture + décisions + post-mortem)
    text = recap.compose_daily(posture, day.end_of_day(), intents)
    recap.send(text)
    store.close()


if __name__ == "__main__":
    main()
