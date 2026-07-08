"""
Fetcher funding MULTI-ANNEES (regime test) : historique profond incluant le bear 2022.
Sonde la source atteignable depuis le VPS (Binance -> Bybit -> OKX) et telecharge
l'univers des majors ayant traverse 2021-2022. -> data/funding_multi/{BASE}.parquet + _manifest.json
Donnees publiques, pas de cle. Lecture seule. Incremental : ne refetch que le nouveau.
"""
import json
import os
import time
import urllib.request

OUT = "data/funding_multi"
UA = {"User-Agent": "agent-forex-fmulti/1.0"}

UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT", "LTC", "LINK",
            "BCH", "ATOM", "ETC", "XLM", "TRX", "AVAX", "UNI", "FIL", "AAVE", "EOS",
            "XTZ", "ALGO", "THETA", "VET", "ICP", "EGLD", "SAND", "MANA", "CHZ", "ENJ",
            "ZEC", "DASH", "COMP", "YFI", "SNX", "CRV", "SUSHI", "GRT", "NEO", "IOTA"]


def _get(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.2 * (k + 1))


def reachable(url):
    try:
        _get(url); return True
    except Exception:
        return False


# ---- Binance (funding 8h, depuis ~2020) ----
def binance_funding(base, since_ms=0):
    sym = base + "USDT"
    out = []; start = since_ms
    while True:
        url = "https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=1000" % sym
        if start:
            url += "&startTime=%d" % start
        d = _get(url)
        if not d:
            break
        for r in d:
            out.append((int(r["fundingTime"]), float(r["fundingRate"])))
        if len(d) < 1000:
            break
        nstart = int(d[-1]["fundingTime"]) + 1
        if nstart == start:
            break
        start = nstart
        time.sleep(0.25)
    return out


# ---- Bybit v5 (funding, pagine par endTime desc) ----
def bybit_funding(base, since_ms=0):
    sym = base + "USDT"
    out = []; end = None
    while True:
        url = "https://api.bybit.com/v5/market/funding/history?category=linear&symbol=%s&limit=200" % sym
        if end:
            url += "&endTime=%d" % end
        d = _get(url)
        lst = (d.get("result") or {}).get("list", []) or []
        if not lst:
            break
        for r in lst:
            out.append((int(r["fundingRateTimestamp"]), float(r["fundingRate"])))
        oldest = int(lst[-1]["fundingRateTimestamp"])
        if len(lst) < 200 or (since_ms and oldest <= since_ms):
            break
        end = oldest - 1
        time.sleep(0.2)
    return out


# ---- OKX (funding-rate-history, pagine par after=ts desc) ----
def okx_funding(base, since_ms=0):
    inst = base + "-USDT-SWAP"
    out = []; after = None
    while True:
        url = "https://www.okx.com/api/v5/public/funding-rate-history?instId=%s&limit=100" % inst
        if after:
            url += "&after=%d" % after
        d = _get(url)
        lst = d.get("data", []) or []
        if not lst:
            break
        for r in lst:
            out.append((int(r["fundingTime"]), float(r["realizedRate"] if "realizedRate" in r else r["fundingRate"])))
        oldest = int(lst[-1]["fundingTime"])
        if len(lst) < 100 or (since_ms and oldest <= since_ms):
            break
        after = oldest
        time.sleep(0.2)
    return out


SOURCES = [("binance", "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1", binance_funding),
           ("bybit", "https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=1", bybit_funding),
           ("okx", "https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=1", okx_funding)]


def pick_source():
    for name, probe, fn in SOURCES:
        if reachable(probe):
            return name, fn
    return None, None


def main():
    import pandas as pd
    os.makedirs(OUT, exist_ok=True)
    name, fn = pick_source()
    if not fn:
        raise SystemExit("Aucune source funding multi-annees atteignable depuis le VPS (Binance/Bybit/OKX).")
    print("Source atteignable: %s" % name, flush=True)
    ok = 0; catalog = []; t0 = time.time()
    for i, base in enumerate(UNIVERSE, 1):
        path = os.path.join(OUT, "%s.parquet" % base)
        since = 0; old = None
        if os.path.exists(path):                       # incremental : repartir du dernier ts
            try:
                old = pd.read_parquet(path)
                since = int(old["ts_ms"].max()) + 1
            except Exception:
                old = None; since = 0
        try:
            rows = fn(base, since_ms=since)
        except Exception as e:
            print("  ! %s: %s" % (base, str(e)[:70]), flush=True); continue
        if rows:
            df = pd.DataFrame(rows, columns=["ts_ms", "rate"])
            if old is not None:
                df = pd.concat([old, df], ignore_index=True)
            df = df.drop_duplicates("ts_ms").sort_values("ts_ms")
            df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
            df.to_parquet(path)
            ok += 1
            catalog.append({"symbol": base, "rows": int(len(df)),
                            "first": str(df["ts"].iloc[0]), "last": str(df["ts"].iloc[-1])})
        if i % 10 == 0 or i == len(UNIVERSE):
            print("  %d/%d ok=%d | %.0fs" % (i, len(UNIVERSE), ok, time.time() - t0), flush=True)
        time.sleep(0.2)
    json.dump({"source": name, "kind": "funding_multi", "raw_kept": True,
               "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "symbols": len(catalog), "datasets": catalog},
              open(os.path.join(OUT, "_manifest.json"), "w"), indent=2)
    print("TERMINE: %d symboles funding multi-annees (%s) dans %s" % (ok, name, OUT))


if __name__ == "__main__":
    main()
