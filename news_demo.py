"""
DÉMO hors-ligne de la couche news/macro (aucune connexion réseau).

Montre :
  1. le scoring "social vs officiel",
  2. le modulateur de prudence (blackout + facteur de réduction),
  3. l'intégration au moteur de risque via external_caution.

Lancer :  python news_demo.py
"""

from datetime import datetime, timedelta, timezone

from news import NewsItem, Trust, TrustScorer, RiskModulator
from risk_manager import RiskManager, TradeProposal, Profile

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)


def mock_items(scenario: str):
    """Construit un jeu d'actualités fictif selon le scénario."""
    if scenario == "calme":
        return [
            NewsItem("Reuters", Trust.MAJOR_MEDIA, "Marchés FX stables avant la séance US",
                     NOW - timedelta(hours=2), region="global", currencies=["EUR", "USD"],
                     impact="low"),
            NewsItem("Bluesky", Trust.SOCIAL_TREND, "EUR/USD va exploser selon un thread",
                     NOW - timedelta(minutes=30), currencies=["EUR", "USD"], impact="low"),
        ]
    if scenario == "evenement_bce":
        return [
            NewsItem("BCE / ECB", Trust.OFFICIAL, "Décision de taux BCE",
                     NOW - timedelta(hours=1), region="europe", currencies=["EUR"],
                     impact="high", is_event=True,
                     event_time=NOW + timedelta(minutes=20)),  # dans 20 min -> blackout
            NewsItem("Reuters", Trust.MAJOR_MEDIA, "Les marchés attendent la BCE",
                     NOW - timedelta(minutes=45), currencies=["EUR", "USD"], impact="medium"),
        ]
    if scenario == "evenement_lointain":
        return [
            NewsItem("US Federal Reserve / FRED", Trust.OFFICIAL, "Emploi US (NFP)",
                     NOW - timedelta(hours=3), region="north_america", currencies=["USD"],
                     impact="high", is_event=True,
                     event_time=NOW + timedelta(hours=5)),  # dans 5h -> prudence, pas blackout
        ]
    return []


def run_scenario(name: str):
    items = mock_items(name)
    print("\n" + "=" * 60)
    print(f"SCÉNARIO : {name}")
    print("=" * 60)

    # 1) Scoring social vs officiel
    rate = TrustScorer().corroboration_rate(items)
    print(f"  Taux de corroboration des rumeurs sociales : {rate}")

    # 2) Modulateur de prudence pour EUR_USD
    decision = RiskModulator().assess(items, "EUR_USD", NOW)
    print(f"  Blackout EUR_USD : {decision.blackout}")
    print(f"  Facteur de prudence : {decision.caution_factor}")
    for r in decision.reasons:
        print(f"    · {r}")

    # 3) Intégration au moteur de risque (proposition fictive d'achat)
    proposal = TradeProposal("EUR_USD", "buy", 1.0850, 1.0820, 1.0920)
    if decision.blackout:
        print("  -> Trade BLOQUÉ par la couche news (pas de nouvelle position).")
        return
    rm = RiskManager(profile=Profile.DOUX)
    res = rm.size_position(
        proposal=proposal,
        equity_account_ccy=5000.0,
        quote_to_account_rate=1.36,
        base_to_account_rate=1.47,
        external_caution=decision.caution_factor,
    )
    print(f"  -> Taille : {res.units} unités | risque {res.risk_amount_account_ccy} CAD "
          f"| levier {res.effective_leverage}x")


if __name__ == "__main__":
    print("Compte fictif 5000 CAD — la couche news ne fait que RÉDUIRE le risque.")
    for sc in ("calme", "evenement_lointain", "evenement_bce"):
        run_scenario(sc)
