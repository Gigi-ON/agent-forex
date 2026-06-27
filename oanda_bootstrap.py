"""
Premier démarrage réel OANDA — à lancer SUR LE VPS avec votre clé.

Pré-requis (variables d'environnement, jamais en dur) :
    export OANDA_TOKEN="votre-token"
    export OANDA_ACCOUNT_ID="101-002-xxxxxxx-001"
    export OANDA_ENV="practice"        # on commence en démo
    export ACCOUNT_CURRENCY="CAD"

Ce script :
  1. vérifie les identifiants et affiche le compte (solde, devise),
  2. télécharge ~90 jours de bougies M15 dans le cache (paginé),
  3. calcule les conversions CAD en direct,
  4. évalue un signal sur les dernières bougies (sans rien exécuter).

Aucun ordre n'est envoyé. Lancer :  python oanda_bootstrap.py
"""

from datetime import datetime, timedelta, timezone

import config
from oanda_data import OandaData, rfc3339
from signals import SignalEngine


def main():
    print("=" * 60)
    print(f"OANDA — environnement : {config.ENVIRONMENT}")
    print("=" * 60)

    od = OandaData()

    # 1) Compte
    try:
        acct = od.get_account_summary()
    except Exception as e:
        print(f"✗ Connexion impossible : {e}")
        print("  Vérifiez OANDA_TOKEN / OANDA_ACCOUNT_ID / OANDA_ENV.")
        return
    print(f"Compte {acct['id']} · solde {acct['balance']:.2f} {acct['currency']} "
          f"· NAV {acct['nav']:.2f} · {acct['open_trades']} position(s) ouverte(s)")
    if acct["currency"] != config.ACCOUNT_CURRENCY:
        print(f"⚠ La devise du compte ({acct['currency']}) diffère de "
              f"ACCOUNT_CURRENCY ({config.ACCOUNT_CURRENCY}). Ajustez la config.")

    start = rfc3339(datetime.now(timezone.utc) - timedelta(days=90))

    # 2-4) Pour chaque instrument suivi
    engine = SignalEngine()
    for pair in config.INSTRUMENTS:
        print(f"\n— {pair} —")
        n = od.fetch_history(pair, "M15", start=start)
        total = len(od.cache.get_candles(pair, "M15"))
        print(f"  {n} bougies téléchargées · {total} en cache")

        q2a, b2a = od.conversion_rates(pair)
        print(f"  conversions : 1 cotation = {q2a:.4f} {config.ACCOUNT_CURRENCY} · "
              f"1 base = {b2a:.4f} {config.ACCOUNT_CURRENCY}")

        candles = od.cache.get_candles(pair, "M15")
        sig = engine.evaluate(pair, candles)
        if sig.proposal:
            p = sig.proposal
            print(f"  signal : {p.side} (conf {sig.confidence}) "
                  f"entrée {p.entry_price} stop {p.stop_loss} obj {p.take_profit}")
        else:
            print(f"  signal : aucun ({sig.notes[-1]})")

    print("\nTéléchargement terminé. Étape suivante : lancer le test de "
          "robustesse et la campagne de maîtrise sur ces vraies données.")
    od.cache.close()


if __name__ == "__main__":
    main()
