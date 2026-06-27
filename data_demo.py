"""
DÉMO hors-ligne de la couche données (le cache SQLite tourne réellement).

  bougies synthétiques  ->  cache SQLite  ->  relecture  ->  backtest

Lancer :  python data_demo.py
"""

import random
from datetime import datetime, timedelta, timezone

from cache import Cache
from backtest import Backtester
from risk_manager import Profile


def make_m15(n=1500, start=1.0800, drift=0.00002, noise=0.00045, seed=11):
    """Bougies M15 synthétiques avec une légère tendance et du bruit."""
    rng = random.Random(seed)
    t = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles, close = [], start
    for _ in range(n):
        o = close
        close = o + drift + rng.uniform(-noise, noise)
        hi = max(o, close) + rng.uniform(0, noise)
        lo = min(o, close) - rng.uniform(0, noise)
        candles.append({
            "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "o": round(o, 5), "h": round(hi, 5),
            "l": round(lo, 5), "c": round(close, 5),
        })
        t += timedelta(minutes=15)
    return candles


def main():
    pair, gran = "EUR_USD", "M15"
    cache = Cache(db_path="data/demo_market.db")

    # 1) Générer et stocker dans le cache (simule un téléchargement OANDA).
    candles = make_m15()
    n = cache.upsert_candles(pair, gran, candles)
    print(f"Cache : {n} bougies {pair} {gran} stockées dans SQLite.")

    # 2) Relire depuis le cache (ce que fera get_history en réel).
    reread = cache.get_candles(pair, gran)
    print(f"Relecture depuis le cache : {len(reread)} bougies.")
    print(f"Période : {reread[0]['time']}  ->  {reread[-1]['time']}")

    # 3) Backtest via les vraies couches signaux + risque.
    print("\n" + "=" * 60)
    print("BACKTEST (profil DOUX, spread ~0.8 pip, compte 5000 CAD)")
    print("=" * 60)
    bt = Backtester(profile=Profile.DOUX)
    result = bt.run(pair, reread, start_equity=5000.0)
    print(result.summary())

    print("\nRappel : backtest indicatif, pas une promesse. Résultats sur "
          "données synthétiques — à refaire sur de vraies bougies OANDA.")
    cache.close()


if __name__ == "__main__":
    main()
