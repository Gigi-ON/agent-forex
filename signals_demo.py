"""
DÉMO hors-ligne du pipeline complet (aucune connexion réseau) :

    bougies  ->  SignalEngine (propose)  ->  RiskManager (dispose)

Lancer :  python signals_demo.py
"""

import random

from signals import SignalEngine
from risk_manager import RiskManager, Profile


def make_candles(n=150, start=1.0800, drift=0.00015, noise=0.0010, seed=7):
    """Génère une série OHLC synthétique avec une tendance haussière douce."""
    rng = random.Random(seed)
    candles = []
    close = start
    for _ in range(n):
        o = close
        close = o + drift + rng.uniform(-noise, noise)
        hi = max(o, close) + rng.uniform(0, noise)
        lo = min(o, close) - rng.uniform(0, noise)
        candles.append({"o": o, "h": hi, "l": lo, "c": close})
    return candles


def run():
    # 1) Données simulées (tendance haussière -> on s'attend à un signal buy)
    candles = make_candles()

    # 2) Couche signaux : PROPOSE
    engine = SignalEngine()
    sig = engine.evaluate("EUR_USD", candles)

    print("=" * 60)
    print("SIGNAL")
    print("=" * 60)
    for note in sig.notes:
        print(f"  · {note}")
    if sig.proposal is None:
        print("  -> Aucun trade proposé.")
        return
    p = sig.proposal
    print(f"  -> {p.side.upper()} {p.instrument}  (confiance {sig.confidence})")
    print(f"     entrée={p.entry_price}  stop={p.stop_loss}  objectif={p.take_profit}")

    # 3) Couche risque : DISPOSE (mêmes hypothèses que demo.py)
    rm = RiskManager(profile=Profile.DOUX)
    res = rm.size_position(
        proposal=p,
        equity_account_ccy=5000.0,
        quote_to_account_rate=1.36,   # 1 USD = 1.36 CAD (exemple figé)
        base_to_account_rate=1.47,    # 1 EUR = 1.47 CAD (exemple figé)
    )

    print("\n" + "=" * 60)
    print("DÉCISION DU MOTEUR DE RISQUE (profil DOUX)")
    print("=" * 60)
    print(f"  accepté        : {res.accepted}")
    print(f"  unités         : {res.units}")
    print(f"  risque réel    : {res.risk_amount_account_ccy} CAD")
    print(f"  levier effectif: {res.effective_leverage}x")
    print(f"  ratio gain/risq: {res.reward_risk_ratio}")
    for reason in res.reasons:
        print(f"  ↪ {reason}")

    if res.accepted:
        print("\n  Étape suivante (verrouillée) : oanda_client.place_market_order(...)")
        print("  -> en mode practice, aucun ordre réel n'est envoyé.")


if __name__ == "__main__":
    run()
