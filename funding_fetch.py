"""
Fetcher funding Kraken Futures (perps PF_) -> parquet data/funding/{SYM}.parquet.
Donnees publiques, pas de cle. Lecture seule cote plateforme (n'ecrit que data/funding).
VPS : venv python funding_fetch.py
"""
import json
import os
import time
import urllib.request

BASE = "https://futures.kraken.com/derivatives/api"
OUT = "data/funding"
UA = {"User-Agent": "agent-forex-funding/1.0"}


def _get(url, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))


def list_perps():
    d = _get(BASE + "/v3/instruments")
    inst = d.get("instruments", d.get("result", []))
    syms = []
    for it in inst:
        s = it.get("symbol", "")
        if s.upper().startswith("PF_") and it.get("tradeable", True):
            syms.append(s)
    return sorted(set(syms))


def fetch_funding(sym):
    """Retourne [(ts, fundingRate, relativeFundingRate)] -- on conserve le BRUT (les deux)."""
    d = _get(BASE + "/v4/historicalfundingrates?symbol=%s" % sym)
    rates = d.get("rates", d.get("result", [])) or []
    out = []
    for r in rates:
        ts = r.get("timestamp") or r.get("time")
        fr = r.get("fundingRate")
        rfr = r.get("relativeFundingRate")
        if ts is not None and (fr is not None or rfr is not None):
            out.append((ts, None if fr is None else float(fr),
                        None if rfr is None else float(rfr)))
    return out


def main():
    import pandas as pd
    os.makedirs(OUT, exist_ok=True)
    perps = list_perps()
    print("Perps PF_ tradeables: %d" % len(perps), flush=True)
    ok = 0; empty = 0; err = 0; t0 = time.time(); catalog = []
    for i, s in enumerate(perps, 1):
        try:
            rows = fetch_funding(s)
            if not rows:
                empty += 1
            else:
                df = pd.DataFrame(rows, columns=["ts", "fundingRate", "relativeFundingRate"])
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df = df.drop_duplicates("ts").sort_values("ts")
                df.to_parquet(os.path.join(OUT, "%s.parquet" % s.replace("/", "-")))
                catalog.append({"symbol": s, "rows": int(len(df)),
                                "first": str(df["ts"].iloc[0]), "last": str(df["ts"].iloc[-1])})
                ok += 1
        except Exception as e:
            err += 1
            print("  ! %s: %s" % (s, str(e)[:80]), flush=True)
        if i % 25 == 0 or i == len(perps):
            el = time.time() - t0
            eta = el / i * (len(perps) - i)
            print("  %d/%d ok=%d vide=%d err=%d | %.0fs ETA %.0fs"
                  % (i, len(perps), ok, empty, err, el, eta), flush=True)
        time.sleep(0.15)  # doux avec l'API
    # CATALOGUE (brique du Centre de donnees) : source, granularite, couverture par symbole
    manifest = {"source": "kraken_futures", "endpoint": "v4/historicalfundingrates",
                "granularity": "1h", "fields": ["fundingRate", "relativeFundingRate"],
                "raw_kept": True, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "symbols": len(catalog), "datasets": catalog}
    with open(os.path.join(OUT, "_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    print("TERMINE: %d parquets funding + _manifest.json dans %s" % (ok, OUT))


if __name__ == "__main__":
    main()
