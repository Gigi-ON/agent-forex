"""
Mise a jour CONTINUE de TOUT le centre de donnees (fondation des analyses).
Orchestre les pipelines CANONIQUES (aucun mapping de symbole maison) :
  - crypto spot     -> history_pipeline.run("update")   (Alpaca, incremental, format BTC/USD)
  - forex/metaux    -> OandaData().update_history()      (OANDA, incremental, format EUR_USD)
  - funding Kraken  -> funding_fetch.main()
  - funding profond -> coinalyze_funding.main()          (daily, 2020+)
  - capacity 8h+OI  -> capacity_fetch.main()
Puis HEALTH-CHECK par source ET par pair (toute pair manquante/perimee = FAIL nomme).
Concu pour cron 05:00. N'ecrit que dans data/*. Idempotent, incremental ("hier" seulement).
"""
import json
import os
import time
from datetime import datetime, timezone

import config

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FOREX_GRANS = ["M15", "H1", "D"]


def log(m):
    print(time.strftime("%H:%M:%S "), m, flush=True)


def _now():
    return datetime.now(timezone.utc)


def update_crypto_spot():
    try:
        import history_pipeline
        history_pipeline.run("update")
        return "ok"
    except Exception as e:
        log("crypto spot ERR: " + str(e)[:120]); return "err"


def update_forex():
    try:
        from oanda_data import OandaData
        from data_provider import normalize_pair
        od = OandaData(account="practice")
        ok = 0; err = 0
        for pair in config.FOREX_PRIORITY:
            p = normalize_pair(pair)
            for g in FOREX_GRANS:
                try:
                    od.update_history(p, g); ok += 1
                except Exception:
                    err += 1
        log("forex: %d maj, %d erreurs (%d pairs x %d grans)" % (ok, err, len(config.FOREX_PRIORITY), len(FOREX_GRANS)))
        return "ok" if ok else "err"
    except Exception as e:
        log("forex ERR: " + str(e)[:120]); return "err"


def _run_module(modname):
    try:
        mod = __import__(modname)
        mod.main()
        return "ok"
    except Exception as e:
        log("%s ERR: %s" % (modname, str(e)[:120])); return "err"


def _fresh_days(ts, max_days):
    try:
        import pandas as pd
        age = (_now() - pd.to_datetime(ts, utc=True)).total_seconds() / 86400.0
        return age <= max_days, age
    except Exception:
        return False, 1e9


def _api_live(fn):
    try:
        return bool(fn())
    except Exception:
        return False


def health_check():
    import glob
    import pandas as pd
    checks = {}

    def alpaca():
        import history_pipeline; return history_pipeline.verify_alpaca()

    def oanda():
        from oanda_data import OandaData
        return OandaData(account="practice").get_account_summary().get("id") is not None

    def kraken_fut():
        import urllib.request, json as _j
        u = "https://futures.kraken.com/derivatives/api/v4/historicalfundingrates?symbol=PF_XBTUSD"
        with urllib.request.urlopen(u, timeout=20) as r:
            return _j.loads(r.read().decode()).get("rates") is not None

    def coinalyze():
        try:
            import coinalyze_funding as CF
            key = CF.load_key()
            if not key:
                return False
            d = CF._get("/future-markets", {}, key)
            return isinstance(d, list) and len(d) > 0
        except Exception:
            return False

    checks["api_alpaca"] = "PASS" if _api_live(alpaca) else "FAIL"
    checks["api_oanda"] = "PASS" if _api_live(oanda) else "FAIL"
    checks["api_kraken_futures"] = "PASS" if _api_live(kraken_fut) else "FAIL"
    checks["api_coinalyze"] = "PASS" if _api_live(coinalyze) else "FAIL"

    hist = os.path.join(DATA, "history")
    stale_crypto = []
    for sym in getattr(config, "CRYPTO_PRIORITY", []):
        f = os.path.join(hist, sym.replace("/", "-") + "_1H.parquet")
        if not os.path.exists(f):
            stale_crypto.append(sym + "(absent)"); continue
        try:
            last = pd.read_parquet(f, columns=["ts"])["ts"].max()
            ok, age = _fresh_days(last, 2.0)
            if not ok:
                stale_crypto.append("%s(%.1fj)" % (sym, age))
        except Exception:
            stale_crypto.append(sym + "(illisible)")
    n_c = len(getattr(config, "CRYPTO_PRIORITY", []))
    checks["crypto_pairs"] = ("PASS %d/%d" % (n_c - len(stale_crypto), n_c)
                              + (" | STALE: " + ", ".join(stale_crypto[:8]) if stale_crypto else ""))

    stale_fx = []
    try:
        from cache import Cache
        from data_provider import normalize_pair
        c = Cache()
        for pair in getattr(config, "FOREX_PRIORITY", []):
            p = normalize_pair(pair)
            last = c.last_candle_time(p, "M15")
            ok, age = _fresh_days(last, 4.0) if last else (False, 1e9)
            if not ok:
                stale_fx.append(pair + ("(%.1fj)" % age if age < 1e8 else "(absent)"))
        n_f = len(getattr(config, "FOREX_PRIORITY", []))
        checks["forex_pairs"] = ("PASS %d/%d" % (n_f - len(stale_fx), n_f)
                                 + (" | STALE: " + ", ".join(stale_fx[:8]) if stale_fx else ""))
    except Exception as e:
        checks["forex_pairs"] = "FAIL (" + str(e)[:50] + ")"

    for d, mx in [("funding", 3), ("funding_multi", 3), ("capacity", 3)]:
        files = glob.glob(os.path.join(DATA, d, "*.parquet"))
        if not files:
            checks["data_" + d] = "FAIL (vide)"; continue
        try:
            f = max(files, key=os.path.getsize)
            last = pd.read_parquet(f)["ts"].max()
            ok, age = _fresh_days(last, mx)
            checks["data_" + d] = ("PASS (%.1fj)" % age) if ok else ("STALE (%.1fj)" % age)
        except Exception as e:
            checks["data_" + d] = "FAIL (" + str(e)[:40] + ")"

    verdict = "PASS" if all(str(v).startswith("PASS") for v in checks.values()) else "ATTENTION"
    checks["_verdict"] = verdict
    checks["_checked"] = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(DATA, "_health.json"), "w") as fh:
        json.dump(checks, fh, indent=2)
    log("HEALTH: " + verdict)
    for k, v in checks.items():
        if not k.startswith("_"):
            log("   %-20s %s" % (k, v))
    return verdict


def main():
    t0 = time.time()
    os.makedirs(DATA, exist_ok=True)
    log("=== MAJ centre de donnees : debut ===")
    res = {
        "crypto_spot": update_crypto_spot(),
        "forex": update_forex(),
        "funding_kraken": _run_module("funding_fetch"),
        "funding_multi": _run_module("coinalyze_funding"),
        "capacity": _run_module("capacity_fetch"),
    }
    verdict = health_check()
    stamp = {"updated": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "health": verdict,
             "sources": res, "duration_s": round(time.time() - t0)}
    with open(os.path.join(DATA, "_updated.json"), "w") as fh:
        json.dump(stamp, fh, indent=2)
    log("=== MAJ terminee en %ds === %s" % (round(time.time() - t0), json.dumps(res)))


if __name__ == "__main__":
    main()
