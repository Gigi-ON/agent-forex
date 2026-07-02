"""
Agrege les fichiers Time&Sales Kraken (trades bruts : ts,prix,volume) en bougies
OHLCV 15min / 1h / 1j, en UNE passe streaming (chunks pandas, memoire bornee).
Ecrit dans data/history/{SYMBOLE}_{gran}.parquet (meme schema, fusion/dedoublonnage).

Usage (python du venv) :
  python3 aggregate_trades.py /chemin/TimeAndSales_Combined                # tout USD, tout l'historique
  python3 aggregate_trades.py /chemin/TimeAndSales_Combined 2018           # depuis 2018
  python3 aggregate_trades.py /chemin/TimeAndSales_Combined 2018 univers   # + filtre top-100
"""
import glob
import os
import sys
import time
from datetime import datetime, timezone

import history_pipeline as hp

SEC = {"15Min": 900, "1H": 3600, "1D": 86400}
CHUNK = 5_000_000


def norm_symbol(pairfile):
    p = pairfile.upper()
    if not p.endswith("USD"):
        return None
    base = p[:-3]
    base = {"XBT": "BTC", "XXBT": "BTC", "XDG": "DOGE", "XXDG": "DOGE"}.get(base, base)
    if len(base) == 4 and base[0] == "X":
        base = base[1:]
    return (base + "/USD") if base else None


def _iso(sec):
    return datetime.fromtimestamp(int(sec), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def aggregate_file(path, start_ts=0):
    import pandas as pd
    masters = {g: {} for g in SEC}
    for chunk in pd.read_csv(path, header=None, names=["ts", "price", "vol"],
                             usecols=[0, 1, 2], chunksize=CHUNK):
        if start_ts:
            chunk = chunk[chunk["ts"] >= start_ts]
            if chunk.empty:
                continue
        for g, sec in SEC.items():
            b = (chunk["ts"] // sec) * sec
            grp = chunk.groupby(b).agg(o=("price", "first"), h=("price", "max"),
                                       l=("price", "min"), c=("price", "last"), v=("vol", "sum"))
            m = masters[g]
            for bucket, row in grp.iterrows():
                e = m.get(bucket)
                if e:
                    e[1] = max(e[1], row.h); e[2] = min(e[2], row.l); e[3] = row.c; e[4] += row.v
                else:
                    m[bucket] = [row.o, row.h, row.l, row.c, row.v]
    return masters


def to_rows(m):
    out = []
    for bucket in sorted(m):
        o, h, l, c, v = m[bucket]
        out.append({"ts": _iso(bucket), "o": float(o), "h": float(h),
                    "l": float(l), "c": float(c), "volume": float(v)})
    return out


def run(src, min_year=None, universe=None):
    start_ts = int(datetime(min_year, 1, 1, tzinfo=timezone.utc).timestamp()) if min_year else 0
    only = set(universe) if universe else None
    files = sorted(glob.glob(os.path.join(src, "*USD.csv")))
    files = [f for f in files if norm_symbol(os.path.basename(f)[:-4])]
    if only:
        files = [f for f in files if norm_symbol(os.path.basename(f)[:-4]) in only]
    N = len(files); t0 = time.time(); done = 0
    print("Paires USD a agreger : %d" % N, flush=True)
    for f in files:
        sym = norm_symbol(os.path.basename(f)[:-4])
        try:
            masters = aggregate_file(f, start_ts)
            for g in SEC:
                rows = to_rows(masters[g])
                if rows:
                    hp.compute_derived(rows); hp.write_store(sym, g, rows)
        except Exception as e:
            print("  ERR %s : %s" % (sym, e), flush=True); continue
        done += 1
        el = time.time() - t0
        eta = el / done * (N - done)
        print("  [%3d/%3d] %-12s %6d bougies 15m | %.0fs ecoules | ETA %.0fmin"
              % (done, N, sym, len(masters["15Min"]), el, eta / 60), flush=True)
    print("Termine : %d paires en %.1f min." % (done, (time.time() - t0) / 60), flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = sys.argv[1]
    yr = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
    uni = None
    if "univers" in sys.argv:
        import config
        uni = list(config.CRYPTO_PRIORITY)
    run(src, yr, uni)
