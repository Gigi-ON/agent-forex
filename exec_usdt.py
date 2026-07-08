"""
Check EXECUTABILITE cote USDT (LECTURE SEULE) : mesure le cout REEL par nom sur le venue
liquide (Binance/Bybit/OKX) ou vit le funding multi. Spreads spot+perp + frais du venue.
-> data/funding_multi/_exec_usdt.json. De-biaise le 344 bp mesure sur Kraken USD illiquide.
"""
import glob
import json
import os
import time
import urllib.request

OUT = "data/funding_multi"
UA = {"User-Agent": "agent-forex-execusdt/1.0"}
# frais taker par venue (spot, perp) en bp
FEES = {"binance": (10.0, 5.0), "bybit": (10.0, 5.5), "okx": (10.0, 5.0)}


def _get(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.0 * (k + 1))


def _spread_bp(bid, ask):
    bid = float(bid); ask = float(ask); mid = (bid + ask) / 2
    return (ask - bid) / mid * 1e4 if mid > 0 else None


# ---- Binance ----
def binance_spot(base):
    d = _get("https://api.binance.com/api/v3/ticker/bookTicker?symbol=%sUSDT" % base)
    return _spread_bp(d["bidPrice"], d["askPrice"])


def binance_perp(base):
    d = _get("https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol=%sUSDT" % base)
    return _spread_bp(d["bidPrice"], d["askPrice"])


# ---- Bybit ----
def _bybit_tick(cat, base):
    d = _get("https://api.bybit.com/v5/market/tickers?category=%s&symbol=%sUSDT" % (cat, base))
    lst = (d.get("result") or {}).get("list", []) or []
    if not lst:
        return None
    return _spread_bp(lst[0]["bid1Price"], lst[0]["ask1Price"])


def bybit_spot(base): return _bybit_tick("spot", base)
def bybit_perp(base): return _bybit_tick("linear", base)


# ---- OKX ----
def okx_spot(base):
    d = _get("https://www.okx.com/api/v5/market/ticker?instId=%s-USDT" % base)
    r = d.get("data", []) or []
    return _spread_bp(r[0]["bidPx"], r[0]["askPx"]) if r else None


def okx_perp(base):
    d = _get("https://www.okx.com/api/v5/market/ticker?instId=%s-USDT-SWAP" % base)
    r = d.get("data", []) or []
    return _spread_bp(r[0]["bidPx"], r[0]["askPx"]) if r else None


VENUES = {"binance": (binance_spot, binance_perp),
          "bybit": (bybit_spot, bybit_perp),
          "okx": (okx_spot, okx_perp)}


def source_from_manifest():
    p = os.path.join(OUT, "_manifest.json")
    if os.path.exists(p):
        try:
            return json.load(open(p)).get("source")
        except Exception:
            pass
    return None


def main():
    src = source_from_manifest()
    if src not in VENUES:
        raise SystemExit("Source funding multi inconnue (%s). Lance funding_multi.py d'abord." % src)
    spot_fn, perp_fn = VENUES[src]
    spot_fee, perp_fee = FEES[src]
    bases = sorted({os.path.basename(f)[:-len(".parquet")]
                    for f in glob.glob(os.path.join(OUT, "*.parquet"))})
    print("Venue: %s | frais spot %.0fbp perp %.0fbp | %d symboles" % (src, spot_fee, perp_fee, len(bases)), flush=True)
    table = {}; ok = 0
    for i, base in enumerate(bases, 1):
        ss = pp = None
        try:
            ss = spot_fn(base)
        except Exception:
            ss = None
        try:
            pp = perp_fn(base)
        except Exception:
            pp = None
        rt = None
        if ss is not None and pp is not None:
            rt = 2 * (spot_fee + ss / 2) + 2 * (perp_fee + pp / 2)
            ok += 1
        table[base] = {"has_spot": ss is not None, "spot_spread_bp": None if ss is None else round(ss, 1),
                       "perp_spread_bp": None if pp is None else round(pp, 1),
                       "roundtrip_bp": None if rt is None else round(rt, 1)}
        time.sleep(0.1)
        if i % 10 == 0 or i == len(bases):
            print("  %d/%d, hedgeables=%d" % (i, len(bases), ok), flush=True)
    avg = [v["roundtrip_bp"] for v in table.values() if v["roundtrip_bp"] is not None]
    json.dump({"venue": src, "spot_fee_bp": spot_fee, "perp_fee_bp": perp_fee,
               "hedgeable": ok, "total": len(bases),
               "avg_roundtrip_bp": round(sum(avg) / len(avg), 1) if avg else None, "table": table},
              open(os.path.join(OUT, "_exec_usdt.json"), "w"), indent=2)
    print("EXEC USDT: %d/%d hedgeables, cout AR moyen %s bp (vs 344 bp Kraken) -> _exec_usdt.json"
          % (ok, len(bases), round(sum(avg) / len(avg), 1) if avg else "n/a"))


if __name__ == "__main__":
    main()
