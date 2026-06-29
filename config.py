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
    "max_open_positions": 9,
    # Ratio minimum gain potentiel / perte potentielle pour accepter un trade.
    "min_reward_risk_ratio": 1.5,
}

# --- Comptes OANDA (practice / live) ---------------------------------------
# Le compte "practice" reprend les variables existantes (aucun changement requis
# au .env actuel). Le compte "live" n'est utilisé qu'au mode Réel armé.
ACCOUNTS = {
    "practice": {"token": OANDA_TOKEN, "account_id": OANDA_ACCOUNT_ID, "env": "practice"},
    "live": {"token": os.environ.get("OANDA_LIVE_TOKEN", ""),
             "account_id": os.environ.get("OANDA_LIVE_ACCOUNT_ID", ""), "env": "live"},
}

# --- Kraken (crypto) -------------------------------------------------------
# Les COURS utilisent l'API publique Kraken (AUCUNE clé requise). Les clés ne
# servent qu'à l'exécution future (après gate GO + double auth).
KRAKEN_API_KEY = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
CRYPTO_INSTRUMENTS = ["BTC/USD", "ETH/USD"]
# affichage -> (paire Kraken pour la requête, code de base pour le matching réponse)
KRAKEN_PAIRS = {"BTC/USD": ("XBTUSD", "XBT"), "ETH/USD": ("ETHUSD", "ETH")}

# Ordre de priorité d'affichage (la liste réelle = intersection avec le courtier).
FOREX_PRIORITY = [
    "EUR/USD","USD/JPY","GBP/USD","USD/CHF","AUD/USD","USD/CAD","NZD/USD",
    "EUR/GBP","EUR/JPY","EUR/CHF","EUR/AUD","EUR/CAD","EUR/NZD","GBP/JPY","GBP/CHF",
    "GBP/AUD","GBP/CAD","GBP/NZD","AUD/JPY","AUD/CHF","AUD/CAD","AUD/NZD","CAD/JPY",
    "CAD/CHF","CHF/JPY","NZD/JPY","NZD/CAD","NZD/CHF",
    "USD/MXN","USD/ZAR","USD/TRY","USD/SGD","USD/NOK","USD/SEK","USD/PLN","USD/CNH",
    "USD/HUF","USD/CZK","EUR/TRY","EUR/NOK","EUR/SEK","EUR/PLN","XAU/USD","XAG/USD",
]
# --- Alpaca (données historiques crypto, LECTURE seule) --------------------
ALPACA_PAPER_KEY = os.environ.get("ALPACA_PAPER_KEY", "")
ALPACA_PAPER_SECRET = os.environ.get("ALPACA_PAPER_SECRET", "")
# --- Supabase (écriture serveur du pipeline d'historique) ------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://qdhnnsipwnogecrptxfk.supabase.co")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
# Granularités stockées : label interne -> timeframe Alpaca
CRYPTO_GRANULARITIES = {"1D": "1Day", "1H": "1Hour", "15Min": "15Min"}

CRYPTO_PRIORITY = [
    "BTC/USD","ETH/USD","XRP/USD","BNB/USD","SOL/USD","TRX/USD","DOGE/USD","ADA/USD",
    "HYPE/USD","LINK/USD","AVAX/USD","SUI/USD","XLM/USD","BCH/USD","HBAR/USD","LTC/USD",
    "DOT/USD","UNI/USD","XMR/USD","AAVE/USD","POL/USD","NEAR/USD","APT/USD","ETC/USD",
    "ICP/USD","VET/USD","RNDR/USD","ATOM/USD","FIL/USD","ARB/USD","OP/USD","INJ/USD",
    "ALGO/USD","GRT/USD","STX/USD","MKR/USD","TAO/USD","IMX/USD","THETA/USD","XTZ/USD",
    "KAS/USD","SEI/USD","QNT/USD","FLOW/USD","LDO/USD","TIA/USD","RUNE/USD","S/USD",
    "AXS/USD","SAND/USD","MANA/USD","CHZ/USD","GALA/USD","PEPE/USD","SHIB/USD","MNT/USD",
    "CRV/USD","CAKE/USD","SNX/USD","COMP/USD","DYDX/USD","1INCH/USD","ZEC/USD","DASH/USD",
    "HNT/USD","JUP/USD","PYTH/USD","WLD/USD","ONDO/USD","STRK/USD","RON/USD","ENA/USD",
    "BONK/USD","WIF/USD","FLOKI/USD","KAVA/USD","MINA/USD","IOTA/USD","NEO/USD","CFX/USD",
    "GNO/USD","ENJ/USD","LRC/USD","BAT/USD","ZRX/USD","YFI/USD","FET/USD","BAND/USD",
    "STORJ/USD","LPT/USD","OCEAN/USD","BAL/USD","ANKR/USD","AUDIO/USD","ZIL/USD","QTUM/USD",
    "REN/USD","KSM/USD","WAVES/USD","CHR/USD",
]
