"""Tests monitoring (heartbeat + récap). python3 tests_monitoring.py"""
import monitoring as M


class Mgr:
    def __init__(self, n): self.active = list(range(n))


class Eng:
    def __init__(self):
        self.last_tick = "2026-06-30T12:00:00Z"; self.running = True; self.daily_halted = False
        self.manager = Mgr(2)
    def snapshot(self, now=None):
        return {"balance": 5000.0, "available": 4200.0, "day_pnl": 12.5,
                "daily_halted": False, "running": True,
                "sessions": [{"id": "1", "instrument": "BTC/USD", "mode": "pratique", "live_pnl": 3.2}]}


def t_heartbeat():
    d = M.heartbeat(Eng(), now=1000)
    assert d["ok"] and d["running"] and d["active_sessions"] == 2 and d["ts"] == 1000
    assert "autopilot" in d and "autotrainer" in d
    print("OK heartbeat (vivant, sessions, autopilote/auto-trainer)")


def t_recap():
    import decisions as D
    D._MEM[:] = []; D._LAST.clear(); D._save = lambda: None
    now = 1_700_000_000
    D.record("1", "BTC/USD", "buy", 0.7, 10, "auto", "validé", ts=now)
    D.record("2", "ETH/USD", "buy", 0.6, 10, "rejected", "corrélation", ts=now)
    md = M.recap_md(Eng(), now=now)
    assert md.startswith("# Récap quotidien") and "## Compte" in md
    assert "Sessions actives (1)" in md and "BTC/USD" in md
    assert "Décisions du jour (2)" in md and "Auto-validées : 1" in md and "Refusées : 1" in md
    assert "corrélation (1)" in md and "Paper" in md
    print("OK récap markdown (compte + sessions + décisions + note honnête)")


if __name__ == "__main__":
    t_heartbeat(); t_recap()
    print("\n=== Monitoring (heartbeat + récap) : tous les tests passent ===")
