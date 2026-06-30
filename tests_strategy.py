"""Tests store de stratégie runtime (Lot 1 Ingénieur). python3 tests_strategy.py"""
import tempfile, pathlib, math
import strategy as S
import config


def _isolate():
    S.DATA = pathlib.Path(tempfile.mkdtemp())
    S.STORE = S.DATA / "s.json"; S.HIST = S.DATA / "h.json"
    S._CACHE["params"] = None


def t_store():
    _isolate()
    assert S.P1()["adx_min"] == config.PHASE1["adx_min"]            # vide = défaut
    r = S.set_overrides({"PHASE1": {"adx_min": 25, "SECRET": "x"}, "PHASE2": {"max_trades_per_day": 999}}, note="t")
    assert "PHASE1.SECRET" in r["dropped"]                          # allowlist
    assert S.P1()["adx_min"] == 25 and S.P2()["max_trades_per_day"] == 100  # override + borne
    v1 = r["version"]
    S.apply_diff({"PHASE2": {"cooldown_min_after_loss": 90}})
    assert S.P2()["cooldown_min_after_loss"] == 90 and S.P1()["adx_min"] == 25
    S.rollback(v1)
    assert S.P2()["cooldown_min_after_loss"] == config.PHASE2["cooldown_min_after_loss"]
    assert len(S.versions()) >= 3
    print("OK store (merge, allowlist, bornes, versioning, rollback)")


def t_runtime():
    _isolate()
    from signals import SignalEngine
    def mk(n=80):
        out = []; prev = 100.0
        for i in range(n):
            c = 100 + 0.2 * i + math.sin(i / 4)
            out.append({"o": prev, "h": max(prev, c) + .3, "l": min(prev, c) - .3, "c": c}); prev = c
        return out
    eng = SignalEngine(); eng.evaluate("BTC/USD", mk())
    assert eng.adx_min == config.PHASE1["adx_min"]
    S.set_overrides({"PHASE1": {"adx_min": 31}}, note="rt")
    eng.evaluate("BTC/USD", mk())
    assert eng.adx_min == 31                                        # pris au runtime, sans restart
    print("OK runtime (override appliqué sans redémarrage)")


if __name__ == "__main__":
    t_store(); t_runtime()
    print("\n=== Store de stratégie (Lot 1 Ingénieur) : tous les tests passent ===")
