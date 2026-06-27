"""
DÉMO accélérée du mode maîtrise du marché (hors-ligne).

On simule ~30 jours de M15 (les cours sont ici synthétiques ; en production
ce sont les VRAIS prix du compte démo OANDA) et on produit le verdict.

Lancer :  python mastery_demo.py
"""

from datetime import datetime, timezone

from backtest import Backtester
from risk_manager import Profile
from market_mastery import MasteryCampaign, evaluate
from data_demo import make_m15


def main():
    # ~30 jours de M15 ≈ 30 × 96 bougies (week-ends ignorés pour la démo).
    candles = make_m15(n=2880)

    camp = MasteryCampaign(virtual_capital=5000.0, days=30,
                           started=datetime(2026, 5, 1, tzinfo=timezone.utc))
    print("=" * 64)
    print("MODE MAÎTRISE DU MARCHÉ — forward-test 30 jours")
    print("Cours réels (ici simulés) · capital fictif 5000 $ · zéro risque réel")
    print("=" * 64)

    res = Backtester(profile=Profile.DOUX).run("EUR_USD", candles,
                                               start_equity=camp.virtual_capital)
    print(f"\nTrades sur la période : {len(res.trade_log)}")
    print(f"Équity finale (fictive) : {res.end_equity} $\n")

    verdict = evaluate(res.trade_log, res.equity_curve)
    print(verdict.summary())

    print("\n" + "-" * 64)
    print("Rappel : un NO-GO est un SUCCÈS du processus — il vous évite de "
          "risquer de l'argent réel sur un système sans avantage prouvé.")


if __name__ == "__main__":
    main()
