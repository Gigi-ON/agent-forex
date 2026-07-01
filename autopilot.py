"""
Autopilote-Trader (Lot 1) — maintient N sessions actives sur les meilleurs
marchés OUVERTS (crypto 24/7 + forex en séance), en PAPER uniquement.

Philosophie inchangée : l'autopilote ne fait qu'OUVRIR des sessions ; ce sont
ensuite le superviseur, le risk manager et les garde-fous qui décident des
trades. Il n'exécute jamais d'ordre lui-même et ne touche jamais au Réel.

Sécurité :
  - PAPER only : mode forcé dans {pratique, apprentissage} ('reel' -> 'apprentissage').
  - Respecte le plafond dur de sessions simultanées (SessionManager.MAX_CONCURRENT).
  - Espacement des ouvertures + cooldown de ré-ouverture par instrument.
  - Kill-switch : coupe toute nouvelle ouverture (les sessions en cours gardent
    leur SL/TP — on ne ferme rien brutalement).
  - Config + état persistés (survit à un redémarrage).
"""
import json
import threading
import time as _time
from pathlib import Path

import config

_LOCK = threading.Lock()
_DATA = Path(__file__).parent / "data"
STATE_FILE = _DATA / "autopilot.json"

MODES_PAPER = ("pratique", "apprentissage")
TRADERS = ("deterministe", "grok", "hybride")
MARKETS = ("crypto", "forex", "both")

DEFAULT_CONFIG = {
    "max_concurrent": 3,          # borné à SessionManager.MAX_CONCURRENT
    "mode": "pratique",           # paper only
    "trader": "deterministe",
    "profil": "reserve",
    "budget_per_session": 500.0,
    "markets": "both",
    "accept_min": 0.55,           # bande basse ; haute = 1.0 (pas de veto haut la nuit)
    "min_open_interval_sec": 60,  # espacement entre deux ouvertures
    "reopen_cooldown_sec": 900,   # ne pas rouvrir le même instrument avant 15 min
}

_STATE = {"enabled": False, "config": dict(DEFAULT_CONFIG),
          "journal": [], "last_open_ts": 0.0, "recent": {}}


# ------------------------------------------------------------------ persistance
def _load():
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        _STATE["enabled"] = bool(d.get("enabled", False))
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(d.get("config", {}) or {})
        _STATE["config"] = _clean(cfg)
    except Exception:
        pass


def _save():
    try:
        _DATA.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(
            {"enabled": _STATE["enabled"], "config": _STATE["config"]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------------------------ validation
def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except Exception:
        return default
    return max(lo, min(hi, v))


def _clean(cfg):
    """Borne/valide une config (paper-only, plafonds durs)."""
    out = dict(DEFAULT_CONFIG)
    out.update({k: cfg[k] for k in cfg if k in DEFAULT_CONFIG})
    out["max_concurrent"] = int(_clamp(out["max_concurrent"], 1, 5, 3))
    out["budget_per_session"] = _clamp(out["budget_per_session"], 10, 100000, 500.0)
    out["accept_min"] = _clamp(out["accept_min"], 0.30, 0.90, 0.55)
    out["min_open_interval_sec"] = int(_clamp(out["min_open_interval_sec"], 5, 3600, 60))
    out["reopen_cooldown_sec"] = int(_clamp(out["reopen_cooldown_sec"], 60, 86400, 900))
    m = str(out.get("mode", "pratique")).lower()
    out["mode"] = m if m in MODES_PAPER else "apprentissage"     # 'reel' -> apprentissage
    out["trader"] = out["trader"] if out["trader"] in TRADERS else "deterministe"
    out["markets"] = out["markets"] if out["markets"] in MARKETS else "both"
    out["profil"] = str(out.get("profil", "reserve"))
    return out


# ------------------------------------------------------------------ journal/API
def _log(kind, msg, now_ts=None):
    _STATE["journal"].insert(0, {"ts": now_ts or _time.time(), "kind": kind, "msg": msg})
    del _STATE["journal"][50:]


def status(engine=None):
    active_auto = 0
    if engine is not None:
        try:
            active_auto = sum(1 for s in engine.manager.active if getattr(s, "auto", False))
        except Exception:
            active_auto = 0
    return {"enabled": _STATE["enabled"], "config": dict(_STATE["config"]),
            "journal": list(_STATE["journal"][:20]), "active_auto": active_auto}


def set_config(patch):
    with _LOCK:
        cfg = dict(_STATE["config"])
        cfg.update({k: v for k, v in (patch or {}).items() if k in DEFAULT_CONFIG})
        _STATE["config"] = _clean(cfg)
        _save()
        return dict(_STATE["config"])


def toggle(on, now_ts=None):
    with _LOCK:
        _STATE["enabled"] = bool(on)
        _log("toggle", "Autopilote %s" % ("ACTIVÉ" if on else "désactivé"), now_ts)
        _save()
        return _STATE["enabled"]


def kill(now_ts=None):
    with _LOCK:
        _STATE["enabled"] = False
        _log("kill", "KILL-SWITCH — aucune nouvelle ouverture (sessions en cours conservées)", now_ts)
        _save()
        return True


# ------------------------------------------------------------------ sélection
def _candidates(engine, now, cfg, busy):
    out = []
    if cfg["markets"] in ("crypto", "both"):
        for inst in getattr(config, "CRYPTO_INSTRUMENTS", ["BTC/USD", "ETH/USD"]):
            if inst not in busy:
                out.append((inst, "crypto"))
    if cfg["markets"] in ("forex", "both"):
        try:
            if engine._forex_open(now):
                import sessions_clock as sc
                open_set = sc.open_sessions(sc._now_utc(now))
                for r in sc.rank_pairs(list(config.FOREX_PRIORITY), open_set, top=8):
                    disp = r["pair"]
                    if disp not in busy and disp.replace("/", "_") not in busy:
                        out.append((disp, "forex"))
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ boucle
def step(engine, now=None, now_ts=None):
    """Appelée à chaque tick. Ouvre AU PLUS une session par pas, si un créneau
    se libère et que l'espacement/cooldown le permet. Best-effort, non bloquant."""
    if not _STATE["enabled"]:
        return None
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    now_ts = _time.time() if now_ts is None else now_ts
    cfg = _STATE["config"]
    try:
        active = engine.manager.active
        cap = min(cfg["max_concurrent"], getattr(engine.manager, "MAX_CONCURRENT", 5))
        if len(active) >= cap:
            return None
        if now_ts - _STATE["last_open_ts"] < cfg["min_open_interval_sec"]:
            return None
        busy = {getattr(s, "instrument", None) for s in active}
        from session import Profile
        for inst, asset in _candidates(engine, now, cfg, busy):
            last = _STATE["recent"].get(inst)
            if last is not None and now_ts - last < cfg["reopen_cooldown_sec"]:
                continue
            try:
                s = engine.open_session(
                    budget=cfg["budget_per_session"],
                    accept_min=cfg["accept_min"], accept_max=1.0,
                    profile=Profile(cfg["profil"]) if _is_profile(cfg["profil"]) else Profile.RESERVE,
                    risk_level=cfg["profil"],
                    instrument=(inst.replace("/", "_") if asset == "forex" else inst),
                    mode=cfg["mode"], trader=cfg["trader"])
                setattr(s, "auto", True)
                _STATE["last_open_ts"] = now_ts
                _STATE["recent"][inst] = now_ts
                _log("open", "Ouvert %s · %s · %s · %d$" %
                     (inst, cfg["mode"], cfg["trader"], int(cfg["budget_per_session"])), now_ts)
                _save()
                return s
            except Exception as e:
                _log("skip", "Ouverture %s ignorée : %s" % (inst, e), now_ts)
                continue
    except Exception:
        return None
    return None


def _is_profile(v):
    try:
        from session import Profile
        Profile(v)
        return True
    except Exception:
        return False


_load()
