"""Tests Autopilote-Trader (Lot 1). python3 tests_autopilot.py"""
import autopilot as A


class FakeMgr:
    MAX_CONCURRENT = 5
    def __init__(self): self.active = []


class FakeEngine:
    def __init__(self, forex_open=True):
        self.manager = FakeMgr(); self._fo = forex_open; self.opened = []
    def _forex_open(self, now): return self._fo
    def open_session(self, budget, accept_min=None, accept_max=None, profile=None,
                     risk_level=None, instrument=None, mode="pratique", trader="deterministe"):
        s = type("S", (), {})()
        s.id = str(len(self.opened) + 1); s.instrument = instrument; s.allocated = budget
        s.mode = mode; s.trader = trader; s.accept_max = accept_max
        self.manager.active.append(s); self.opened.append(s)
        return s


def _reset(**cfg):
    A._STATE["enabled"] = False
    A._STATE["config"] = A._clean({**A.DEFAULT_CONFIG, **cfg})
    A._STATE["journal"] = []; A._STATE["last_open_ts"] = 0.0; A._STATE["recent"] = {}
    A._save = lambda: None   # pas d'écriture disque en test


def t_disabled():
    _reset(markets="crypto")
    e = FakeEngine()
    assert A.step(e, now_ts=1000) is None and not e.opened
    print("OK désactivé (aucune ouverture)")


def t_open_and_spacing():
    _reset(markets="crypto", min_open_interval_sec=60, max_concurrent=3)
    e = FakeEngine()
    A.toggle(True, now_ts=999)
    s = A.step(e, now_ts=1000)
    assert s is not None and s.instrument == "BTC/USD" and getattr(s, "auto") is True
    assert s.accept_max == 1.0            # pas de veto haut la nuit
    # espacement : trop tôt -> rien
    assert A.step(e, now_ts=1030) is None and len(e.opened) == 1
    # après l'intervalle -> ouvre le suivant (ETH), BTC occupé
    s2 = A.step(e, now_ts=1061)
    assert s2 is not None and s2.instrument == "ETH/USD" and len(e.opened) == 2
    print("OK ouverture + espacement + dédup instrument")


def t_cap():
    _reset(markets="crypto", min_open_interval_sec=0, max_concurrent=2)
    e = FakeEngine()
    A.toggle(True, now_ts=0)
    A.step(e, now_ts=10); A.step(e, now_ts=20)
    assert len(e.opened) == 2
    assert A.step(e, now_ts=30) is None and len(e.opened) == 2   # plafond atteint
    print("OK plafond de sessions simultanées")


def t_reopen_cooldown():
    _reset(markets="crypto", min_open_interval_sec=0, max_concurrent=3, reopen_cooldown_sec=900)
    e = FakeEngine()
    A.toggle(True, now_ts=0)
    b = A.step(e, now_ts=100)     # BTC
    A.step(e, now_ts=106)         # ETH (>= 5s d'espacement)
    e.manager.active.remove(b)    # BTC "fermée"
    # dans le cooldown : BTC ignoré, ETH occupé -> rien
    assert A.step(e, now_ts=200) is None
    # après le cooldown : BTC rouvre
    s = A.step(e, now_ts=100 + 901)
    assert s is not None and s.instrument == "BTC/USD"
    print("OK cooldown de ré-ouverture par instrument")


def t_forex_gate():
    _reset(markets="forex", min_open_interval_sec=0)
    A.toggle(True, now_ts=0)
    closed = FakeEngine(forex_open=False)
    assert A.step(closed, now_ts=10) is None and not closed.opened   # forex fermé -> rien
    # crypto n'est pas bloqué par la fermeture forex
    _reset(markets="crypto", min_open_interval_sec=0)
    A.toggle(True, now_ts=0)
    e = FakeEngine(forex_open=False)
    assert A.step(e, now_ts=10) is not None
    print("OK garde de séance forex (crypto non bloqué)")


def t_paper_only_and_validation():
    c = A.set_config({"mode": "reel", "trader": "n'importe", "max_concurrent": 99,
                      "budget_per_session": -5, "accept_min": 2.0, "markets": "zzz"})
    assert c["mode"] == "apprentissage"        # jamais réel
    assert c["trader"] == "deterministe"
    assert c["max_concurrent"] == 5            # borné au plafond dur
    assert c["budget_per_session"] == 10       # borné bas
    assert c["accept_min"] == 0.90             # borné haut
    assert c["markets"] == "both"              # valeur invalide -> défaut
    print("OK paper-only + bornage config")


def t_kill():
    _reset(markets="crypto", min_open_interval_sec=0)
    e = FakeEngine()
    A.toggle(True, now_ts=0); A.step(e, now_ts=10)
    A.kill(now_ts=20)
    st = A.status(e)
    assert st["enabled"] is False
    assert A.step(e, now_ts=30) is None        # plus aucune ouverture
    assert st["journal"][0]["kind"] == "kill"
    print("OK kill-switch (coupe les ouvertures, conserve l'existant)")


if __name__ == "__main__":
    t_disabled(); t_open_and_spacing(); t_cap(); t_reopen_cooldown()
    t_forex_gate(); t_paper_only_and_validation(); t_kill()
    print("\n=== Autopilote-Trader (Lot 1) : tous les tests passent ===")
