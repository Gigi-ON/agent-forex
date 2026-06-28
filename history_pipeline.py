"""
Pipeline d'historique crypto — base d'entraînement (LECTURE / ANALYSE uniquement).

Flux : top 100 (univers) -> téléchargement des bougies Alpaca (1D / 1H / 15min,
depuis le listing, paginé) -> calcul variation % / log-returns / volatilité
réalisée -> écriture Parquet + CSV (magasin profond) + upsert Supabase
(journalier complet + fenêtre récente pour le fin).

Ré-exécutable / incrémental : on ne récupère que le nouveau depuis le dernier
ts stocké. Aucune exécution d'ordre — données seulement.

Usage :
    python history_pipeline.py verify          # vérifie les clés Alpaca
    python history_pipeline.py backfill         # historique profond (1er remplissage)
    python history_pipeline.py update           # passe rapide (timer 10 min)
"""
import json
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config

DATA_DIR = Path(__file__).parent / "data" / "history"
ALPACA_BARS = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
GRANS = config.CRYPTO_GRANULARITIES                 # {"1D":"1Day", ...}
DEEP_START = "2018-01-01T00:00:00Z"
# Fenêtre récente poussée vers Supabase pour les granularités fines (limite la taille)
SUPABASE_WINDOW = {"1D": 100000, "1H": 2000, "15Min": 2000}
# Fenêtre par défaut en mode update si aucun historique local
UPDATE_LOOKBACK = {"1D": 10, "1H": 3, "15Min": 1}    # en jours


# ---------------------------------------------------------------- Alpaca data
def _headers():
    return {"APCA-API-KEY-ID": config.ALPACA_PAPER_KEY,
            "APCA-API-SECRET-KEY": config.ALPACA_PAPER_SECRET}


def verify_alpaca():
    import requests
    try:
        r = requests.get(ALPACA_BARS, params={"symbols": "BTC/USD", "timeframe": "1Day", "limit": 1},
                         headers=_headers(), timeout=12)
        return r.status_code == 200 and "bars" in r.json()
    except Exception as e:
        print("verify_alpaca:", e)
        return False


def fetch_bars(symbol, timeframe, start, limit=10000):
    """Bougies Alpaca, paginées, triées ascendant."""
    import requests
    out, page = [], None
    while True:
        params = {"symbols": symbol, "timeframe": timeframe, "start": start,
                  "limit": limit, "sort": "asc"}
        if page:
            params["page_token"] = page
        r = requests.get(ALPACA_BARS, params=params, headers=_headers(), timeout=25)
        r.raise_for_status()
        j = r.json()
        out.extend((j.get("bars", {}) or {}).get(symbol, []) or [])
        page = j.get("next_page_token")
        if not page:
            break
    return out


# ---------------------------------------------------------- transformations (PURES)
def parse_bars(raw):
    return [{"ts": b["t"], "o": float(b["o"]), "h": float(b["h"]), "l": float(b["l"]),
             "c": float(b["c"]), "volume": float(b.get("v", 0) or 0)} for b in raw]


def compute_derived(rows, vol_window=20):
    """Ajoute ret_pct, log_ret, vol_realized. rows triés par ts ascendant."""
    prev, logs = None, []
    for i, r in enumerate(rows):
        if prev and prev > 0 and r["c"] > 0:
            r["ret_pct"] = round((r["c"] / prev - 1) * 100, 6)
            r["log_ret"] = round(math.log(r["c"] / prev), 6)
        else:
            r["ret_pct"] = None
            r["log_ret"] = None
        prev = r["c"]
        logs.append(r["log_ret"] if r["log_ret"] is not None else 0.0)
        win = logs[max(0, i - vol_window + 1):i + 1]
        if len(win) >= 2:
            m = sum(win) / len(win)
            var = sum((x - m) ** 2 for x in win) / (len(win) - 1)
            r["vol_realized"] = round(var ** 0.5, 6)
        else:
            r["vol_realized"] = None
    return rows


# ----------------------------------------------------------------- stockage fichier
def _pq(symbol, gran):
    return DATA_DIR / (symbol.replace("/", "-") + "_" + gran + ".parquet")


def last_ts(symbol, gran):
    import pandas as pd
    p = _pq(symbol, gran)
    if p.exists():
        df = pd.read_parquet(p)
        if len(df):
            return str(df["ts"].max())
    return None


def write_store(symbol, gran, rows):
    import pandas as pd
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _pq(symbol, gran)
    df_new = pd.DataFrame(rows)
    if p.exists():
        df = pd.concat([pd.read_parquet(p), df_new]).drop_duplicates(subset=["ts"], keep="last").sort_values("ts")
    else:
        df = df_new.sort_values("ts")
    df.to_parquet(p, index=False)
    df.to_csv(str(p)[:-8] + ".csv", index=False)
    return len(df)


# ------------------------------------------------------------------------ Supabase
def supabase_upsert(table, rows):
    import requests
    if not config.SUPABASE_SERVICE_KEY or not rows:
        return False
    url = config.SUPABASE_URL.rstrip("/") + "/rest/v1/" + table
    h = {"apikey": config.SUPABASE_SERVICE_KEY,
         "Authorization": "Bearer " + config.SUPABASE_SERVICE_KEY,
         "Content-Type": "application/json",
         "Prefer": "resolution=merge-duplicates,return=minimal"}
    for i in range(0, len(rows), 500):
        r = requests.post(url, headers=h, data=json.dumps(rows[i:i + 500]), timeout=40)
        r.raise_for_status()
    return True


# --------------------------------------------------------------------------- univers
def universe(n=100):
    return list(config.CRYPTO_PRIORITY)[:n]


def push_universe(syms):
    rows = [{"symbol": s, "rank": i + 1, "source": "alpaca"} for i, s in enumerate(syms)]
    supabase_upsert("crypto_universe", rows)


# ------------------------------------------------------------------------------- run
def _start_for(symbol, gran, mode):
    lt = last_ts(symbol, gran)
    if lt:
        return lt
    if mode == "backfill":
        return DEEP_START
    days = UPDATE_LOOKBACK.get(gran, 2)
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(mode="update"):
    syms = universe()
    push_universe(syms)
    total = 0
    for s in syms:
        for gran, tf in GRANS.items():
            try:
                rows = compute_derived(parse_bars(fetch_bars(s, tf, _start_for(s, gran, mode))))
                if not rows:
                    continue
                write_store(s, gran, rows)
                supa = [{"symbol": s, "granularity": gran,
                         "ts": r["ts"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"],
                         "volume": r["volume"], "ret_pct": r["ret_pct"],
                         "log_ret": r["log_ret"], "vol_realized": r["vol_realized"]} for r in rows]
                supa = supa[-SUPABASE_WINDOW.get(gran, 2000):]
                supabase_upsert("crypto_ohlc", supa)
                total += len(rows)
            except Exception as e:
                print("ERR", s, gran, "->", e)
    print("Pipeline %s terminé : %d bougies traitées sur %d cryptos." % (mode, total, len(syms)))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    if cmd == "verify":
        print("Alpaca data OK" if verify_alpaca() else "Alpaca data INDISPONIBLE (clés ou accès)")
    elif cmd in ("backfill", "update"):
        run(cmd)
    else:
        print(__doc__)
