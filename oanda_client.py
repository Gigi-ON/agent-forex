"""
Client OANDA : couche d'accès aux données et à l'exécution.

Sécurités intégrées :
  - Démarre en mode practice (compte démo) par défaut.
  - place_market_order() REFUSE d'envoyer un ordre réel tant que
    config.LIVE_TRADING n'est pas explicitement à True.
  - L'API token n'est jamais loggé.
"""

import oandapyV20
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.pricing as pricing
import oandapyV20.endpoints.orders as orders
from oandapyV20.contrib.requests import (
    MarketOrderRequest,
    TakeProfitDetails,
    StopLossDetails,
)

import config


class OandaClient:
    def __init__(self):
        if not config.OANDA_TOKEN or not config.OANDA_ACCOUNT_ID:
            raise RuntimeError(
                "OANDA_TOKEN et OANDA_ACCOUNT_ID doivent être définis "
                "(variables d'environnement). Voir config.py."
            )
        self.account_id = config.OANDA_ACCOUNT_ID
        self.api = oandapyV20.API(
            access_token=config.OANDA_TOKEN,
            environment=config.ENVIRONMENT,  # "practice" ou "live"
        )

    # -- données -------------------------------------------------------------
    def get_equity(self) -> float:
        """Capital (NAV) du compte, dans la devise du compte."""
        r = accounts.AccountSummary(self.account_id)
        self.api.request(r)
        return float(r.response["account"]["NAV"])

    def get_price(self, instrument: str) -> dict:
        """Meilleur bid/ask courant pour un instrument."""
        params = {"instruments": instrument}
        r = pricing.PricingInfo(self.account_id, params=params)
        self.api.request(r)
        p = r.response["prices"][0]
        return {
            "bid": float(p["bids"][0]["price"]),
            "ask": float(p["asks"][0]["price"]),
        }

    def get_candles(self, instrument: str, granularity="H1", count=200):
        """
        Bougies OHLC. granularity : M1, M5, M15, H1, H4, D...
        Renvoie une liste de dicts {time, o, h, l, c}.
        """
        params = {"granularity": granularity, "count": count, "price": "M"}
        r = instruments.InstrumentsCandles(instrument=instrument, params=params)
        self.api.request(r)
        out = []
        for c in r.response["candles"]:
            if not c["complete"]:
                continue
            m = c["mid"]
            out.append({
                "time": c["time"],
                "o": float(m["o"]), "h": float(m["h"]),
                "l": float(m["l"]), "c": float(m["c"]),
            })
        return out

    # -- exécution (VERROUILLÉE par défaut) ----------------------------------
    def place_market_order(self, instrument, units, stop_loss, take_profit):
        """
        Place un ordre au marché AVEC stop-loss et take-profit attachés.

        VERROU DE SÉCURITÉ : tant que config.LIVE_TRADING != True, cette
        méthode n'envoie RIEN et renvoie une simulation. C'est volontaire.
        """
        order = MarketOrderRequest(
            instrument=instrument,
            units=units,  # signé : + achat, - vente
            takeProfitOnFill=TakeProfitDetails(price=round(take_profit, 5)).data,
            stopLossOnFill=StopLossDetails(price=round(stop_loss, 5)).data,
        )

        if not config.LIVE_TRADING:
            return {
                "simulated": True,
                "message": "LIVE_TRADING désactivé : aucun ordre réel envoyé.",
                "would_send": order.data,
            }

        r = orders.OrderCreate(self.account_id, data=order.data)
        self.api.request(r)
        return {"simulated": False, "response": r.response}
