"""
Fournisseur de données OANDA — SOURCE PRINCIPALE pour l'intraday.

  - Historique : bougies OHLC paginées (plafond ~5000/req) -> cache SQLite.
  - Live : meilleur bid/ask courant.
  - Conversions : ramener un P&L vers la devise du compte (CAD).
  - Compte : résumé (solde, NAV, devise) pour vérifier la config.

Sécurité : token et account_id viennent UNIQUEMENT des variables
d'environnement (config.py). Réseau requis -> imports paresseux.
"""

from datetime import datetime, timedelta, timezone

from data_provider import DataProvider, normalize_pair
from cache import Cache
import config


def rfc3339(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OandaData(DataProvider):
    def __init__(self, cache: Cache = None, account_currency=None):
        self.cache = cache or Cache()
        self.account_ccy = account_currency or config.ACCOUNT_CURRENCY
        self._api = None

    def _ensure_api(self):
        if self._api is None:
            if not config.OANDA_TOKEN or not config.OANDA_ACCOUNT_ID:
                raise RuntimeError(
                    "OANDA_TOKEN et OANDA_ACCOUNT_ID requis (variables "
                    "d'environnement). Voir .env.example.")
            import oandapyV20
            self._api = oandapyV20.API(access_token=config.OANDA_TOKEN,
                                       environment=config.ENVIRONMENT)
        return self._api

    # -- compte --------------------------------------------------------------
    def get_account_summary(self):
        import oandapyV20.endpoints.accounts as accounts
        api = self._ensure_api()
        r = accounts.AccountSummary(config.OANDA_ACCOUNT_ID)
        api.request(r)
        a = r.response["account"]
        return {"id": a.get("id"), "currency": a.get("currency"),
                "balance": float(a.get("balance", 0)), "nav": float(a.get("NAV", 0)),
                "open_trades": int(a.get("openTradeCount", 0))}

    def get_equity(self):
        return self.get_account_summary()["nav"]

    def list_instruments(self):
        """Noms OANDA des instruments tradables (CURRENCY + METAL), ex. EUR_USD, XAU_USD."""
        import oandapyV20.endpoints.accounts as accounts
        api = self._ensure_api()
        r = accounts.AccountInstruments(config.OANDA_ACCOUNT_ID)
        api.request(r)
        return [i["name"] for i in r.response.get("instruments", [])
                if i.get("type") in ("CURRENCY", "METAL")]

    # -- parsing isolé (PUR, testable sans réseau) ---------------------------
    @staticmethod
    def parse_candles(raw_candles):
        """Transforme la réponse OANDA en bougies {time,o,h,l,c} (complètes)."""
        out = []
        for c in raw_candles:
            if not c.get("complete"):
                continue
            m = c["mid"]
            out.append({"time": c["time"], "o": float(m["o"]), "h": float(m["h"]),
                        "l": float(m["l"]), "c": float(m["c"])})
        return out

    # -- historique ----------------------------------------------------------
    def fetch_history(self, pair, granularity="M15", start=None, end=None):
        import oandapyV20.endpoints.instruments as instruments  # noqa
        from oandapyV20.contrib.factories import InstrumentsCandlesFactory
        pair = normalize_pair(pair)
        api = self._ensure_api()
        params = {"granularity": granularity, "price": "M"}
        if start:
            params["from"] = start
        if end:
            params["to"] = end
        collected = []
        for req in InstrumentsCandlesFactory(instrument=pair, params=params):
            api.request(req)
            collected += self.parse_candles(req.response.get("candles", []))
        if collected:
            self.cache.upsert_candles(pair, granularity, collected)
        return len(collected)

    def update_history(self, pair, granularity="M15"):
        """Incrémental : ne télécharge que les bougies depuis la dernière en cache."""
        pair = normalize_pair(pair)
        last = self.cache.last_candle_time(pair, granularity)
        start = last or rfc3339(datetime.now(timezone.utc) - timedelta(days=90))
        return self.fetch_history(pair, granularity, start=start)

    def get_history(self, pair, granularity="M15", start=None, end=None):
        pair = normalize_pair(pair)
        cached = self.cache.get_candles(pair, granularity, start, end)
        if cached:
            return cached
        self.fetch_history(pair, granularity, start, end)
        return self.cache.get_candles(pair, granularity, start, end)

    # -- live ----------------------------------------------------------------
    def get_latest(self, pair):
        import oandapyV20.endpoints.pricing as pricing
        pair = normalize_pair(pair)
        api = self._ensure_api()
        r = pricing.PricingInfo(config.OANDA_ACCOUNT_ID, params={"instruments": pair})
        api.request(r)
        p = r.response["prices"][0]
        return {"bid": float(p["bids"][0]["price"]), "ask": float(p["asks"][0]["price"])}

    # -- conversions vers la devise du compte --------------------------------
    def conversion_rates(self, instrument):
        instrument = normalize_pair(instrument)
        base, quote = instrument.split("_")
        return (self._to_account(quote), self._to_account(base))

    def _to_account(self, ccy):
        if ccy == self.account_ccy:
            return 1.0
        try:
            px = self.get_latest(f"{ccy}_{self.account_ccy}")
            return (px["bid"] + px["ask"]) / 2.0
        except Exception:
            px = self.get_latest(f"{self.account_ccy}_{ccy}")
            mid = (px["bid"] + px["ask"]) / 2.0
            return 1.0 / mid if mid else 0.0
