"""Tests Ingénieur — workflow 3 étapes + backtest impact (Lot 2). python3 tests_ingenieur.py"""
import tempfile, pathlib, math
import config, strategy as S, ingenieur as I


def _iso():
    d = pathlib.Path(tempfile.mkdtemp())
    S.DATA = d; S.STORE = d / "s.json"; S.HIST = d / "h.json"; S._CACHE["params"] = None
    I.DATA = d; I.PROPS = d / "props.json"
    config.OPENROUTER_API_KEY = "sk-test"


class Resp:
    def __init__(self, p): self._p = p
    def json(self): return self._p


class Cli:
    def __init__(self, content): self.content = content
    def post(self, u, json=None, headers=None, **k): return Resp({"choices": [{"message": {"content": self.content}}]})


def t_workflow():
    _iso()
    cli = Cli('{"diff":{"PHASE1":{"adx_min":18,"SECRET":"x"},"PHASE2":{"max_trades_per_day":10}},"rationale":"flux","expected_impact":"+trades"}')
    p = I.propose({"j": 1}, session=cli)
    assert p["status"] == "proposee" and p["diff"]["PHASE1"]["adx_min"] == 18 and "PHASE1.SECRET" in p["dropped"]
    assert I.review(p["id"], "valider")["status"] == "validee"
    a = I.apply(p["id"]); assert a.get("applied") and S.P1()["adx_min"] == 18 and S.P2()["max_trades_per_day"] == 10
    assert "error" in I.apply(p["id"])               # pas de double application
    p2 = I.propose({"j": 1}, session=cli); I.review(p2["id"], "rejeter")
    assert "error" in I.apply(p2["id"])              # rejetée -> pas d'application
    print("OK workflow (proposer/valider/appliquer + allowlist + gardes)")


def t_backtest():
    _iso()
    def mk(inst):
        out = []; prev = 100.0
        for i in range(200):
            c = 100 + 0.2 * i + 1.2 * math.sin(i / 4)
            out.append({"o": prev, "h": max(prev, c) + .3, "l": min(prev, c) - .3, "c": c}); prev = c
        return out
    bi = I.backtest_impact({"PHASE1": {"adx_min": 25}}, ["BTC/USD"], fetch=mk)
    r = bi["impact"][0]
    assert r["instrument"] == "BTC/USD" and "avant" in r and "apres" in r
    print("OK backtest impact (avant/après par instrument)")


if __name__ == "__main__":
    t_workflow(); t_backtest()
    print("\n=== Ingénieur (Lot 2) : tous les tests passent ===")
