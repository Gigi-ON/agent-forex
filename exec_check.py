"""
Check EXECUTABILITE du carry (LECTURE SEULE) :
- pour chaque perp PF_ avec funding, cherche le SPOT Kraken correspondant (jambe de hedge)
- mesure le spread perp (Futures tickers) et le spread spot (Ticker)
- calcule un cout aller-retour REALISTE par nom (frais + demi-spreads, 2 jambes x 2 sens)
Ecrit data/funding/_exec.json (extension du catalogue). Aucune ecriture ailleurs.
"""
import glob
import json
import os
import time
import urllib.request

SPOT = "https://api.kraken.com/0/public"
FUT = "https://futures.kraken.com/derivatives/api"
OUT = "data/funding"
UA = {"User-Agent": "agent-forex-exec/1.0"}
SPOT_TAKER_BP = 26.0   # Kraken spot taker ~0.26%
FUT_TAKER_BP = 5.0     # Kraken Futures taker ~0.05%


def _get(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.2 * (k + 1))


def perp_base(sym):
    b = sym[3:] if sym.upper().startswith("PF_") else sym
    if b.upper().endswith("USD"):
        b = b[:-3]
    return b.upper()


def spot_index():
    d = _get(SPOT + "/AssetPairs")
    return d.get("result", {}) or {}


def find_spot(base, res):
    cands = {base}
    if base == "XBT": cands.add("BTC")
    if base == "BTC": cands.add("XBT")
    for quotes in (("USD",), ("USDT", "USDC")):
        for k, v in res.items():
            ws = v.get("wsname", "")
            if "/" in ws:
                b, q = ws.split("/")
                if q in quotes and b.upper() in cands:
                    return v.get("altname", k)
    return None


def spot_spread_bp(pair):
    try:
        d = _get(SPOT + "/Ticker?pair=" + pair)
        for _, v in (d.get("result", {}) or {}).items():
            a = float(v["a"][0]); b = float(v["b"][0]); mid = (a + b) / 2
            return (a - b) / mid * 1e4 if mid > 0 else None
    except Exception:
        return None
    return None


def futures_spreads():
    d = _get(FUT + "/v3/tickers")
    out = {}
    for t in d.get("tickers", []):
        s = t.get("symbol", ""); bid = t.get("bid"); ask = t.get("ask")
        if s and bid and ask:
            mid = (float(bid) + float(ask)) / 2
            out[s] = (float(ask) - float(bid)) / mid * 1e4 if mid > 0 else None
    return out


def main():
    syms = [os.path.basename(f)[:-len(".parquet")]
            for f in glob.glob(os.path.join(OUT, "*.parquet"))]
    syms = [s for s in syms if s.upper().startswith("PF_")]
    print("Perps a evaluer: %d" % len(syms), flush=True)
    res = spot_index()
    fspr = futures_spreads()
    table = {}
    hedgeable = 0
    for i, s in enumerate(syms, 1):
        base = perp_base(s)
        sp = find_spot(base, res)
        pspr = fspr.get(s)
        sspr = spot_spread_bp(sp) if sp else None
        rt = None
        if sp is not None and pspr is not None and sspr is not None:
            rt = 2 * (SPOT_TAKER_BP + sspr / 2) + 2 * (FUT_TAKER_BP + pspr / 2)
            hedgeable += 1
        table[s] = {"base": base, "has_spot": sp is not None, "spot_pair": sp,
                    "spot_spread_bp": None if sspr is None else round(sspr, 1),
                    "perp_spread_bp": None if pspr is None else round(pspr, 1),
                    "roundtrip_bp": None if rt is None else round(rt, 1)}
        if sp:
            time.sleep(0.12)
        if i % 25 == 0 or i == len(syms):
            print("  %d/%d evalues, hedgeables=%d" % (i, len(syms), hedgeable), flush=True)
    json.dump({"spot_taker_bp": SPOT_TAKER_BP, "fut_taker_bp": FUT_TAKER_BP,
               "hedgeable": hedgeable, "total": len(syms), "table": table},
              open(os.path.join(OUT, "_exec.json"), "w"), indent=2)
    print("EXEC: %d/%d perps hedgeables (spot Kraken dispo) -> _exec.json" % (hedgeable, len(syms)))


if __name__ == "__main__":
    main()
