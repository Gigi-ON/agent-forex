"""Tests Phase 1 — qualité de décision (régime, confluence, sorties, gardes).
Exécutable : python3 tests_phase1.py  (sort 0 si tout passe)."""
import math, random
from datetime import datetime, timezone


def t_indicators():
    from indicators import adx, recent_swing_low, recent_swing_high, resample
    trend=[{"o":100+i,"h":101+i,"l":99+i,"c":100.5+i} for i in range(60)]
    rng=[{"o":100+(i%2),"h":100.5+(i%2),"l":99.5+(i%2),"c":100+(i%2)} for i in range(60)]
    assert adx(trend) > 25 and adx(rng) < 20
    m15=[{"o":i,"h":i+2,"l":i-1,"c":i+1} for i in range(20)]
    h1=resample(m15,4); assert len(h1)==5 and h1[-1]["c"]==m15[-1]["c"]
    print("OK indicators (ADX, swing, resample)")


def _mk(closes, w=0.15):
    out=[]; prev=closes[0]
    for c in closes:
        out.append({"o":prev,"h":max(prev,c)+w,"l":min(prev,c)-w,"c":c}); prev=c
    return out


def t_signals():
    from signals import SignalEngine
    from indicators import adx
    eng=SignalEngine(use_store=False)
    # range choppy -> ADX faible -> pas de trade
    random.seed(7); x=100.0; cl=[]
    for _ in range(240):
        x += -0.25*(x-100.0)+random.gauss(0,0.5); cl.append(x)
    s=eng.evaluate("EUR_USD",_mk(cl)); assert s.proposal is None and "range" in s.notes[-1].lower()
    # tendance dent-de-scie -> achat atteignable, RR ~2, stop<entry<tp
    cl=[]; x=100.0
    for i in range(320):
        x += 0.6 if i%13<8 else -0.55; cl.append(round(x,4))
    buys=[eng.evaluate("EUR_USD",_mk(cl[:e])) for e in range(80,321)]
    buy=next((s for s in buys if s.proposal and s.proposal.side=="buy"), None)
    assert buy, "un achat doit être atteignable en tendance avec replis"
    pr=buy.proposal; rr=abs(pr.take_profit-pr.entry_price)/abs(pr.entry_price-pr.stop_loss)
    assert pr.stop_loss<pr.entry_price<pr.take_profit and rr>1.9
    print("OK signals (régime + pullback + structure, RR=%.2f)" % rr)


def t_exits():
    import paper_engine as PE
    eng=PE.PaperEngine(starting_balance=5000.0)
    trades=[]; eng.journal=type("J",(),{"record":lambda self,t:trades.append(t)})()
    eng.manager.record_trade_pnl=lambda sid,pnl:None
    def feed(prices):
        trades.clear(); eng.positions={}
        pos=PE.PaperPosition(pending_id="x",session_id="S1",pair="EUR_USD",side="buy",
            units=1000,entry_price=100.0,stop_loss=98.0,take_profit=104.0,initial_risk=10.0,
            confidence=0.7,caution=1.0,entry_time="2026-06-29T12:00:00Z",
            r_unit=2.0,original_stop=98.0,hwm=100.0,lwm=100.0)
        eng.positions[pos.id]=pos
        now=datetime(2026,6,29,12,5,tzinfo=timezone.utc)
        for px in prices: eng._update_positions({"EUR_USD":{"price":px}},now)
    feed([101,102,103,101]); rs=[t.exit_reason for t in trades]
    assert "PARTIAL" in rs and [t for t in trades if t.exit_reason!="PARTIAL"][-1].pnl>0
    feed([99,98]); assert abs([t for t in trades if t.exit_reason=="STOP"][0].pnl+10.0)<1e-6
    feed([101,102,103,104]); assert abs([t for t in trades if t.exit_reason=="TP"][0].pnl-10.0)<1e-6
    print("OK exits (partielle +1R, break-even, trailing, R final)")


def t_guards():
    from supervisor import Supervisor, Pending
    from signals import Signal
    from risk_manager import TradeProposal, Profile
    import sessions_clock
    eng=type("E",(),{"evaluate":lambda self,p,c:Signal(p,TradeProposal(p,"buy",100.0,98.0,104.0),0.8,["x"])})()
    mod=type("M",(),{"assess":lambda self,i,p,n:type("D",(),{"blackout":False,"caution_factor":1.0})()})()
    sess=type("S",(),{"id":"S1","profile":Profile.DOUX,"equity":500.0,"accept_min":0.7,"accept_max":0.9,"risk_level":"doux"})()
    sup=Supervisor(manager=None,engine=eng,modulator=mod)
    c=[{"o":100,"h":100.5,"l":99.5,"c":100} for _ in range(60)]
    now=datetime(2026,1,7,14,0,tzinfo=timezone.utc)
    assert sup.propose(sess,"EUR_USD",c,[],1.0,1.0,now,spread=1.0) is None      # spread large
    assert sup.propose(sess,"EUR_USD",c,[],1.0,1.0,now,spread=0.1) is not None  # spread fin
    peg=Pending(session_id="S1",pair="EUR_USD",proposal=TradeProposal("EUR_USD","buy",100,98,104),
                units=1000,risk=2.5,leverage=1.0,confidence=0.8,caution=1.0,created=now)
    o=sessions_clock.open_sessions; sessions_clock.open_sessions=lambda *a,**k:[]
    try:
        assert sup._auto_ok(sess,peg) is False     # forex hors-session
        pc=Pending(session_id="S1",pair="BTC/USD",proposal=TradeProposal("BTC/USD","buy",100,98,104),
                   units=1,risk=2.5,leverage=1.0,confidence=0.8,caution=1.0,created=now)
        assert sup._auto_ok(sess,pc) is True       # crypto 24/7
    finally:
        sessions_clock.open_sessions=o
    print("OK guards (spread + session Niveau 3)")


if __name__ == "__main__":
    t_indicators(); t_signals(); t_exits(); t_guards()
    print("\n=== Phase 1 : tous les tests passent ===")
