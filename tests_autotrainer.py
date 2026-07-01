"""Tests Auto-Trainer (Lot 2). python3 tests_autotrainer.py"""
import autotrainer as T


class Eng:
    def __init__(self, halted=False): self.daily_halted = halted


def _reset(**cfg):
    T._STATE["enabled"] = False
    T._STATE["config"] = T._clean({**T.DEFAULT_CONFIG, "interval_hours": 1, **cfg})
    T._STATE["journal"] = []; T._STATE["last_run_ts"] = 0.0
    T._STATE["changes_today"] = 0; T._STATE["day"] = None; T._STATE["last"] = None
    T._save = lambda: None


def _hooks(bt, overrides=None, applies=None, sets=None):
    applies = applies if applies is not None else []
    sets = sets if sets is not None else []
    def apply_diff(diff, note="", source=""):
        applies.append((diff, source)); return {"version": 7}
    def set_overrides(ov, note="", source=""):
        sets.append((ov, source)); return {"version": 8}
    return dict(bt=bt, cur_p1=lambda: {"tag": "cur"}, base_p1=lambda: {"tag": "base"},
                overrides_p1=lambda: (overrides or {}), apply_diff=apply_diff,
                set_overrides=set_overrides), applies, sets


def t_disabled_and_interval():
    _reset()
    assert T.step(now_ts=10, **_hooks(lambda p1: (0.0, 100))[0]) is None
    T.toggle(True, ts=0)
    # intervalle 1h : à t=10 (last_run=0) -> pas encore
    r = T.step(now_ts=10, **_hooks(lambda p1: (0.0, 100))[0])
    assert r is None
    print("OK désactivé + intervalle respecté")


def t_apply_best_candidate():
    _reset(); T.toggle(True, ts=0)
    def bt(p1):
        if p1.get("adx_min") == 22.0: return (0.20, 100)   # candidat gagnant
        return (0.0, 100)
    h, applies, sets = _hooks(bt)
    r = T.step(now_ts=4000, **h)   # > 3600 -> passe autorisée
    assert r and r["applied"] and r["version"] == 7 and abs(r["delta"] - 0.20) < 1e-9
    assert applies and applies[0][0]["PHASE1"]["adx_min"] == 22.0 and applies[0][1] == "auto-trainer"
    assert T._STATE["changes_today"] == 1 and T._STATE["last"]["version"] == 7
    print("OK applique le meilleur candidat (Δ ≥ seuil, versionné)")


def t_quota():
    _reset(max_changes_per_day=1); T.toggle(True, ts=0)
    def bt(p1): return (0.20 if p1.get("adx_min") == 22.0 else 0.0, 100)
    h, applies, _ = _hooks(bt)
    T.step(now_ts=4000, **h)                     # applique
    r = T.step(now_ts=8000, **h)                 # quota atteint
    assert r is None and len(applies) == 1 and T._STATE["journal"][0]["kind"] == "skip"
    print("OK rate-limit (max changements/jour)")


def t_reject_below_threshold():
    _reset(); T.toggle(True, ts=0)
    h, applies, _ = _hooks(lambda p1: (0.0, 100))   # rien ne bat le courant
    r = T.step(now_ts=4000, **h)
    assert r is None and not applies and T._STATE["journal"][0]["kind"] == "reject"
    print("OK rejet si aucun candidat au-dessus du seuil")


def t_min_trades_guard():
    _reset(min_trades=50); T.toggle(True, ts=0)
    h, applies, _ = _hooks(lambda p1: (0.9, 10))    # gros exp mais trop peu de trades
    r = T.step(now_ts=4000, **h)
    assert r is None and not applies and T._STATE["journal"][0]["kind"] == "skip"
    print("OK garde nb de trades minimum (jugement suspendu)")


def t_rollback_circuit():
    _reset(); T.toggle(True, ts=0)
    T._STATE["last"] = {"version": 7, "prev_overrides": {"PHASE1": {"adx_min": 20.0}}}
    h, applies, sets = _hooks(lambda p1: (0.0, 100))
    r = T.step(engine=Eng(halted=True), now_ts=4000, **h)
    assert r == {"rollback": "circuit"} and sets and sets[0][1] == "auto-trainer"
    assert sets[0][0] == {"PHASE1": {"adx_min": 20.0}} and T._STATE["last"] is None
    print("OK rollback de sécurité (coupe-circuit)")


def t_rollback_revalidation():
    _reset(); T.toggle(True, ts=0)
    def bt(p1): return (0.0, 100) if p1.get("tag") == "cur" else (0.30, 100)  # défauts >> courant
    h, applies, sets = _hooks(bt, overrides={"adx_min": 40.0})
    r = T.step(now_ts=4000, **h)
    assert r == {"rollback": "revalidation"} and sets and sets[0][0] == {}
    print("OK rollback de re-validation (sous la config par défaut)")


def t_validation_and_kill():
    c = T.set_config({"interval_hours": 999, "min_delta": 9, "max_changes_per_day": 99,
                      "source": "zzz", "min_trades": 0})
    assert c["interval_hours"] == 168 and c["min_delta"] == 2.0
    assert c["max_changes_per_day"] == 20 and c["source"] == "grid" and c["min_trades"] == 1
    _reset(); T.toggle(True, ts=0); T.kill(ts=1)
    assert T._STATE["enabled"] is False
    assert T.step(now_ts=9999, **_hooks(lambda p1: (0.5, 100))[0]) is None
    print("OK bornage config + kill-switch")


if __name__ == "__main__":
    t_disabled_and_interval(); t_apply_best_candidate(); t_quota(); t_reject_below_threshold()
    t_min_trades_guard(); t_rollback_circuit(); t_rollback_revalidation(); t_validation_and_kill()
    print("\n=== Auto-Trainer (Lot 2) : tous les tests passent ===")
