"""
DÉMO hors-ligne (aucune connexion réseau).

Montre comment le moteur de risque dimensionne une position pour EUR_USD
et EUR_CAD, selon les 3 profils, et comment la volatilité réduit la taille.

Lancer :  python demo.py
"""

from risk_manager import RiskManager, TradeProposal, Profile, PROFILE_RISK_PCT

# --- Hypothèses de démo ----------------------------------------------------
# Compte fictif de 5 000 CAD.
EQUITY = 5000.0
ACCOUNT_CCY = "CAD"

# Taux de conversion (EXEMPLES figés pour la démo ; en réel ils viennent
# de l'API OANDA en direct).
RATES = {
    # EUR_USD : cotation = USD. 1 USD = 1.36 CAD ; base = EUR, 1 EUR = 1.47 CAD
    "EUR_USD": {"quote_to_account": 1.36, "base_to_account": 1.47},
    # EUR_CAD : cotation = CAD (= devise du compte). 1 CAD = 1 CAD ;
    #           base = EUR, 1 EUR = 1.47 CAD
    "EUR_CAD": {"quote_to_account": 1.00, "base_to_account": 1.47},
}

# Propositions de trade fictives (entrée / stop / objectif).
PROPOSALS = [
    TradeProposal("EUR_USD", "buy", entry_price=1.0850,
                  stop_loss=1.0820, take_profit=1.0920),   # RR = 70/30 ≈ 2.33
    TradeProposal("EUR_CAD", "buy", entry_price=1.4700,
                  stop_loss=1.4650, take_profit=1.4820),   # RR = 120/50 = 2.4
]


def show(profile: Profile, vol_spike: bool):
    rm = RiskManager(profile=profile)
    label = "VOLATILITÉ ÉLEVÉE" if vol_spike else "volatilité normale"
    print(f"\n=== Profil {profile.value.upper()} "
          f"({PROFILE_RISK_PCT[profile]}% risqué/trade) — {label} ===")

    for p in PROPOSALS:
        r = RATES[p.instrument]
        # En cas de pic : ATR courant 2x la moyenne -> facteur 0.5
        cur_atr, avg_atr = (0.0020, 0.0010) if vol_spike else (0.0010, 0.0010)

        res = rm.size_position(
            proposal=p,
            equity_account_ccy=EQUITY,
            quote_to_account_rate=r["quote_to_account"],
            base_to_account_rate=r["base_to_account"],
            current_atr=cur_atr,
            average_atr=avg_atr,
        )

        status = "✅ ACCEPTÉ" if res.accepted else "❌ REFUSÉ"
        print(f"  {p.instrument} {p.side:>4} | {status}")
        print(f"     unités        : {res.units:>8}")
        print(f"     risque réel   : {res.risk_amount_account_ccy:>8.2f} {ACCOUNT_CCY}")
        print(f"     levier effectif: {res.effective_leverage:>7.2f}x")
        print(f"     ratio gain/risq: {res.reward_risk_ratio:>7.2f}")
        if res.reasons:
            for reason in res.reasons:
                print(f"     ↪ {reason}")


if __name__ == "__main__":
    print("=" * 60)
    print(f"DÉMO — compte fictif {EQUITY:.0f} {ACCOUNT_CCY} (aucun ordre réel)")
    print("=" * 60)
    for prof in (Profile.RESERVE, Profile.DOUX, Profile.AGRESSIF):
        show(prof, vol_spike=False)
    # Effet de la volatilité : même profil agressif, taille réduite.
    show(Profile.AGRESSIF, vol_spike=True)
    print("\nNote : en volatilité élevée, la taille baisse automatiquement.")
