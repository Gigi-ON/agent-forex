"""
Registre léger du statut & des quotas des fournisseurs externes (OANDA / Alpaca /
Kraken). Pas de dépendance réseau ici : on enregistre ce que les appels rapportent.

  - Alpaca renvoie les en-têtes X-RateLimit-Limit/-Remaining -> quota exact.
  - OANDA / Kraken n'exposent pas de quota exploitable -> on compte NOS appels
    (suivi) face à la limite documentée, plus le dernier statut/latence.
"""
import threading
import time

_LOCK = threading.Lock()
STATS = {}


def _default():
    return {"calls": 0, "status": None, "latency_ms": None, "error": None,
            "limit": None, "remaining": None, "ts": None}


def record(provider, ok=True, latency_ms=None, error=None, limit=None, remaining=None):
    with _LOCK:
        s = STATS.setdefault(provider, _default())
        s["calls"] += 1
        s["status"] = "ok" if ok else "down"
        if latency_ms is not None:
            s["latency_ms"] = round(latency_ms)
        s["error"] = error
        if limit is not None:
            s["limit"] = limit
        if remaining is not None:
            s["remaining"] = remaining
        s["ts"] = time.time()


def snapshot():
    with _LOCK:
        return {k: dict(v) for k, v in STATS.items()}


def timed(provider, fn, **rec):
    """Exécute fn(), chronomètre, enregistre le statut. Renvoie (résultat | None, ok)."""
    t0 = time.time()
    try:
        r = fn()
        record(provider, ok=True, latency_ms=(time.time() - t0) * 1000.0, **rec)
        return r, True
    except Exception as e:
        record(provider, ok=False, latency_ms=(time.time() - t0) * 1000.0, error=str(e)[:140])
        return None, False
