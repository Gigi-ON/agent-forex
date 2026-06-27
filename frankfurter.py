"""
Fournisseur Frankfurter — CONTEXTE MACRO quotidien (pas l'intraday).

Rôle dans le projet intraday :
  - filtre de régime long terme (tendance de fond sur années de clôtures),
  - référence de volatilité longue,
  - fallback de conversion CAD si OANDA est indisponible.

Limites assumées : un seul taux médian par jour ouvré (BCE), pas de OHLC,
pas de spread. Donc JAMAIS utilisé pour backtester de l'intraday.

API publique : https://api.frankfurter.dev/v2 — pas de clé, base EUR.
Réseau requis -> import paresseux.
"""

from data_provider import normalize_pair
from cache import Cache

BASE_URL = "https://api.frankfurter.dev/v2"


class FrankfurterData:
    def __init__(self, cache: Cache = None):
        self.cache = cache or Cache()

    def get_daily(self, pair, start, end=None):
        """
        Série quotidienne (date, taux) pour une paire. EUR comme base BCE :
          EUR_USD -> taux = USD par EUR ; EUR_CAD -> CAD par EUR.
        Pour une paire sans EUR, on calcule le croisement via l'EUR.
        Met en cache (table fx_daily).
        """
        pair = normalize_pair(pair)
        base, quote = pair.split("_")

        if base == "EUR":
            series = self._fetch_eur_quote(quote, start, end)
        elif quote == "EUR":
            inv = self._fetch_eur_quote(base, start, end)
            series = [(d, 1.0 / r) for d, r in inv if r]
        else:
            # croisement : (EUR->quote) / (EUR->base)
            q = dict(self._fetch_eur_quote(quote, start, end))
            b = dict(self._fetch_eur_quote(base, start, end))
            series = [(d, q[d] / b[d]) for d in sorted(q)
                      if d in b and b[d]]

        if series:
            self.cache.upsert_fx_daily(pair, series)
        return series

    def _fetch_eur_quote(self, quote_ccy, start, end):
        import requests
        url = f"{BASE_URL}/{start}.." + (f"{end}" if end else "")
        r = requests.get(url, params={"base": "EUR", "symbols": quote_ccy},
                         timeout=15)
        r.raise_for_status()
        data = r.json().get("rates", {})
        out = []
        for date in sorted(data):
            val = data[date].get(quote_ccy)
            if val is not None:
                out.append((date, float(val)))
        return out

    def regime(self, pair, start, end=None, fast=50, slow=200):
        """
        Filtre de régime simple sur les clôtures quotidiennes :
        renvoie 'haussier', 'baissier' ou 'neutre' selon deux moyennes.
        À utiliser comme CONTEXTE (autoriser/limiter le sens), pas comme signal.
        """
        from indicators import ema
        series = self.cache.get_fx_daily(pair, start, end) \
            or self.get_daily(pair, start, end)
        closes = [r for _, r in series]
        if len(closes) < slow + 1:
            return "neutre"
        ef, es = ema(closes, fast)[-1], ema(closes, slow)[-1]
        if ef > es * 1.001:
            return "haussier"
        if ef < es * 0.999:
            return "baissier"
        return "neutre"
