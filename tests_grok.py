"""Tests chasseur Grok + routage Trader. python3 tests_grok.py"""
import math
from datetime import datetime, timezone
import config
import grok_signals as G
from signals import Signal
from risk_manager import TradeProposal, Profile
from supervisor import Supervisor


class Resp:
    def __init__(self, p): self._p = p
    def json(self): return self._p


class Cli:
    def __init__(self, content): self.content = content; self.calls = 0
    def post(self, url, json=None, headers=None, **k):
        self.calls += 1
        return Resp({"choices": [{"message": {"content": self.content}}]})


def _candles(n=80):
    out = []; prev = 100.0
    for i in range(n):
        c = 100 + 0.2 * i + 1.0 * math.sin(i / 4)
        out.append({"o": prev, "h": max(prev, c) + 0.3, "l": min(prev, c) - 0.3, "c": c}); prev = c
    return out


def t_hunter():
    config.OPENROUTER_API_KEY = "sk-test"
    G._CACHE.clear(); G._CALLS.update(day=None, n=0)
    eng = G.GrokSignalEngine(client=Cli('{"action":"trade","side":"buy","confidence":0.7,"rationale":"momentum"}'))
    sig = eng.evaluate("BTC/USD", _candles())
    assert sig.proposal and sig.proposal.side == "buy" and sig.confidence == 0.7
    assert sig.proposal.stop_loss < sig.proposal.entry_price < sig.proposal.take_profit
    # wait + JSON invalide -> pas de proposition
    G._CACHE.clear()
    assert G.GrokSignalEngine(client=Cli('{"action":"wait"}')).evaluate("X/USD", _candles()).proposal is None
    G._CACHE.clear()
    assert G.GrokSignalEngine(client=Cli("pas json")).evaluate("X/USD", _candles()).proposal is None
    print("OK chasseur (trade ATR-stop, wait, JSON invalide)")


def t_routing():
    det_buy = lambda: Signal("BTC/USD", TradeProposal("BTC/USD", "buy", 100.0, 98.0, 104.0), 0.60, ["det"])
    grok_buy = lambda: Signal("BTC/USD", TradeProposal("BTC/USD", "buy", 100.0, 98.0, 104.0), 0.80, ["grok"])
    grok_sell = lambda: Signal("BTC/USD", TradeProposal("BTC/USD", "sell", 100.0, 102.0, 96.0), 0.80, ["grok"])

    class EngStub:
        def __init__(self, sig): self.sig = sig; self.calls = 0
        def evaluate(self, pair, candles): self.calls += 1; return self.sig

    class Mod:
        def assess(self, items, pair, now): return type("D", (), {"blackout": False, "caution_factor": 1.0})()

    def sess(trader, mode="apprentissage"):
        return type("S", (), {"id": "S1", "profile": Profile.DOUX, "equity": 500.0,
                              "accept_min": 0.55, "accept_max": 0.95, "risk_level": "doux",
                              "instrument": "BTC/USD", "mode": mode, "trader": trader})()
    now = datetime.now(timezone.utc)
    pf = {"open_risk": 0.0, "equity": 5000.0, "ccy_exposure": {}}

    # trader=grok -> moteur Grok utilisé, déterministe non
    sup = Supervisor(manager=None, engine=EngStub(Signal("BTC/USD", None, 0, ["det no"])), modulator=Mod())
    sup._grok_engine = EngStub(grok_buy())
    sup.propose(sess("grok"), "BTC/USD", _candles(), [], 1.0, 1.0, now=now, spread=0.0, portfolio=pf)
    assert sup._grok_engine.calls == 1 and sup.engine.calls == 0
    assert abs(sup.last_look["S1"]["conf"] - 0.80) < 1e-9
    print("OK routage grok (chasseur utilisé)")

    # hybride + Grok d'accord -> confiance boostée 0.60 -> 0.75
    sup = Supervisor(manager=None, engine=EngStub(det_buy()), modulator=Mod())
    sup._grok_engine = EngStub(grok_buy())
    sup.propose(sess("hybride"), "BTC/USD", _candles(), [], 1.0, 1.0, now=now, spread=0.0, portfolio=pf)
    assert abs(sup.last_look["S1"]["conf"] - 0.75) < 1e-9
    print("OK hybride (Grok confirme -> +0.15)")

    # hybride + Grok en désaccord -> confiance réduite 0.60 -> 0.45
    sup = Supervisor(manager=None, engine=EngStub(det_buy()), modulator=Mod())
    sup._grok_engine = EngStub(grok_sell())
    sup.propose(sess("hybride"), "BTC/USD", _candles(), [], 1.0, 1.0, now=now, spread=0.0, portfolio=pf)
    assert abs(sup.last_look["S1"]["conf"] - 0.45) < 1e-9
    print("OK hybride (Grok désaccord -> -0.15)")

    # GATE paper-only : grok + reel -> bascule déterministe (Grok jamais en réel)
    sup = Supervisor(manager=None, engine=EngStub(det_buy()), modulator=Mod())
    sup._grok_engine = EngStub(grok_buy())
    sup.propose(sess("grok", mode="reel"), "BTC/USD", _candles(), [], 1.0, 1.0, now=now, spread=0.0, portfolio=pf)
    assert sup.engine.calls == 1 and sup._grok_engine.calls == 0
    print("OK gate paper-only (Grok bloqué en Réel -> déterministe)")


if __name__ == "__main__":
    t_hunter(); t_routing()
    print("\n=== Chasseur Grok + routage Trader : tous les tests passent ===")
