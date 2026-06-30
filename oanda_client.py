"""
Client OANDA : couche d'accès aux données et à l'exécution.

Sécurités intégrées :
  - Sélection du compte par nom : "practice" (démo) ou "live" (réel).
  - place_market_order() ENVOIE librement sur un compte PRACTICE (argent démo),
    mais sur un compte LIVE (argent réel) elle REFUSE tant que
    config.LIVE_TRADING n'est pas explicitement à True.
  - L'API token n'est jamais loggé.

Imports oandapyV20 en différé (lazy) : le module s'importe même sans le paquet ;
le paquet n'est requis qu'au moment d'un appel réseau réel (sur le serveur).
"""

import config


class OandaClient:
    def __init__(self, account="practice"):
        acc = config.ACCOUNTS.get(account, {})
        self.account = account
        self._token = acc.get("token") or config.OANDA_TOKEN
        self._account_id = acc.get("account_id") or config.OANDA_ACCOUNT_ID
        self._env = acc.get("env") or config.ENVIRONMENT      # "practice" | "live"
        if not self._token or not self._account_id:
            raise RuntimeError(
                "OANDA : token/account_id manquants pour le compte '%s' "
                "(variables d'environnement)." % account)
        self.account_id = self._account_id
        self._api = None

    @property
    def api(self):
        if self._api is None:
            import oandapyV20
            self._api = oandapyV20.API(access_token=self._token, environment=self._env)
        return self._api

    # -- données -------------------------------------------------------------
    def get_equity(self) -> float:
        import oandapyV20.endpoints.accounts as accounts
        r = accounts.AccountSummary(self.account_id)
        self.api.request(r)
        return float(r.response["account"]["NAV"])

    def get_price(self, instrument: str) -> dict:
        import oandapyV20.endpoints.pricing as pricing
        r = pricing.PricingInfo(self.account_id, params={"instruments": instrument})
        self.api.request(r)
        p = r.response["prices"][0]
        return {"bid": float(p["bids"][0]["price"]), "ask": float(p["asks"][0]["price"])}

    def get_candles(self, instrument: str, granularity="H1", count=200):
        import oandapyV20.endpoints.instruments as instruments
        params = {"granularity": granularity, "count": count, "price": "M"}
        r = instruments.InstrumentsCandles(instrument=instrument, params=params)
        self.api.request(r)
        out = []
        for c in r.response["candles"]:
            if not c["complete"]:
                continue
            m = c["mid"]
            out.append({"time": c["time"], "o": float(m["o"]), "h": float(m["h"]),
                        "l": float(m["l"]), "c": float(m["c"])})
        return out

    def get_open_trades(self):
        """Trades ouverts du compte : [{id, instrument, currentUnits, price, unrealizedPL}, ...]."""
        import oandapyV20.endpoints.trades as trades
        r = trades.OpenTrades(self.account_id)
        self.api.request(r)
        return r.response.get("trades", []) or []

    def close_trade(self, trade_id):
        import oandapyV20.endpoints.trades as trades
        r = trades.TradeClose(self.account_id, tradeID=str(trade_id))
        self.api.request(r)
        return r.response

    def partial_close(self, trade_id, units):
        """Clôture PARTIELLE : ferme `units` unités du trade (le reste court)."""
        import oandapyV20.endpoints.trades as trades
        r = trades.TradeClose(self.account_id, tradeID=str(trade_id),
                              data={"units": str(int(abs(units)))})
        self.api.request(r)
        return r.response

    def modify_stop(self, trade_id, stop):
        """Déplace le stop-loss attaché au trade (break-even / trailing)."""
        import oandapyV20.endpoints.trades as trades
        data = {"stopLoss": {"price": str(round(stop, 5))}}
        r = trades.TradeCRCDO(self.account_id, tradeID=str(trade_id), data=data)
        self.api.request(r)
        return r.response

    def trade_realized_pl(self, trade_id):
        """PnL réalisé d'un trade (utile après clôture côté courtier)."""
        import oandapyV20.endpoints.trades as trades
        r = trades.TradeDetails(self.account_id, tradeID=str(trade_id))
        self.api.request(r)
        return float(r.response["trade"].get("realizedPL", 0) or 0)

    # -- construction d'ordre (pure, testable) -------------------------------
    def build_order(self, instrument, units, stop_loss, take_profit):
        from oandapyV20.contrib.requests import (
            MarketOrderRequest, TakeProfitDetails, StopLossDetails)
        return MarketOrderRequest(
            instrument=instrument,
            units=units,                                  # signé : + achat, - vente
            takeProfitOnFill=TakeProfitDetails(price=round(take_profit, 5)).data,
            stopLossOnFill=StopLossDetails(price=round(stop_loss, 5)).data,
        ).data

    # -- exécution -----------------------------------------------------------
    def place_market_order(self, instrument, units, stop_loss, take_profit):
        """
        Ordre au marché AVEC stop-loss et take-profit attachés (broker-managed).

        Compte PRACTICE (démo) : envoyé normalement.
        Compte LIVE (argent réel) : VERROU — n'envoie rien tant que
        config.LIVE_TRADING != True.
        """
        data = self.build_order(instrument, units, stop_loss, take_profit)
        if self._env == "live" and not config.LIVE_TRADING:
            return {"blocked": True,
                    "message": "LIVE_TRADING désactivé : aucun ordre réel envoyé.",
                    "would_send": data}
        import oandapyV20.endpoints.orders as orders
        r = orders.OrderCreate(self.account_id, data=data)
        self.api.request(r)
        return {"simulated": False, "response": r.response}
