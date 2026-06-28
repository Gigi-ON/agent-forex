"""
Données crypto via l'API publique Kraken — COURS UNIQUEMENT (aucune exécution).

Les données de marché publiques ne nécessitent AUCUNE clé. Les clés
(KRAKEN_API_KEY/SECRET) ne serviront qu'à l'exécution future, derrière le gate
« verdict GO » et la double authentification.
"""
from datetime import datetime, timezone

import config

BASE = "https://api.kraken.com/0/public"


class KrakenData:
    @staticmethod
    def parse_ticker(result, displays):
        """{symbole_affiché: {bid, ask, mid}} (PUR, testable). Kraken renvoie des
        clés canoniques (ex. XXBTZUSD) -> on les retrouve via le code de base."""
        out = {}
        for disp in displays:
            base = config.KRAKEN_PAIRS.get(disp, (None, None))[1]
            if not base:
                continue
            key = next((k for k in result if base in k and "USD" in k), None)
            if not key:
                continue
            d = result[key]
            ask = float(d["a"][0]); bid = float(d["b"][0])
            out[disp] = {"bid": bid, "ask": ask,
                         "mid": round((bid + ask) / 2, 2) if (bid and ask) else None}
        return out

    @staticmethod
    def parse_ohlc(result):
        key = next((k for k in result if k != "last"), None)
        rows = result.get(key, []) if key else []
        return [{"time": datetime.fromtimestamp(int(x[0]), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])}
                for x in rows]

    def latest_quotes(self, displays):
        import requests
        req = ",".join(config.KRAKEN_PAIRS[d][0] for d in displays if d in config.KRAKEN_PAIRS)
        r = requests.get(BASE + "/Ticker", params={"pair": req}, timeout=8)
        r.raise_for_status()
        j = r.json()
        if j.get("error"):
            raise RuntimeError("Kraken: " + ";".join(j["error"]))
        return self.parse_ticker(j.get("result", {}), displays)

    def get_history(self, display, interval=15, limit=300):
        import requests
        pair = config.KRAKEN_PAIRS.get(display, (display,))[0]
        r = requests.get(BASE + "/OHLC", params={"pair": pair, "interval": interval}, timeout=10)
        r.raise_for_status()
        j = r.json()
        if j.get("error"):
            raise RuntimeError("Kraken: " + ";".join(j["error"]))
        return self.parse_ohlc(j.get("result", {}))[-limit:]
