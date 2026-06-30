"""
Store de stratégie au RUNTIME — permet de tuner PHASE1/PHASE2 sans redéploiement,
avec versioning + rollback. Source de vérité : data/strategy_store.json (overrides)
fusionné PAR-DESSUS les défauts de config.py. Si le store est vide → comportement
identique à config.py.

Sécurité : seules les clés de l'ALLOWLIST (PHASE1/PHASE2) sont acceptées ; toute
valeur numérique est bornée. Jamais de secrets / .env / LIVE_TRADING ici.
"""
import json
import threading
import time
from pathlib import Path

import config

DATA = Path(__file__).parent / "data"
STORE = DATA / "strategy_store.json"
HIST = DATA / "strategy_versions.json"
MAX_VERSIONS = 20

_LOCK = threading.Lock()
_CACHE = {"params": None}          # overrides courants (dict {PHASE1,PHASE2})

ALLOW = {"PHASE1": set(config.PHASE1.keys()), "PHASE2": set(config.PHASE2.keys())}
BOUNDS = {
    "PHASE1": {
        "adx_min": (5, 40), "adx_period": (5, 30), "htf_factor": (2, 16),
        "htf_ema_fast": (5, 60), "htf_ema_slow": (20, 200),
        "pullback_atr_mult": (0.3, 4.0), "swing_lookback": (3, 30),
        "swing_buffer_atr": (0.0, 2.0), "stop_min_atr": (1.0, 5.0),
        "stop_max_atr": (2.0, 10.0), "be_trigger_R": (0.3, 3.0),
        "be_buffer_R": (0.0, 0.5), "partial_trigger_R": (0.3, 3.0),
        "partial_frac": (0.0, 0.9), "trail_mult_R": (0.0, 5.0),
        "max_spread_frac": (0.05, 1.0),
    },
    "PHASE2": {
        "max_portfolio_heat_pct": (1.0, 20.0), "max_ccy_heat_pct": (0.5, 15.0),
        "derisk_floor": (0.2, 1.0), "derisk_step": (0.0, 0.6),
        "cooldown_min_after_loss": (0, 360), "max_trades_per_day": (1, 100),
        "min_minutes_between_same_pair": (0, 240),
    },
}


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def validate(params):
    """Filtre les clés hors allowlist (sécurité) et borne les valeurs numériques."""
    out, dropped = {}, []
    for sec in ("PHASE1", "PHASE2"):
        src = (params or {}).get(sec, {}) or {}
        clean = {}
        for k, v in src.items():
            if k not in ALLOW[sec]:
                dropped.append(sec + "." + k)
                continue
            b = BOUNDS.get(sec, {}).get(k)
            if b is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
                v = max(b[0], min(b[1], v))
                if isinstance(BOUNDS[sec][k][0], int) and isinstance(BOUNDS[sec][k][1], int):
                    v = int(round(v))
            clean[k] = v
        if clean:
            out[sec] = clean
    return out, dropped


def _store():
    with _LOCK:
        if _CACHE["params"] is None:
            st = _read(STORE, {})
            _CACHE["params"] = (st.get("params") or {}) if isinstance(st, dict) else {}
        return _CACHE["params"]


def P1():
    return {**config.PHASE1, **(_store().get("PHASE1", {}))}


def P2():
    return {**config.PHASE2, **(_store().get("PHASE2", {}))}


def current():
    st = _read(STORE, {})
    return {"version": st.get("version", 0) if isinstance(st, dict) else 0,
            "updated": st.get("updated") if isinstance(st, dict) else None,
            "overrides": _store(),
            "params": {"PHASE1": P1(), "PHASE2": P2()},
            "defaults": {"PHASE1": dict(config.PHASE1), "PHASE2": dict(config.PHASE2)}}


def versions():
    h = _read(HIST, [])
    return h if isinstance(h, list) else []


def set_overrides(overrides, note="", source="manuel"):
    """Écrit un nouvel ensemble d'overrides validés + crée une version."""
    clean, dropped = validate(overrides)
    st = _read(STORE, {})
    ver = (st.get("version", 0) if isinstance(st, dict) else 0) + 1
    new = {"version": ver, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "params": clean}
    with _LOCK:
        _write(STORE, new)
        _CACHE["params"] = clean
        h = versions()
        h.append({"version": ver, "ts": new["updated"], "params": clean,
                  "note": note, "source": source})
        _write(HIST, h[-MAX_VERSIONS:])
    return {"version": ver, "applied": clean, "dropped": dropped}


def apply_diff(diff, note="", source="ingenieur"):
    """Fusionne un diff par-dessus les overrides courants, valide, versionne."""
    merged = {"PHASE1": {**_store().get("PHASE1", {}), **((diff or {}).get("PHASE1", {}))},
              "PHASE2": {**_store().get("PHASE2", {}), **((diff or {}).get("PHASE2", {}))}}
    return set_overrides(merged, note=note, source=source)


def rollback(version):
    h = versions()
    target = next((v for v in h if v.get("version") == version), None)
    if not target:
        return {"error": "version %s introuvable" % version}
    res = set_overrides(target.get("params", {}), note="rollback vers v%s" % version, source="rollback")
    return {"rolled_back_to": version, "new_version": res["version"]}
