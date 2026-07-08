"""
Fetcher funding PROFOND via Coinalyze (GRATUIT, US-accessible) -> data/funding_multi/{BASE}.parquet.
Granularite 'daily' = aucune suppression -> historique complet 2020->auj (bear 2022 inclus).
Symboles agreges multi-exchanges ({BASE}USDT_PERP.A). Cle lue depuis .env (COINALYZE_API_KEY),
jamais affichee, passee en HEADER (pas dans l'URL). Lecture seule. VPS : venv python coinalyze_funding.py
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.coinalyze.net/v1"
OUT = "data/funding_multi"
ENV = "/home/forex/agent-forex/.env"
FROM_TS = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp())
SLEEP = 1.7   # 40 appels/min -> ~1.5s ; on prend une marge

UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT", "LTC", "LINK",
            "BCH", "ATOM", "ETC", "XLM", "TRX", "AVAX", "UNI", "FIL", "AAVE", "EOS",
            "XTZ", "ALGO", "THETA", "VET", "ICP", "EGLD", "SAND", "MANA", "CHZ", "ENJ",
            "ZEC", "DASH", "COMP", "YFI", "SNX", "CRV", "SUSHI", "GRT", "NEO", "IOTA"]


def load_key():
    k = os.environ.get("COINALYZE_API_KEY")
    if k:
        return k.strip()
    try:
        for line in open(ENV):
            line = line.strip()
            if line.startswith("COINALYZE_API_KEY") and "=" in line:
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except Exception:
        pass
    return None


def _get(path, params, key, tries=3):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"api_key": key, "accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and k < tries - 1:
                time.sleep(float(e.headers.get("Retry-After", 5)) + 1); continue
            if k == tries - 1:
                raise
            time.sleep(2.0 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2.0 * (k + 1))


def resolve_symbols(key):
    """base_asset -> symbole perp agrege ('{BASE}USDT_PERP.A' de preference)."""
    mkts = _get("/future-markets", {}, key)
    by_base = {}
    for m in mkts:
        if not m.get("is_perpetual"):
            continue
        sym = m.get("symbol", ""); base = (m.get("base_asset") or "").upper()
        quote = (m.get("quote_asset") or "").upper()
        if not base or base not in UNIVERSE:
            continue
        agg = sym.endswith(".A")
        usdt = quote in ("USDT", "USD")
        score = (2 if agg else 0) + (1 if usdt else 0)
        if base not in by_base or score > by_base[base][1]:
            by_base[base] = (sym, score)
    return {b: v[0] for b, v in by_base.items()}


def fetch_funding(symbols, key, interval="daily"):
    """Renvoie {symbol: [(ts_ms, rate)]}. Batch <=20 symboles/appel."""
    out = {}
    now = int(time.time())
    for i in range(0, len(symbols), 20):
        chunk = symbols[i:i + 20]
        d = _get("/funding-rate-history",
                 {"symbols": ",".join(chunk), "interval": interval, "from": FROM_TS, "to": now}, key)
        for row in d or []:
            hist = row.get("history") or []
            out[row.get("symbol")] = [(int(h["t"]) * 1000, float(h["c"])) for h in hist if "c" in h]
        time.sleep(SLEEP)
    return out


def main():
    import pandas as pd
    key = load_key()
    if not key:
        raise SystemExit("Cle COINALYZE_API_KEY absente du .env. Ajoute COINALYZE_API_KEY=... puis relance.")
    os.makedirs(OUT, exist_ok=True)
    sym_map = resolve_symbols(key)               # base -> symbol
    if not sym_map:
        raise SystemExit("Aucun symbole perp resolu (verifie la cle / l'univers).")
    print("Symboles perp resolus: %d/%d" % (len(sym_map), len(UNIVERSE)), flush=True)
    inv = {v: k for k, v in sym_map.items()}     # symbol -> base
    data = fetch_funding(list(sym_map.values()), key)
    ok = 0; catalog = []
    for sym, rows in data.items():
        base = inv.get(sym)
        if not base or not rows:
            continue
        df = pd.DataFrame(rows, columns=["ts_ms", "rate"]).drop_duplicates("ts_ms").sort_values("ts_ms")
        df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
        df.to_parquet(os.path.join(OUT, "%s.parquet" % base))
        ok += 1
        catalog.append({"symbol": base, "rows": int(len(df)),
                        "first": str(df["ts"].iloc[0]), "last": str(df["ts"].iloc[-1])})
    span = ""
    if catalog:
        span = " | couverture %s -> %s" % (min(c["first"] for c in catalog), max(c["last"] for c in catalog))
    json.dump({"source": "coinalyze:daily", "raw_kept": True,
               "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "symbols": len(catalog), "datasets": catalog},
              open(os.path.join(OUT, "_manifest.json"), "w"), indent=2)
    print("TERMINE: %d symboles funding Coinalyze (daily)%s" % (ok, span))


if __name__ == "__main__":
    main()
