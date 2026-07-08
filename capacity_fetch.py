"""
Fetch pour l'etude MAGNITUDE + CAPACITE (Coinalyze, gratuit) :
- funding 8h REEL (granularite correcte, ~1.8 an dispo en intraday)
- open interest en USD (proxy de capacite / liquidite)
-> data/capacity/{BASE}.parquet {ts, rate, oi_usd}. Lecture seule.
"""
import json
import os
import time
from datetime import datetime, timezone

import coinalyze_funding as CF   # reutilise load_key / _get / resolve_symbols

OUT = "data/capacity"
DAYS = 640                        # fenetre intraday (cap Coinalyze ~2000 pts a 8h)
INTERVAL = "12hour"


def hist(path, symbols, key, frm, to, extra=None):
    out = {}
    for i in range(0, len(symbols), 20):
        chunk = symbols[i:i + 20]
        params = {"symbols": ",".join(chunk), "interval": INTERVAL, "from": frm, "to": to}
        if extra:
            params.update(extra)
        d = CF._get(path, params, key)
        for row in d or []:
            out[row.get("symbol")] = row.get("history") or []
        time.sleep(CF.SLEEP)
    return out


def main():
    import pandas as pd
    key = CF.load_key()
    if not key:
        raise SystemExit("Cle COINALYZE_API_KEY absente du .env.")
    os.makedirs(OUT, exist_ok=True)
    sym_map = CF.resolve_symbols(key)
    inv = {v: k for k, v in sym_map.items()}
    syms = list(sym_map.values())
    to = int(time.time()); frm = to - DAYS * 86400
    print("Fetch funding 8h + OI (usd) : %d symboles, ~%d jours" % (len(syms), DAYS), flush=True)
    fund = hist("/funding-rate-history", syms, key, frm, to)
    oi = hist("/open-interest-history", syms, key, frm, to, {"convert_to_usd": "true"})
    ok = 0; catalog = []
    for sym in syms:
        base = inv.get(sym); fr = fund.get(sym) or []; oh = oi.get(sym) or []
        if not base or not fr:
            continue
        df_f = pd.DataFrame([(int(h["t"]) * 1000, float(h["c"])) for h in fr if "c" in h],
                            columns=["ts_ms", "rate"])
        df_o = pd.DataFrame([(int(h["t"]) * 1000, float(h["c"])) for h in oh if "c" in h],
                            columns=["ts_ms", "oi_usd"])
        df = df_f.merge(df_o, on="ts_ms", how="left").drop_duplicates("ts_ms").sort_values("ts_ms")
        df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
        df.to_parquet(os.path.join(OUT, "%s.parquet" % base))
        ok += 1
        catalog.append({"symbol": base, "rows": int(len(df)),
                        "first": str(df["ts"].iloc[0]), "last": str(df["ts"].iloc[-1]),
                        "oi_usd_med": float(df["oi_usd"].median()) if df["oi_usd"].notna().any() else None})
    json.dump({"source": "coinalyze:8hour", "fields": ["rate", "oi_usd"], "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "symbols": len(catalog), "datasets": catalog},
              open(os.path.join(OUT, "_manifest.json"), "w"), indent=2)
    cov = " | %s -> %s" % (min(c["first"] for c in catalog), max(c["last"] for c in catalog)) if catalog else ""
    print("TERMINE: %d symboles (funding 8h + OI) dans %s%s" % (ok, OUT, cov))


if __name__ == "__main__":
    main()
