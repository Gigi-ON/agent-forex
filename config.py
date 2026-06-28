"""
Configuration centrale du projet.

RÈGLE DE SÉCURITÉ : le token OANDA n'est JAMAIS écrit en dur dans le code.
On le lit depuis des variables d'environnement. Avant de lancer le projet :

    export OANDA_TOKEN="votre-token-de-compte-PRACTICE"
    export OANDA_ACCOUNT_ID="101-002-xxxxxxx-001"

Par défaut on est en mode PRACTICE (compte démo, argent fictif).
Passer en réel demande un geste volontaire et explicite (voir LIVE_TRADING).
"""

import os

# Chargement optionnel d'un fichier .env en développement local.
# En production (Hostinger), on définit plutôt les variables directement
# dans l'environnement du serveur ; ce bloc est alors sans effet.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Connexion OANDA -------------------------------------------------------
OANDA_TOKEN = os.environ.get("OANDA_TOKEN", "")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")

# practice = compte démo (argent fictif). On reste ici par défaut.
# live = argent réel. Ne JAMAIS activer avant des semaines de simulation.
ENVIRONMENT = os.environ.get("OANDA_ENV", "practice")  # "practice" ou "live"

# Garde-fou supplémentaire : même en env "live", aucun ordre réel n'est
# envoyé tant que cette variable n'est pas mise explicitement à True.
LIVE_TRADING = os.environ.get("OANDA_LIVE_TRADING", "false").lower() == "true"

# --- Compte ----------------------------------------------------------------
# Devise du compte. Vous êtes au Québec -> probablement CAD, mais vérifiez
# dans votre compte OANDA. Sert aux conversions de risque.
ACCOUNT_CURRENCY = os.environ.get("ACCOUNT_CURRENCY", "CAD")

# --- Instruments suivis ----------------------------------------------------
# Format OANDA : "EUR_USD", "EUR_CAD" (underscore, pas slash).
INSTRUMENTS = ["EUR_USD", "EUR_CAD"]

# --- Garde-fous globaux (plafonds DURS, non négociables) -------------------
# Ces limites priment toujours sur le profil de risque choisi.
HARD_LIMITS = {
    # Risque maximum absolu par trade, en % du capital. Même en "agressif"
    # on ne dépasse jamais ça.
    "max_risk_per_trade_pct": 2.0,
    # Perte cumulée maximale sur une journée. Au-delà, le bot coupe tout.
    "max_daily_loss_pct": 4.0,
    # Levier effectif maximum toléré sur une position.
    "max_effective_leverage": 10.0,
    # Nombre maximum de positions ouvertes simultanément.
    "max_open_positions": 3,
    # Ratio minimum gain potentiel / perte potentielle pour accepter un trade.
    "min_reward_risk_ratio": 1.5,
}

# --- Kraken (crypto) -------------------------------------------------------
# Les COURS utilisent l'API publique Kraken (AUCUNE clé requise). Les clés ne
# servent qu'à l'exécution future (après gate GO + double auth).
KRAKEN_API_KEY = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
CRYPTO_INSTRUMENTS = ["BTC/USD", "ETH/USD"]
# affichage -> (paire Kraken pour la requête, code de base pour le matching réponse)
KRAKEN_PAIRS = {"BTC/USD": ("XBTUSD", "XBT"), "ETH/USD": ("ETHUSD", "ETH")}
