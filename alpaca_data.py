"""
Fournisseur de données crypto via Alpaca — COURS UNIQUEMENT (aucune exécution).

Lit les clés depuis l'environnement (ALPACA_PAPER_KEY/SECRET en priorité, sinon
LIVE). Les données crypto d'Alpaca sont les mêmes quel que soit le compte ;
l'exécution réelle (live) viendra plus tard, derrière le gate « verdict GO »
et la double authentification.
"""
from datetime import datetime, timedelta, timezone

import config

DATA_BASE = "https://data.alpaca.markets/v1beta3/crypto/us"


def _headers():
    k = config.ALPACA_PAPER_KEY or config.ALPACA_LIVE_KEY
    s = config.ALPACA_PAPER_SECRET or config.ALPACA_LIVE_SECRET
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s} if (k and s) else {}


def configured():
    return bool((config.ALPACA_PAPER_KEY or config.ALPACA_LIVE_KEY)
                and (config.ALPACA_PAPER_SECRET or config.ALPACA_LIVE_SECRET))


class AlpacaData:
    """Cours crypto (derniers quotes + historique de bougies)."""

    @staticmethod
    def parse_quotes(payload):
        """Transforme la réponse Alpaca en {symbole: {bid, ask, mid}} (PUR, testable)."""
        out = {}
        for sym, d in (payload.get("quotes", {}) or {}).items():
            bid = float(d.get("bp", 0) or 0)
            ask = float(d.get("ap", 0) or 0)
            out[sym] = {"bid": bid, "ask": ask,
                        "mid": round((bid + ask) / 2, 2) if (bid and ask) else None}
        return out

    @staticmethod
    def parse_bars(payload, symbol):
        bars = (payload.get("bars", {}) or {}).get(symbol, []) or []
        return [{"time": b["t"], "o": float(b["o"]), "h": float(b["h"]),
                 "l": float(b["l"]), "c": float(b["c"])} for b in bars]

    def latest_quotes(self, symbols):
        import requests
        r = requests.get(DATA_BASE + "/latest/quotes",
                         params={"symbols": ",".join(symbols)},
                         headers=_headers(), timeout=8)
        r.raise_for_status()
        return self.parse_quotes(r.json())

    def get_history(self, symbol, timeframe="15Min", limit=300):
        import requests
        start = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.get(DATA_BASE + "/bars",
                         params={"symbols": symbol, "timeframe": timeframe,
                                 "start": start, "limit": limit},
                         headers=_headers(), timeout=10)
        r.raise_for_status()
        return self.parse_bars(r.json(), symbol)
