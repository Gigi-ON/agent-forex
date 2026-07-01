"""
Auto-Trainer (Lot 2) — l'Ingénieur en BOUCLE : propose des réglages, backteste
AVANT/APRÈS, et n'APPLIQUE QUE si l'espérance s'améliore au-delà d'un seuil.
Versionné et réversible (rollback).

Garde-fous (godmode borné) :
  - N'écrit que la STRATÉGIE runtime (PHASE1/PHASE2 via strategy.py, allowlist +
    bornes). Jamais de secrets/.env/LIVE_TRADING.
  - Gate backtest : Δ espérance ≥ min_delta (R/trade) ET nb de trades suffisant.
  - Rate-limit : max N changements/jour. Intervalle mini entre passes.
  - Rollback automatique :
      (a) sécurité — si le coupe-circuit journalier s'est déclenché depuis le
          dernier changement, on revient à l'état d'avant ;
      (b) re-validation — si la config accumulée (defaults+overrides) fait MOINS
          bien que la config par défaut au backtest, on revient aux défauts.
  - Kill-switch + persistance de l'état.

Note d'honnêteté : le rollback « live » idéal (espérance réelle par version sur
M trades) demande d'étiqueter chaque trade par version — prévu en affinage. Ici
on s'appuie sur des déclencheurs mesurables tout de suite (coupe-circuit +
re-validation backtest).
"""
import json
import threading
import time as _time
from pathlib import Path

import config

_LOCK = threading.Lock()
_DATA = Path(__file__).parent / "data"
STATE_FILE = _DATA / "autotrainer.json"

SOURCES = ("grid", "ingenieur")

DEFAULT_CONFIG = {
    "interval_hours": 24,
    "min_delta": 0.05,        # amélioration d'espérance requise (R/trade)
    "min_trades": 20,         # trades mini au backtest pour juger
    "max_changes_per_day": 1,
    "instruments": ["BTC/USD", "ETH/USD", "EUR_USD"],
    "source": "grid",         # grid (déterministe, sans coût) | ingenieur (Claude)
}

# Grille déterministe de candidats (petits pas sur les tunables PHASE1).
GRID = [
    {"PHASE1": {"adx_min": 18.0}}, {"PHASE1": {"adx_min": 22.0}}, {"PHASE1": {"adx_min": 25.0}},
    {"PHASE1": {"pullback_atr_mult": 1.2}}, {"PHASE1": {"pullback_atr_mult": 2.0}},
    {"PHASE1": {"stop_min_atr": 1.5}}, {"PHASE1": {"stop_max_atr": 3.5}},
    {"PHASE1": {"swing_lookback": 8}}, {"PHASE1": {"swing_lookback": 14}},
]

_STATE = {"enabled": False, "config": dict(DEFAULT_CONFIG),
          "journal": [], "last_run_ts": 0.0, "changes_today": 0, "day": None,
          "last": None}    # last = {version, prev_overrides, delta, ts}


# ------------------------------------------------------------------ persistance
def _load():
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        _STATE["enabled"] = bool(d.get("enabled", False))
        cfg = dict(DEFAULT_CONFIG); cfg.update(d.get("config", {}) or {})
        _STATE["config"] = _clean(cfg)
        _STATE["last"] = d.get("last")
    except Exception:
        pass


def _save():
    try:
        _DATA.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(
            {"enabled": _STATE["enabled"], "config": _STATE["config"], "last": _STATE["last"]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------------------------ validation
def _clamp(v, lo, hi, d):
    try:
        v = float(v)
    except Exception:
        return d
    return max(lo, min(hi, v))


def _clean(cfg):
    out = dict(DEFAULT_CONFIG)
    out.update({k: cfg[k] for k in cfg if k in DEFAULT_CONFIG})
    out["interval_hours"] = _clamp(out["interval_hours"], 1, 168, 24)
    out["min_delta"] = _clamp(out["min_delta"], 0.0, 2.0, 0.05)
    out["min_trades"] = int(_clamp(out["min_trades"], 1, 100000, 20))
    out["max_changes_per_day"] = int(_clamp(out["max_changes_per_day"], 1, 20, 1))
    out["source"] = out["source"] if out["source"] in SOURCES else "grid"
    insts = out.get("instruments") or DEFAULT_CONFIG["instruments"]
    out["instruments"] = [str(x) for x in insts][:12] if isinstance(insts, list) else DEFAULT_CONFIG["instruments"]
    return out


# ------------------------------------------------------------------ journal/API
def _log(kind, msg, ts=None):
    _STATE["journal"].insert(0, {"ts": ts or _time.time(), "kind": kind, "msg": msg})
    del _STATE["journal"][50:]


def status():
    import strategy
    try:
        ver = strategy.current().get("version", 0)
    except Exception:
        ver = 0
    return {"enabled": _STATE["enabled"], "config": dict(_STATE["config"]),
            "journal": list(_STATE["journal"][:20]),
            "changes_today": _STATE["changes_today"], "version": ver,
            "last": _STATE["last"]}


def set_config(patch):
    with _LOCK:
        cfg = dict(_STATE["config"]); cfg.update({k: v for k, v in (patch or {}).items() if k in DEFAULT_CONFIG})
        _STATE["config"] = _clean(cfg); _save()
        return dict(_STATE["config"])


def toggle(on, ts=None):
    with _LOCK:
        _STATE["enabled"] = bool(on)
        _log("toggle", "Auto-Trainer %s" % ("ACTIVÉ" if on else "désactivé"), ts); _save()
        return _STATE["enabled"]


def kill(ts=None):
    with _LOCK:
        _STATE["enabled"] = False
        _log("kill", "KILL-SWITCH — plus aucun réglage automatique", ts); _save()
        return True


def _roll_day(ts):
    day = _time.strftime("%Y-%m-%d", _time.gmtime(ts))
    if _STATE["day"] != day:
        _STATE["day"] = day; _STATE["changes_today"] = 0


# ------------------------------------------------------------------ backtest
def _real_bt(p1, instruments):
    """Espérance moyenne (R/trade) et nb total de trades pour un jeu PHASE1."""
    from signals import SignalEngine
    from backtest_signals import backtest, _fetch
    TUN = ("adx_min", "pullback_atr_mult", "swing_lookback", "swing_buffer_atr",
           "stop_min_atr", "stop_max_atr")
    e = SignalEngine(use_store=False)
    for k in TUN:
        if k in p1:
            setattr(e, k, p1[k])
    tot_r, n = 0.0, 0
    for inst in instruments:
        try:
            c = _fetch(inst)
            for t in backtest(c, inst, engine=e):
                tot_r += t["R"]; n += 1
        except Exception:
            continue
    return (round(tot_r / n, 4) if n else 0.0, n)


# ------------------------------------------------------------------ boucle
def step(engine=None, now_ts=None, *, bt=None, cur_p1=None, base_p1=None,
         overrides_p1=None, apply_diff=None, set_overrides=None, propose=None):
    if not _STATE["enabled"]:
        return None
    import strategy
    cfg = _STATE["config"]
    now_ts = _time.time() if now_ts is None else now_ts
    if now_ts - _STATE["last_run_ts"] < cfg["interval_hours"] * 3600:
        return None
    _STATE["last_run_ts"] = now_ts
    _roll_day(now_ts)

    bt = bt or (lambda p1: _real_bt(p1, cfg["instruments"]))
    cur_p1 = cur_p1 or strategy.P1
    base_p1 = base_p1 or (lambda: dict(config.PHASE1))
    overrides_p1 = overrides_p1 or (lambda: (strategy.current().get("overrides") or {}).get("PHASE1", {}))
    apply_diff = apply_diff or strategy.apply_diff
    set_overrides = set_overrides or strategy.set_overrides

    try:
        # (a) rollback de sécurité : coupe-circuit journalier depuis le dernier changement
        if engine is not None and getattr(engine, "daily_halted", False) and _STATE["last"]:
            prev = _STATE["last"].get("prev_overrides") or {}
            set_overrides(prev, note="auto-rollback (coupe-circuit)", source="auto-trainer")
            _log("rollback", "Coupe-circuit journalier → retour à l'état d'avant le dernier réglage", now_ts)
            _STATE["last"] = None; _save()
            return {"rollback": "circuit"}

        cur = cur_p1()
        cur_exp, cur_n = bt(cur)

        # (b) rollback de re-validation : overrides sous le stock -> retour défauts
        if overrides_p1() and cur_n >= cfg["min_trades"]:
            base_exp, base_n = bt(base_p1())
            if base_n >= cfg["min_trades"] and cur_exp < base_exp - cfg["min_delta"]:
                set_overrides({}, note="auto-rollback (sous la config par défaut)", source="auto-trainer")
                _log("rollback", "Config accumulée sous le stock (%.3f < %.3f) → retour aux défauts"
                     % (cur_exp, base_exp), now_ts)
                _STATE["last"] = None; _save()
                return {"rollback": "revalidation"}

        # quota
        if _STATE["changes_today"] >= cfg["max_changes_per_day"]:
            _log("skip", "Quota de changements du jour atteint (%d)" % cfg["max_changes_per_day"], now_ts)
            return None
        if cur_n < cfg["min_trades"]:
            _log("skip", "Trop peu de trades au backtest (%d < %d) — jugement suspendu" % (cur_n, cfg["min_trades"]), now_ts)
            return None

        # candidats
        cands = _candidates(cfg, propose, engine)
        best, best_d = None, 0.0
        for diff in cands:
            p1n = {**cur, **((diff or {}).get("PHASE1") or {})}
            exp_n, n_n = bt(p1n)
            if n_n < cfg["min_trades"]:
                continue
            d = exp_n - cur_exp
            if d > best_d:
                best, best_d = diff, d

        if best is not None and best_d >= cfg["min_delta"]:
            prev_overrides = dict(overrides_p1() or {})
            res = apply_diff(best, note="auto-trainer Δ+%.3f R" % best_d, source="auto-trainer")
            ver = res.get("version") if isinstance(res, dict) else None
            _STATE["changes_today"] += 1
            _STATE["last"] = {"version": ver, "prev_overrides": {"PHASE1": prev_overrides},
                              "delta": round(best_d, 4), "ts": now_ts}
            _log("apply", "Réglage appliqué (Δ +%.3f R/trade) → version %s : %s"
                 % (best_d, ver, json.dumps(best.get("PHASE1", {}), ensure_ascii=False)), now_ts)
            _save()
            return {"applied": True, "version": ver, "delta": round(best_d, 4)}

        _log("reject", "Aucun candidat au-dessus du seuil (meilleur Δ +%.3f < %.3f)"
             % (best_d, cfg["min_delta"]), now_ts)
        return None
    except Exception as e:
        _log("error", "Passe ignorée : %s" % e, now_ts)
        return None


def _candidates(cfg, propose, engine):
    if cfg["source"] == "ingenieur":
        propose = propose or _ingenieur_propose
        try:
            p = propose(engine)
            if isinstance(p, dict) and not p.get("error") and (p.get("diff")):
                return [p["diff"]]
        except Exception:
            pass
        return []
    return list(GRID)


def _ingenieur_propose(engine):
    import ingenieur
    ctx = {"note": "auto-trainer", "instruments": _STATE["config"]["instruments"]}
    return ingenieur.propose(ctx)


_load()
