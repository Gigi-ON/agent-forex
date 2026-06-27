"""
DÉMO de la boucle d'apprentissage (hors-ligne, journal SQLite réel).

  backtest  ->  enregistrement des trades (journal.db)  ->  post-mortem

Lancer :  python journal_demo.py
"""

from cache import Cache
from backtest import Backtester
from risk_manager import Profile
from journal import JournalStore, analyze
from data_demo import make_m15


def main():
    pair = "EUR_USD"

    # 1) Rejouer un backtest qui produit un journal de trades.
    candles = make_m15()
    bt = Backtester(profile=Profile.DOUX)
    res = bt.run(pair, candles, start_equity=5000.0)
    print(f"Backtest : {len(res.trade_log)} trades journalisés.\n")

    # 2) Persister chaque trade dans le journal SQLite.
    store = JournalStore(db_path="data/demo_journal.db")
    store.conn.execute("DELETE FROM trades")  # repartir propre pour la démo
    for t in res.trade_log:
        store.record(t)

    # 3) Relire depuis le journal et produire le post-mortem.
    closed = store.closed_trades()
    pm = analyze(closed)

    print("=" * 64)
    print("POST-MORTEM (lecture depuis le journal SQLite)")
    print("=" * 64)
    print(pm.summary())

    # 4) Quelques lignes du journal, pour montrer ce qui est gardé.
    print("\n" + "-" * 64)
    print("Extrait du journal (3 derniers trades)")
    print("-" * 64)
    for t in closed[-3:]:
        print(f"  {t.entry_time[:16]} | {t.pair} {t.side:>4} | "
              f"sortie {t.exit_reason:>4} | {t.outcome:>9} | {t.r_multiple:+.2f}R")

    print("\nLecture clé : l'espérance (R moyen) dit si le système est viable, "
          "bien plus que le taux de réussite seul.")
    store.close()


if __name__ == "__main__":
    main()
