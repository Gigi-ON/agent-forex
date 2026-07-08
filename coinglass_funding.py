"""
Fetcher funding PROFOND via Coinglass (US-accessible) -> data/funding_multi/{BASE}.parquet.
Source funding Binance agregee par Coinglass = historique 2020->auj (bear 2022 inclus),
sans toucher Binance ni VPN. Cle API lue depuis .env (COINGLASS_API_KEY) -- jamais affichee.
Lecture seule. VPS : venv python coinglass_funding.py
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

URL = "https://open-api-v4.coinglass.com/api/futures/funding-rate/history"
OUT = "data/funding_multi"
ENV = "/home/forex/agent-forex/.env"
EXCHANGE = os.environ.get("CG_EXCHANGE", "Binance")
INTERVAL = os.environ.get("CG_INTERVAL", "8h")          # tier gratuit: >=4h ; 8h = natif funding
DEEP_START_MS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
SLEEP = float(os.environ.get("CG_SLEEP", "2.0"))        # doux avec le rate-limit du tier gratuit

UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT", "LTC", "LINK",
            "BCH", "ATOM", "ETC", "XLM", "TRX", "AVAX", "UNI", "FIL", "AAVE", "EOS",
            "XTZ", "ALGO", "THETA", "VET", "ICP", "EGLD", "SAND", "MANA", "CHZ", "ENJ",
            "ZEC", "DASH", "COMP", "YFI", "SNX", "CRV", "SUSHI", "GRT", "NEO", "IOTA"]


def load_key():
    k = os.environ.get("COINGLASS_API_KEY")
    if k:
        return k.strip()
    try:
        for line in open(ENV):
            line = line.strip()
            if line.startswith("COINGLASS_API_KEY") and "=" in line:
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except Exception:
        pass
    return None


def _get(params, key, tries=3):
    url = URL + "?" + urllib.parse.urlencode(params)
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"accept": "application/json", "CG-API-KEY": key})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2.0 * (k + 1))


def fetch_symbol(base, key, interval, start_ms):
    sym = base + "USDT"
    out = []; start = start_ms
    while True:
        d = _get({"exchange": EXCHANGE, "symbol": sym, "interval": interval,
                  "limit": 1000, "start_time": start}, key)
        if str(d.get("code")) != "0":
            raise RuntimeError(str(d.get("msg") or d.get("code")))
        data = d.get("data") or []
        if not data:
            break
        for c in data:
            out.append((int(c["time"]), float(c["close"])))
        if len(data) < 1000:
            break
        nstart = int(data[-1]["time"]) + 1
        if nstart <= start:
            break
        start = nstart
        time.sleep(SLEEP)
    return out


def main():
    import pandas as pd
    key = load_key()
    if not key:
        raise SystemExit("Cle COINGLASS_API_KEY absente du .env. Ajoute COINGLASS_API_KEY=... puis relance.")
    os.makedirs(OUT, exist_ok=True)
    interval = INTERVAL
    ok = 0; catalog = []; t0 = time.time()
    for i, base in enumerate(UNIVERSE, 1):
        try:
            rows = fetch_symbol(base, key, interval, DEEP_START_MS)
        except Exception as e:
            msg = str(e)
            # tier gratuit peut refuser un intervalle -> repli 1d
            if interval != "1d" and ("interval" in msg.lower() or "plan" in msg.lower() or "30" in msg):
                print("  interval %s refuse (%s) -> repli 1d" % (interval, msg[:60]), flush=True)
                interval = "1d"
                try:
                    rows = fetch_symbol(base, key, interval, DEEP_START_MS)
                except Exception as e2:
                    print("  ! %s: %s" % (base, str(e2)[:70]), flush=True); continue
            else:
                print("  ! %s: %s" % (base, msg[:70]), flush=True); continue
        if rows:
            df = pd.DataFrame(rows, columns=["ts_ms", "rate"]).drop_duplicates("ts_ms").sort_values("ts_ms")
            df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
            df.to_parquet(os.path.join(OUT, "%s.parquet" % base))
            ok += 1
            catalog.append({"symbol": base, "rows": int(len(df)),
                            "first": str(df["ts"].iloc[0]), "last": str(df["ts"].iloc[-1])})
        if i % 5 == 0 or i == len(UNIVERSE):
            print("  %d/%d ok=%d interval=%s | %.0fs" % (i, len(UNIVERSE), ok, interval, time.time() - t0), flush=True)
        time.sleep(SLEEP)
    span = ""
    if catalog:
        span = " | couverture %s -> %s" % (min(c["first"] for c in catalog), max(c["last"] for c in catalog))
    json.dump({"source": "coinglass:%s" % EXCHANGE, "interval": interval, "raw_kept": True,
               "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "symbols": len(catalog), "datasets": catalog},
              open(os.path.join(OUT, "_manifest.json"), "w"), indent=2)
    print("TERMINE: %d symboles funding Coinglass/%s (interval %s)%s" % (ok, EXCHANGE, interval, span))


if __name__ == "__main__":
    main()
