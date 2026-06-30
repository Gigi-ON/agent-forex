"""Tests Lots 1+2 backend — comptes & statut providers. python3 tests_providers.py"""
import providers as P
import execution as X


class Resp:
    def __init__(self, payload, headers=None): self._p = payload; self.headers = headers or {}
    def json(self): return self._p


class FakeAlpacaAcc:
    def get(self, url, **k):
        if "account" in url:
            return Resp({"equity": "100000.5", "cash": "99000", "buying_power": "200000",
                         "currency": "USD", "status": "ACTIVE"},
                        headers={"X-RateLimit-Limit": "200", "X-RateLimit-Remaining": "187"})
        return Resp([])
    def post(self, *a, **k): return Resp({"id": "o"})
    def delete(self, *a, **k): return Resp({})


def t_alpaca_account():
    ex = X.AlpacaExecutor("paper", session=FakeAlpacaAcc(), key="k", secret="s")
    a = ex.account()
    assert abs(a["equity"] - 100000.5) < 1e-6 and abs(a["cash"] - 99000) < 1e-6
    assert a["rate_limit"] == 200 and a["rate_remaining"] == 187 and a["currency"] == "USD"
    print("OK Alpaca account (equity/cash/buying_power + en-têtes quota)")


def t_oanda_account():
    class Cli:
        def account_summary(self): return {"nav": 100000.0, "balance": 99500.0, "currency": "CAD", "open_trades": 2}
    ex = X.OandaExecutor("practice", client=Cli())
    a = ex.account()
    assert a["nav"] == 100000.0 and a["currency"] == "CAD" and a["open_trades"] == 2
    print("OK OANDA account (nav/balance/devise/trades)")


def t_providers():
    P.STATS.clear()
    r, ok = P.timed("alpaca", lambda: 42); assert ok and r == 42
    r, ok = P.timed("oanda", lambda: (_ for _ in ()).throw(RuntimeError("boom"))); assert (not ok) and r is None
    snap = P.snapshot()
    assert snap["alpaca"]["status"] == "ok" and snap["alpaca"]["calls"] == 1
    assert snap["oanda"]["status"] == "down" and "boom" in snap["oanda"]["error"]
    P.record("alpaca", ok=True, limit=200, remaining=150)
    s2 = P.snapshot()
    assert s2["alpaca"]["remaining"] == 150 and s2["alpaca"]["calls"] == 2 and s2["alpaca"]["limit"] == 200
    print("OK providers (timed ok/erreur, record quota, snapshot)")


if __name__ == "__main__":
    t_alpaca_account(); t_oanda_account(); t_providers()
    print("\n=== Providers (Lots 1+2 backend) : tous les tests passent ===")
