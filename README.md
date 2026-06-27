# Agent de trading forex OANDA — fondations prudentes

Projet d'agent de trading forex (OANDA) construit **autour de la gestion du
risque**, pas de la prédiction. L'objectif n'est pas « ne jamais perdre »
(impossible), mais **survivre** : petites pertes contrôlées, pas d'explosion
de compte.

---

## 1. Installation

```bash
python -m venv venv
source venv/bin/activate        # Windows : venv\Scripts\activate
pip install -r requirements.txt
```

Créez un **compte démo (practice)** OANDA, générez un token d'API, puis :

```bash
export OANDA_TOKEN="votre-token-practice"
export OANDA_ACCOUNT_ID="101-002-xxxxxxx-001"
export ACCOUNT_CURRENCY="CAD"     # vérifiez la devise de votre compte
# on reste en practice par défaut ; ne touchez pas à OANDA_LIVE_TRADING
```

Testez le moteur de risque sans connexion :

```bash
python demo.py
```

---

## 2. Architecture (état actuel)

| Fichier | Rôle | Statut |
|---|---|---|
| `config.py` | Configuration + garde-fous durs | ✅ |
| `risk_manager.py` | **Cœur** : sizing, profils, plafonds, volatilité | ✅ |
| `oanda_client.py` | Données + exécution (verrouillée par défaut) | ✅ |
| `indicators.py` | ATR (volatilité) | ✅ |
| `demo.py` | Démo hors-ligne du sizing | ✅ |
| `signals.py` | Détection de configurations (propose, ne décide pas) | à venir |
| `dashboard.py` | Visualisation + journal de trades | à venir |

**Principe de séparation** : la couche analyse *propose*, la couche risque
*dispose*. Aucune position ne passe sans stop-loss, sans ratio gain/risque
minimum, et sans respecter les plafonds durs.

---

## 3. Les profils de risque

Les profils ne changent QUE le pourcentage de capital risqué par trade :

| Profil | Risque / trade |
|---|---|
| Réservé | 0,5 % |
| Doux | 1,0 % |
| Agressif | 1,5 % |

Plafond dur absolu : 2 % par trade, 4 % de perte par jour (coupe-circuit),
levier effectif max 10x. Ces limites priment toujours sur le profil.

**Important** : « agressif » ne veut PAS dire « plus de levier quand ça a
l'air opportun ». Ça veut dire un peu plus de risque par trade, dans des
limites strictes. La volatilité élevée réduit automatiquement la taille.

---

## 4. Mini-formation : forex prudent

Quelques principes qui comptent plus que n'importe quelle stratégie :

1. **Le risque d'abord, le gain ensuite.** On décide combien on accepte de
   perdre AVANT d'entrer. La taille de position en découle. Jamais l'inverse.

2. **Stop-loss systématique.** Chaque trade a un stop défini à l'avance.
   Un trade sans stop est un pari, pas une position.

3. **Risquer petit.** 0,5 à 1 % du capital par trade. À 1 %, il faut une
   série de pertes très longue pour faire mal. À 10 %, quelques trades
   suffisent à détruire le compte.

4. **Le levier est un amplificateur, dans les deux sens.** Il multiplie les
   gains ET les pertes. Un levier élevé sur un compte retail est la première
   cause de liquidation. Restez bas.

5. **Le ratio gain/risque.** Visez au moins 1,5:1. Ça permet d'être perdant
   sur la majorité des trades tout en restant globalement positif.

6. **La volatilité n'est pas une opportunité, c'est un risque.** Quand le
   marché s'agite (news, ouvertures), on réduit, on ne charge pas.

7. **Aucune IA ne prédit le marché de façon fiable.** Méfiez-vous de tout
   système qui promet le contraire — surtout d'un backtest trop beau
   (sur-apprentissage). L'IA aide à mesurer, organiser et discipliner ;
   elle ne devine pas l'avenir.

8. **Simuler longtemps.** Des semaines en compte démo avant le moindre euro
   réel. Le verrou `LIVE_TRADING` est là pour vous y obliger.

---

## 5. Avertissement

Ce code est un outil pédagogique et technique. Il ne constitue pas un conseil
financier. Le trading sur effet de levier comporte un risque élevé de perte,
pouvant dépasser le capital investi. Les décisions restent les vôtres.

---

## Interfaces web (`web/`)

| Fichier | Rôle |
|---|---|
| `web/dashboard.html` | **Application principale** : connexion Supabase, gestion utilisateur, vue Aperçu (monitoring) + vue Bot sous tutelle, sélecteur de mode Pratique / Apprentissage / Réel, bandeau de statut du verrou. |
| `web/bot_sous_tutelle.html` | Vue autonome du bot (sessions, validations 45 s, rapports, apprentissage). |
| `web/agent_forex_app.html` | Variante consolidée (Aperçu + Bot + connexion). |

Le frontend est statique (HTML/JS) : il se sert depuis n'importe quel serveur.
Le backend (bot, OANDA, cron) est en **Python** et nécessite un **VPS**
(accès root). Voir `DEPLOIEMENT_HOSTINGER.md`.

## Modes

- **Pratique** — exploration libre, capital fictif (compte démo OANDA).
- **Apprentissage** — forward-test de 30 jours (`market_mastery.py`), cours
  réels, capital fictif, verdict go/no-go avant tout argent réel.
- **Réel** — argent en jeu. Doublement verrouillé : `OANDA_LIVE_TRADING=true`
  **et** compte OANDA réel. Le mode de l'interface ne lève pas ce verrou.

## Backend partagé (Supabase)

Projet dédié `agent-forex` (région ca-central-1). Tables `sessions` et `trades`
avec sécurité au niveau des lignes (chaque utilisateur ne voit que ses données).
La clé *publishable* dans le frontend est publique par conception ; la clé
secrète ne quitte jamais le serveur.

## Sécurité — rappel

`OANDA_LIVE_TRADING=false` et compte **practice** tant que la campagne
d'apprentissage n'a pas rendu un verdict positif. Un NO-GO est un succès du
processus. Aucun secret dans le dépôt (voir `.gitignore`).
