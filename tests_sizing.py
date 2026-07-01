"""Tests sizing uniforme (base saine et mesurable). python3 tests_sizing.py"""
from risk_manager import RiskManager, Profile, TradeProposal


def _prop():
    # entry 100, stop 98 (d=2), tp 104 (RR=2) — crypto fractionnaire
    return TradeProposal(instrument="BTC/USD", side="buy",
                         entry_price=100.0, stop_loss=98.0, take_profit=104.0)


def t_uniforme():
    rm = RiskManager(Profile.RESERVE)   # 0.5%/trade
    # deux equites TRES differentes, meme base de reference 2000 -> meme risque $
    a = rm.size_position(_prop(), equity_account_ccy=100.0, quote_to_account_rate=1.0,
                         base_to_account_rate=1.0, whole_units=False, risk_base=2000.0)
    b = rm.size_position(_prop(), equity_account_ccy=5000.0, quote_to_account_rate=1.0,
                         base_to_account_rate=1.0, whole_units=False, risk_base=2000.0)
    assert a.accepted and b.accepted
    assert abs(a.risk_amount_account_ccy - b.risk_amount_account_ccy) < 1e-9, \
        (a.risk_amount_account_ccy, b.risk_amount_account_ccy)
    assert abs(a.risk_amount_account_ccy - 2000.0 * 0.005) < 1e-6   # 0.5% de 2000 = 10
    print("OK uniforme (equites 100 vs 5000 -> meme risque %.2f $)" % a.risk_amount_account_ccy)


def t_ancien_comportement():
    rm = RiskManager(Profile.RESERVE)
    # sans risk_base -> risque = % de l'equite de session (ancien, non uniforme)
    a = rm.size_position(_prop(), equity_account_ccy=100.0, quote_to_account_rate=1.0,
                         base_to_account_rate=1.0, whole_units=False)
    b = rm.size_position(_prop(), equity_account_ccy=5000.0, quote_to_account_rate=1.0,
                         base_to_account_rate=1.0, whole_units=False)
    assert abs(a.risk_amount_account_ccy - 100.0 * 0.005) < 1e-6    # 0.5$
    assert abs(b.risk_amount_account_ccy - 5000.0 * 0.005) < 1e-6   # 25$
    assert b.risk_amount_account_ccy > a.risk_amount_account_ccy    # non uniforme
    print("OK ancien comportement conserve si risk_base absent (0.50$ vs 25$)")


def t_profil_scale():
    # meme base, profil different -> risque uniforme PAR profil
    r1 = RiskManager(Profile.RESERVE).size_position(_prop(), 500.0, 1.0, 1.0, whole_units=False, risk_base=2000.0)
    r2 = RiskManager(Profile.AGRESSIF).size_position(_prop(), 500.0, 1.0, 1.0, whole_units=False, risk_base=2000.0)
    assert abs(r1.risk_amount_account_ccy - 10.0) < 1e-6 and abs(r2.risk_amount_account_ccy - 30.0) < 1e-6
    print("OK risque uniforme par profil (reserve 10$ / agressif 30$ sur base 2000)")


if __name__ == "__main__":
    t_uniforme(); t_ancien_comportement(); t_profil_scale()
    print("\n=== Sizing uniforme : tous les tests passent ===")
