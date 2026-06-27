# Déploiement sur VPS Hostinger

Modèle inspiré de la vidéo « Claude 24/7 trader », mais adapté au forex et à
notre architecture : ce sont nos **routines déterministes** qui tournent sur
un planning cron, pas un LLM qui décide seul. Tout reste en **mode practice**
tant que `OANDA_LIVE_TRADING` n'est pas activé.

---

## 1. Préparer le serveur (utilisateur dédié + installateur)

Bonne pratique : ne pas faire tourner l'app en root. On crée un utilisateur
applicatif, puis on laisse l'installateur faire le reste.

```bash
# (a) En root, UNE fois : créer l'utilisateur applicatif
ssh root@votre-vps-hostinger
adduser forex
usermod -aG sudo forex          # pour les rares commandes système

# (b) Basculer sur cet utilisateur et récupérer le projet
su - forex
git clone <votre-repo> agent-forex && cd agent-forex

# (c) Lancer l'installateur : paquets système, venv, dépendances,
#     fichier .env, et vérification hors-ligne — en une commande
bash install.sh
```

L'installateur n'utilise `sudo` que pour les paquets système (jamais pour
`pip`, qui reste dans le venv), ne touche pas à un `.env` existant, et peut
être relancé sans risque. Pour planifier les routines en même temps :
`bash install.sh --cron`.

## 2. Variables d'environnement (les secrets, jamais dans le code)

Créez `/root/forex_agent/.env` (déjà ignoré par git) :

```
OANDA_TOKEN=...le token practice...
OANDA_ACCOUNT_ID=101-002-xxxxxxx-001
OANDA_ENV=practice
OANDA_LIVE_TRADING=false
ACCOUNT_CURRENCY=CAD
FINNHUB_KEY=...optionnel...
```

`config.py` charge ce fichier automatiquement (python-dotenv). En production,
vous pouvez aussi définir ces variables directement dans le panneau Hostinger.

## 3. Premier démarrage OANDA (vérifier + télécharger)

Vous n'avez que la clé API et pas l'ID de compte ? Récupérez-le directement depuis le token :

```bash
./venv/bin/python oanda_accounts.py
```

Le script liste vos comptes (id, devise, solde) ; copiez l'id dans `.env` sous `OANDA_ACCOUNT_ID`. Ensuite seulement :

Une fois les variables définies, validez la connexion et remplissez le cache :

```bash
./venv/bin/python oanda_bootstrap.py
```

Ce script affiche votre compte (solde, devise), télécharge ~90 jours de
bougies M15 dans le cache SQLite, montre les conversions CAD en direct, et
évalue un signal — **sans envoyer aucun ordre**. S'il échoue, c'est presque
toujours un problème de token, d'account id, ou d'environnement (practice vs
live). Lancez `python test_oanda_mock.py` pour vérifier la logique hors-ligne.

## 4. Planifier les routines (cron, en heures UTC)

Le forex ouvre du dimanche 22:00 UTC au vendredi 22:00 UTC. On cible les
sessions Londres + New York (les plus liquides) et les bilans en fin de
journée. `crontab -e` :

```cron
# Charge l'environnement puis lance la routine. Cron n'a pas votre shell,
# d'où le chargement explicite du venv et du .env.
SHELL=/bin/bash
WRAP=cd /root/forex_agent && set -a && . ./.env && set +a && ./venv/bin/python

# Recherche de session : aux ouvertures Tokyo / Londres / New York (lun-ven)
0 0,7,13 * * 1-5   bash -lc "$WRAP run_routine.py session_research"

# Scan d'exécution : toutes les 15 min pendant Londres+NY (06h-21h UTC)
*/15 6-21 * * 1-5  bash -lc "$WRAP run_routine.py execution_scan"

# Surveillance (coupe-circuit) : toutes les 5 min pendant les sessions
*/5 6-21 * * 1-5   bash -lc "$WRAP run_routine.py monitor"

# Bilan de fin de journée : après la clôture NY (21:30 UTC)
30 21 * * 1-5      bash -lc "$WRAP run_routine.py end_of_day"

# Revue hebdomadaire : vendredi après clôture
35 21 * * 5        bash -lc "$WRAP run_routine.py weekly_review"
```

## 4. Notifications (optionnel)

Pour recevoir les récaps sur Slack/Discord/Telegram, ajoutez une variable
`RECAP_WEBHOOK` et passez-la à `recap.send(..., webhook_url=...)`. Sinon les
récaps s'écrivent dans `data/recaps.log`.

---

---

## Mode maîtrise du marché (forward-test 30 jours)

Avant tout argent réel, on fait tourner le bot en **practice** pendant ~30
jours : cours réels OANDA, exécutions simulées, capital fictif. Les routines
cron ci-dessus font déjà le travail ; il suffit de laisser `OANDA_ENV=practice`
et de laisser le journal s'accumuler un mois.

Au bout des 30 jours, on demande le verdict :

```bash
./venv/bin/python -c "from journal import JournalStore; \
from market_mastery import evaluate; \
s=JournalStore(); print(evaluate(s.closed_trades(), []).summary())"
```

Le verdict est GO seulement si TOUT est vrai : assez de trades, espérance
positive, majorité de semaines positives, drawdown maîtrisé. Sinon NO-GO —
et un NO-GO est un succès du processus : il évite de risquer du réel sur un
système sans avantage prouvé. Même un GO ne justifie qu'un passage au réel à
**toute petite taille**.

---

## Garde-fous de déploiement

- **Practice d'abord, longtemps.** Laissez `OANDA_LIVE_TRADING=false` jusqu'à
  ce que le journal + post-mortem montrent une espérance positive sur des
  semaines de données réelles. Le passage en réel doit être un geste manuel,
  conscient et réversible.
- **Le LLM ne calcule rien.** Tailles, stops et filtres sont en Python
  déterministe. Un LLM ne sert (plus tard) qu'à synthétiser du contexte.
- **Les routines sont idempotentes et sans état caché** : si une exécution
  cron échoue, la suivante repart proprement.
- **Surveillez `data/recaps.log`** les premiers jours pour vérifier le
  comportement avant d'envisager quoi que ce soit de réel.
