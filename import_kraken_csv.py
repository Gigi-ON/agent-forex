"""
Importeur des CSV historiques Kraken (OHLCVT telechargeables) -> notre magasin
Parquet (data/history/{SYMBOLE}_{gran}.parquet), meme schema que le pipeline
Alpaca, donc FUSION propre (dedoublonnage par ts).

Format CSV Kraken OHLCVT (sans entete) : timestamp(unix s), open, high, low,
close, volume, trades   (nom de fichier : PAIRE_intervalleMinutes.csv, ex XBTUSD_15.csv)

Usage (VPS, python du venv) :
    python3 import_kraken_csv.py /chemin/vers/csv_kraken           # importe tout l'USD
    python3 import_kraken_csv.py /chemin/vers/csv_kraken univers   # filtre au top-100
"""
import csv
import glob
import os
import sys
from datetime import datetime, timezone

import history_pipeline as hp

# intervalle (minutes) Kraken -> notre granularite
IVAL = {"15": "15Min", "60": "1H", "1440": "1D"}


def norm_symbol(pair):
    """Code paire Kraken -> 'BASE/USD' (USD uniquement), sinon None."""
    p = pair.upper()
    if p.endswith("ZUSD"):
        base = p[:-4]
    elif p.endswith("USD"):
        base = p[:-3]
    else:
        return None
    base = {"XBT": "BTC", "XXBT": "BTC", "XXDG": "DOGE", "XDG": "DOGE"}.get(base, base)
    if len(base) == 4 and base[0] == "X":     # legacy X-prefix (XETH->ETH)
        base = base[1:]
    return base + "/USD" if base else None


def _iso(ts_unix):
    return datetime.fromtimestamp(int(float(ts_unix)), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv_rows(path):
    rows = []
    with open(path, newline="") as fh:
        for line in csv.reader(fh):
            if len(line) < 6:
                continue
            try:
                vol = float(line[6]) if len(line) >= 8 else float(line[5])
                rows.append({"ts": _iso(line[0]), "o": float(line[1]), "h": float(line[2]),
                             "l": float(line[3]), "c": float(line[4]), "volume": vol})
            except Exception:
                continue
    rows.sort(key=lambda r: r["ts"])
    return rows


def import_dir(src, universe=None):
    only = set(universe) if universe else None
    files = sorted(glob.glob(os.path.join(src, "*_*.csv")))
    done = tot = 0
    for f in files:
        name = os.path.basename(f)[:-4]
        if "_" not in name:
            continue
        pair, iv = name.rsplit("_", 1)
        if iv not in IVAL:
            continue
        sym = norm_symbol(pair)
        if not sym or (only and sym not in only):
            continue
        rows = read_csv_rows(f)
        if not rows:
            continue
        hp.compute_derived(rows)
        n = hp.write_store(sym, IVAL[iv], rows)
        done += 1; tot += len(rows)
        print("  %-12s %-6s <- %-22s %8d lignes (store=%d)" % (sym, IVAL[iv], name, len(rows), n))
    print("Importe : %d fichiers, %d bougies. (data/history/)" % (done, tot))
    return done


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = sys.argv[1]
    uni = None
    if len(sys.argv) > 2 and sys.argv[2] == "univers":
        import config
        uni = list(config.CRYPTO_PRIORITY)
    import_dir(src, uni)
