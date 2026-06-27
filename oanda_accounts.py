"""
Découverte des comptes OANDA — récupère votre/vos ACCOUNT_ID à partir du
seul TOKEN. Utile quand on a la clé API mais pas l'identifiant de compte.

Pré-requis : OANDA_TOKEN et OANDA_ENV définis (voir .env). Lancer :

    ./venv/bin/python oanda_accounts.py

Le script affiche chaque compte (id, devise, solde, type) ; copiez l'id voulu
dans .env sous OANDA_ACCOUNT_ID. Aucun ordre n'est envoyé.
"""

import config


def _extract_ids(account_list_response):
    """Extrait les identifiants depuis la réponse /v3/accounts (testable)."""
    return [a["id"] for a in account_list_response.get("accounts", [])]


def main():
    if not config.OANDA_TOKEN:
        print("✗ OANDA_TOKEN manquant. Définissez-le dans .env puis relancez.")
        return

    import oandapyV20
    import oandapyV20.endpoints.accounts as accounts

    api = oandapyV20.API(access_token=config.OANDA_TOKEN,
                         environment=config.ENVIRONMENT)

    print(f"Environnement : {config.ENVIRONMENT}")
    print("Recherche des comptes rattachés à votre token…\n")

    try:
        r = accounts.AccountList()
        api.request(r)
    except Exception as e:
        print(f"✗ Échec : {e}")
        print("  Causes fréquentes : mauvais token, ou mauvais environnement "
              "(un token 'practice' ne voit que les comptes démo, et inversement).")
        return

    ids = _extract_ids(r.response)
    if not ids:
        print("Aucun compte trouvé pour ce token.")
        return

    print(f"{len(ids)} compte(s) trouvé(s) :\n")
    for acc_id in ids:
        line = f"  • {acc_id}"
        try:
            s = accounts.AccountSummary(acc_id)
            api.request(s)
            a = s.response["account"]
            line += (f"   devise {a.get('currency')}  ·  solde "
                     f"{float(a.get('balance', 0)):.2f}  ·  "
                     f"{'RÉEL' if 'fxtrade' in config.ENVIRONMENT else 'practice'}")
        except Exception:
            pass
        print(line)

    print("\n→ Copiez l'identifiant voulu dans .env :")
    print(f"   OANDA_ACCOUNT_ID={ids[0]}")
    if config.ACCOUNT_CURRENCY:
        print(f"   (vérifiez que la devise affichée correspond à "
              f"ACCOUNT_CURRENCY={config.ACCOUNT_CURRENCY})")


if __name__ == "__main__":
    main()
