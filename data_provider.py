"""
Interface commune des fournisseurs de données.

Le reste du projet (signaux, backtest, exécution) ne sait JAMAIS d'où vient
la donnée : il appelle get_history() / get_latest() sur un DataProvider.
On peut ainsi brancher OANDA (intraday, source principale) ou Frankfurter
(contexte macro quotidien) sans changer le code qui consomme.

Format d'une bougie (compatible avec indicators.py et signals.py) :
    {"time": "2026-06-24T12:00:00Z", "o": ..., "h": ..., "l": ..., "c": ...}
"""

from abc import ABC, abstractmethod


class DataProvider(ABC):

    @abstractmethod
    def get_history(self, pair: str, granularity: str,
                    start: str, end: str) -> list:
        """Renvoie une liste de bougies entre start et end (dates ISO)."""
        ...

    @abstractmethod
    def get_latest(self, pair: str) -> dict:
        """Renvoie le dernier prix connu : {"bid": ..., "ask": ...}."""
        ...


def normalize_pair(pair: str) -> str:
    """Accepte 'EUR/USD', 'EURUSD', 'eur_usd' -> 'EUR_USD' (format OANDA)."""
    p = pair.upper().replace("/", "_").replace("-", "_")
    if "_" not in p and len(p) == 6:
        p = p[:3] + "_" + p[3:]
    return p
