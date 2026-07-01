"""Tests Phase 2 — survie & portefeuille (heat, de-risking, corrélation, anti-overtrading).
Exécutable : python3 tests_phase2.py"""
from datetime import datetime, timezone, timedelta
from risk_manager import RiskManager, TradeProposal, Profile

_NOW = datetime(2026, 1, 7, 14, 0, tzinfo=timezone.utc)   # mercredi, marché ouvert
_PROP = TradeProposal("EUR_USD", "buy", 100.0, 98.0, 104.0)


def _size(**kw):
    rm = RiskManager(profile=Profile.DOUX)
    return rm.size_position(proposal=_PROP, equity_account_ccy=500.0,
        quote_to_account_rate=1.0, base_to_account_rate=1.0,
        current_atr=0.0, average_atr=0.0, **kw)


def t_heat():
    import strategy
    cap = strategy.P2().get("max_portfolio_heat_pct", 6.0) / 100.0 * 5000.0  # budget de risque (meme source que le moteur)
    base = _size()
    full = _size(portfolio_open_risk=cap - 2.0, portfolio_equity=5000.0)   # reste 2$
    none = _size(portfolio_open_risk=cap, portfolio_equity=5000.0)         # cap atteint
    assert abs(base.risk_amount_account_ccy - 5.0) < 1e-6
    assert abs(full.risk_amount_account_ccy - 2.0) < 1e-6
    assert none.accepted is False and "Heat" in none.reasons[0]
    print("OK heat global (réduit pour rentrer dans le budget, refuse au plafond)")


def t_derisk():
    assert abs(_size(streak_scale=0.5).risk_amount_account_ccy - 2.5) < 1e-6
    print("OK de-risking (taille réduite après pertes)")


def t_correlation():
    from supervisor import Supervisor
    from signals import Signal
    eng = type("E", (), {"evaluate": lambda self, p, c: Signal(p, TradeProposal(p, "buy", 100.0, 98.0, 104.0), 0.8, ["x"])})()
    mod = type("M", (), {"assess": lambda self, i, p, n: type("D", (), {"blackout": False, "caution_factor": 1.0})()})()
    sess = type("S", (), {"id": "S1", "profile": Profile.DOUX, "equity": 500.0, "accept_min": 0.7, "accept_max": 0.9, "risk_level": "doux"})()
    sup = Supervisor(manager=None, engine=eng, modulator=mod)
    c = [{"o": 100, "h": 100.5, "l": 99.5, "c": 100} for _ in range(60)]
    block = {"open_risk": 0.0, "equity": 5000.0, "ccy_exposure": {"EUR": 198.0}}  # cap=200
    ok = {"open_risk": 0.0, "equity": 5000.0, "ccy_exposure": {"EUR": 100.0}}
    assert sup.propose(sess, "EUR_USD", c, [], 1.0, 1.0, _NOW, portfolio=block) is None
    assert sup.propose(sess, "EUR_USD", c, [], 1.0, 1.0, _NOW, portfolio=ok) is not None
    print("OK corrélation (refuse l'empilement sur une devise saturée)")


def t_overtrading():
    import paper_engine as PE
    candles = [{"o": 1.0, "h": 1.01, "l": 0.99, "c": 1.0} for _ in range(60)]
    market = {"EUR_USD": {"candles": candles, "price": 1.0, "q2a": 1.0, "b2a": 1.0, "spread": 0.0001}}

    def setup():
        e = PE.PaperEngine(starting_balance=5000.0)
        s = e.open_session(budget=500.0, instrument="EUR_USD")
        e._day = _NOW.date()
        calls = []
        e.supervisor.propose = lambda *a, **k: calls.append(1)
        return e, s, calls

    import strategy
    cool = strategy.P2().get("cooldown_min_after_loss", 30)
    space = strategy.P2().get("min_minutes_between_same_pair", 15)
    e, s, c = setup(); e.tick(market, _NOW); assert len(c) == 1
    e, s, c = setup(); e._trades_today = 999; e.tick(market, _NOW); assert len(c) == 0
    e, s, c = setup(); e._last_loss_time[s.id] = _NOW - timedelta(minutes=cool - 1); e.tick(market, _NOW); assert len(c) == 0
    e, s, c = setup(); e._last_loss_time[s.id] = _NOW - timedelta(minutes=cool + 5); e.tick(market, _NOW); assert len(c) == 1
    e, s, c = setup(); e._last_entry_time["EUR_USD"] = _NOW - timedelta(minutes=max(1, space - 1)); e.tick(market, _NOW); assert len(c) == 0
    # de-risking : multiplicateur en escalier, plancher à 0.4
    e, _, _ = setup()
    e._loss_streak = 0; assert e._risk_scale() == 1.0
    e._loss_streak = 2; assert abs(e._risk_scale() - 0.5) < 1e-9
    e._loss_streak = 9; assert abs(e._risk_scale() - 0.4) < 1e-9
    print("OK anti-overtrading (cap/jour, cooldown, espacement) + escalier de-risking")


if __name__ == "__main__":
    t_heat(); t_derisk(); t_correlation(); t_overtrading()
    print("\n=== Phase 2 : tous les tests passent ===")
