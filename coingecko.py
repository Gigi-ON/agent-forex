"""
CoinGecko — capitalisation / offre en circulation / volume 24 h des cryptos.
API gratuite, sans clé (limitée en débit -> on met en cache). Une seule requête
ramène tout le top par market cap.

Le classement (rank_crypto) est PUR et testable : score = volume 24 h + market cap
+ momentum (variation 24 h), restreint à notre univers tradable.
"""
import time

BASE = "https://api.coingecko.com/api/v3"
_CACHE = {"ts": 0.0, "data": None}


def fetch_markets(per_page=100):
    import requests
    r = requests.get(BASE + "/coins/markets",
                     params={"vs_currency": "usd", "order": "market_cap_desc",
                             "per_page": per_page, "page": 1,
                             "price_change_percentage": "24h"},
                     timeout=12)
    r.raise_for_status()
    return r.json()


def markets_cached(ttl=300):
    now = time.time()
    if _CACHE["data"] and (now - _CACHE["ts"]) < ttl:
        return _CACHE["data"]
    d = fetch_markets()
    _CACHE["ts"], _CACHE["data"] = now, d
    return d


def rank_crypto(markets, universe, top=10):
    """markets = liste CoinGecko ; universe = paires d'affichage ('BTC/USD', ...).
    Renvoie le top par score (liquidité + taille + momentum), restreint à l'univers."""
    bases = {d.split("/")[0].lower(): d for d in universe}
    rows, seen = [], set()
    for m in markets or []:
        sym = (m.get("symbol") or "").lower()
        if sym in bases and sym not in seen:        # 1re occurrence = plus grosse cap
            seen.add(sym)
            rows.append({
                "symbol": bases[sym], "name": m.get("name"),
                "price": m.get("current_price"),
                "volume_24h": float(m.get("total_volume") or 0),
                "market_cap": float(m.get("market_cap") or 0),
                "circulating": m.get("circulating_supply"),
                "change_24h": float(m.get("price_change_percentage_24h") or 0)})
    if not rows:
        return []
    vmax = max((r["volume_24h"] for r in rows), default=0) or 1
    cmax = max((r["market_cap"] for r in rows), default=0) or 1
    for r in rows:
        mom = max(0.0, min(1.0, 0.5 + r["change_24h"] / 10.0))   # +5% -> 1.0, -5% -> 0
        r["score"] = round(0.45 * (r["volume_24h"] / vmax)
                           + 0.35 * (r["market_cap"] / cmax)
                           + 0.20 * mom, 4)
        r["state"] = ("haussier" if r["change_24h"] > 1
                      else "baissier" if r["change_24h"] < -1 else "range")
    rows.sort(key=lambda r: -r["score"])
    return rows[:top]
