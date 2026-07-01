"""Tests exécution courtier — incrément 1 (OANDA practice forex).
python3 tests_exec.py"""
import execution as X


class FakeCli:
    def __init__(self, resp): self.resp = resp; self.calls = []
    def place_market_order(self, inst, units, sl, tp):
        self.calls.append((inst, units, sl, tp)); return self.resp
    def get_equity(self): return 100000.0
    def get_open_trades(self): return [{"id": "777"}]


FILL = {"simulated": False, "response": {"orderFillTransaction":
        {"price": "1.23450", "tradeOpened": {"tradeID": "777"}}}}


def t_gating():
    prac = X.OandaExecutor("practice", client=FakeCli(FILL))
    assert prac.can_send() is True
    live_off = X.OandaExecutor("live", client=FakeCli(FILL), live_trading=False)
    assert live_off.can_send() is False
    live_on = X.OandaExecutor("live", client=FakeCli(FILL), live_trading=True)
    assert live_on.can_send() is True
    print("OK gating (practice envoie, live verrouillé sauf LIVE_TRADING)")


def t_place():
    cli = FakeCli(FILL)
    ex = X.OandaExecutor("practice", client=cli)
    r = ex.place("EUR_USD", 1000, 1.2300, 1.2500)
    assert r.ok and r.trade_id == "777" and abs(r.fill_price - 1.23450) < 1e-9
    assert cli.calls == [("EUR_USD", 1000, 1.2300, 1.2500)]
    # live verrouillé : ne touche PAS le client
    cli2 = FakeCli(FILL)
    blocked = X.OandaExecutor("live", client=cli2, live_trading=False).place("EUR_USD", 1000, 1.23, 1.25)
    assert blocked.blocked and not cli2.calls
    # erreur réseau -> ExecResult.error, jamais d'exception
    boom = X.OandaExecutor("practice", client=type("C", (), {"place_market_order":
        lambda self, *a: (_ for _ in ()).throw(RuntimeError("timeout"))})())
    assert boom.place("EUR_USD", 1000, 1.23, 1.25).error == "timeout"
    print("OK place (parse fill, units signés, garde live, erreurs absorbées)")


def t_routing():
    c = {}
    assert X.executor_for("pratique", "forex", c).name == "internal"
    assert X.executor_for("apprentissage", "forex", c).name == "oanda:practice"
    assert X.executor_for("apprentissage", "crypto", c).name == "alpaca:paper"
    assert X.executor_for("reel", "forex", c).name == "oanda:live"
    assert X.asset_of("EUR_USD") == "forex" and X.asset_of("BTC/USD") == "crypto"
    print("OK routing (mode+actif -> bon adaptateur)")


def t_engine_hook():
    import paper_engine as PE
    calls = []
    class Spy:
        name = "oanda:practice"; venue = "OANDA practice"
        def place(self, inst, units, sl, tp):
            calls.append((inst, units, sl, tp))
            return X.ExecResult(ok=True, trade_id="T42", fill_price=float(inst != ""))
    orig = X.executor_for
    X.executor_for = lambda mode, asset, cache=None: (Spy() if mode == "apprentissage" else PE.__import__("execution").InternalExecutor())
    try:
        e = PE.PaperEngine(starting_balance=5000.0)
        sa = e.open_session(budget=500.0, instrument="EUR_USD", mode="apprentissage")
        pos = PE.PaperPosition(pending_id="x", session_id=sa.id, pair="EUR_USD", side="buy",
            units=1000, entry_price=1.23, stop_loss=1.22, take_profit=1.25, initial_risk=10.0,
            confidence=.7, caution=1., entry_time="2026-06-29T12:00:00Z")
        e._route_execution(pos)
        assert calls == [("EUR_USD", 1000, 1.22, 1.25)] and pos.broker_trade_id == "T42"
        assert pos.venue == "OANDA practice"
        # Pratique : aucun appel courtier
        calls.clear()
        sp = e.open_session(budget=500.0, instrument="EUR_USD", mode="pratique")
        pos2 = PE.PaperPosition(pending_id="y", session_id=sp.id, pair="EUR_USD", side="buy",
            units=1000, entry_price=1.23, stop_loss=1.22, take_profit=1.25, initial_risk=10.0,
            confidence=.7, caution=1., entry_time="2026-06-29T12:00:00Z")
        e._route_execution(pos2)
        assert calls == [] and pos2.broker_trade_id is None
    finally:
        X.executor_for = orig
    print("OK hook moteur (Apprentissage -> placement courtier ; Pratique -> interne)")




class _Resp:
    def __init__(self, payload): self._p = payload
    def json(self): return self._p


class FakeAlpaca:
    """Session requests-like : enregistre les appels, renvoie des réponses canned."""
    def __init__(self): self.calls = []
    def post(self, url, json=None, **k):
        self.calls.append(("post", url, json)); return _Resp({"id": "ORD1"})
    def get(self, url, **k):
        self.calls.append(("get", url, None))
        return _Resp({"equity": "100000.5"} if "account" in url else [{"symbol": "BTC/USD"}])
    def delete(self, url, **k):
        self.calls.append(("delete", url, None)); return _Resp({})


def t_alpaca():
    fa = FakeAlpaca()
    ex = X.AlpacaExecutor("paper", session=fa, key="k", secret="s")
    assert ex.can_send() is True
    r = ex.place("BTC/USD", 0.5, 60000, 65000)
    assert r.ok and r.trade_id == "ORD1"
    m, url, body = fa.calls[-1]
    assert m == "post" and body["side"] == "buy" and body["qty"] == "0.5" and body["symbol"] == "BTC/USD"
    # vente : units négatifs
    ex.place("ETH/USD", -2, 0, 0)
    assert fa.calls[-1][2]["side"] == "sell" and fa.calls[-1][2]["qty"] == "2"
    # nav + positions
    assert abs(ex.nav() - 100000.5) < 1e-6
    assert ex.open_trades() == [{"symbol": "BTC/USD"}]
    # close -> DELETE position (symbole encodé)
    ex.close("ignored", "BTC/USD")
    assert fa.calls[-1][0] == "delete" and "BTC%2FUSD" in fa.calls[-1][1]
    # live verrouillé : ne touche pas le réseau
    fa2 = FakeAlpaca()
    blk = X.AlpacaExecutor("live", session=fa2, live_trading=False).place("BTC/USD", 0.5, 0, 0)
    assert blk.blocked and not fa2.calls
    # routage crypto
    c = {}
    assert X.executor_for("apprentissage", "crypto", c).name == "alpaca:paper"
    assert X.executor_for("reel", "crypto", c).name == "alpaca:live"
    print("OK Alpaca (place side/qty, nav/positions, close, garde live, routage)")


def t_close_broker():
    import paper_engine as PE
    closes = []
    class Spy:
        name = "alpaca:paper"; venue = "Alpaca paper"
        def close(self, ref, instrument=None): closes.append((ref, instrument)); return X.ExecResult(ok=True)
    orig = X.executor_for
    X.executor_for = lambda mode, asset, cache=None: (Spy() if asset == "crypto" else PE.__import__("execution").InternalExecutor())
    try:
        e = PE.PaperEngine(starting_balance=5000.0)
        e.journal = type("J", (), {"record": lambda self, t: None})()
        e.manager.record_trade_pnl = lambda sid, pnl: None
        s = e.open_session(budget=500.0, instrument="BTC/USD", mode="apprentissage")
        from datetime import datetime, timezone
        pos = PE.PaperPosition(pending_id="x", session_id=s.id, pair="BTC/USD", side="buy",
            units=0.5, entry_price=60000, stop_loss=59000, take_profit=62000, initial_risk=10.0,
            confidence=.7, caution=1., entry_time="2026-06-29T12:00:00Z", r_unit=1000.0,
            broker_trade_id="ORD1", venue="Alpaca paper")
        e.positions[pos.id] = pos
        e._close(pos, 59000, "STOP", datetime(2026, 6, 29, 13, tzinfo=timezone.utc))
        assert closes == [("ORD1", "BTC/USD")], closes
        # position interne -> pas de clôture courtier
        closes.clear()
        pos2 = PE.PaperPosition(pending_id="y", session_id=s.id, pair="BTC/USD", side="buy",
            units=0.5, entry_price=60000, stop_loss=59000, take_profit=62000, initial_risk=10.0,
            confidence=.7, caution=1., entry_time="2026-06-29T12:00:00Z", r_unit=1000.0)
        e.positions[pos2.id] = pos2
        e._close(pos2, 59000, "STOP", datetime(2026, 6, 29, 13, tzinfo=timezone.utc))
        assert closes == []
    finally:
        X.executor_for = orig
    print("OK clôture courtier (broker-backed -> close ; interne -> rien)")




def t_reconcile():
    import paper_engine as PE
    from datetime import datetime, timezone
    class FakeEx:
        name = "oanda:practice"; venue = "OANDA practice"
        def __init__(self): self.omap = {"T1": {"unrealized": 3.5, "price": 1.2345}}
        def open_map(self): return self.omap
        def nav(self): return 10250.0
        def close(self, ref, instrument=None): return X.ExecResult(ok=True)
    fe = FakeEx(); orig = X.executor_for
    X.executor_for = lambda mode, asset, cache=None: fe
    booked = []
    try:
        e = PE.PaperEngine(starting_balance=5000.0)
        e.journal = type("J", (), {"record": lambda self, t: booked.append(t)})()
        e.manager.record_trade_pnl = lambda sid, pnl: None
        s = e.open_session(budget=500.0, instrument="EUR_USD", mode="apprentissage")
        pos = PE.PaperPosition(pending_id="x", session_id=s.id, pair="EUR_USD", side="buy",
            units=1000, entry_price=1.23, stop_loss=1.22, take_profit=1.25, initial_risk=10.0,
            confidence=.7, caution=1., entry_time="2026-06-29T12:00:00Z", r_unit=0.01,
            broker_trade_id="T1", venue="OANDA practice")
        e.positions[pos.id] = pos
        now = datetime(2026, 6, 29, 13, tzinfo=timezone.utc)
        e._reconcile_broker(now)                       # ouvert au courtier
        assert pos.broker_unreal == 3.5 and e._pos_unreal(pos) == 3.5
        assert e._broker_nav["OANDA practice"] == 10250.0 and pos.id in e.positions
        fe.omap = {}                                   # le courtier l'a fermée
        e._reconcile_broker(now)
        assert pos.id not in e.positions
        assert any(getattr(t, "exit_reason", "") == "BROKER" for t in booked)
    finally:
        X.executor_for = orig
    print("OK reconcile (latent courtier + NAV + book clôture courtier)")




def t_writeback():
    import paper_engine as PE
    from datetime import datetime, timezone
    booked = []
    class Spy:
        name = "oanda:practice"; venue = "OANDA practice"
        def __init__(self): self.mods = []; self.partials = []; self.omap = {"5": {"unrealized": 1.0, "price": 102}}; self._pnl = None
        def open_map(self): return self.omap
        def nav(self): return 100000.0
        def place(self, *a, **k): return X.ExecResult(ok=True, trade_id="5")
        def modify_stop(self, ref, stop, instrument=None): self.mods.append((ref, round(stop, 3), instrument)); return X.ExecResult(ok=True)
        def partial_close(self, ref, units, side=None, instrument=None): self.partials.append((ref, units, side)); return X.ExecResult(ok=True)
        def close(self, ref, instrument=None): return X.ExecResult(ok=True)
        def trade_pnl(self, ref): return self._pnl
    spy = Spy(); orig = X.executor_for
    X.executor_for = lambda mode, asset, cache=None: spy
    def newpos(e, sid, tid="5"):
        return PE.PaperPosition(pending_id="x"+tid, session_id=sid, pair="EUR_USD", side="buy",
            units=1000, entry_price=100.0, stop_loss=98.0, take_profit=104.0, initial_risk=10.0,
            confidence=.7, caution=1., entry_time="2026-06-29T12:00:00Z", r_unit=2.0,
            broker_trade_id=tid, venue="OANDA practice")
    try:
        e = PE.PaperEngine(starting_balance=5000.0)
        e.journal = type("J", (), {"record": lambda self, t: booked.append(t)})()
        e.manager.record_trade_pnl = lambda sid, pnl: None
        s = e.open_session(budget=500.0, instrument="EUR_USD", mode="apprentissage")
        now = datetime(2026, 6, 29, 13, tzinfo=timezone.utc)

        # A) +1R : partielle ET stop (BE) poussés au courtier ; PAS de clôture interne
        pos = newpos(e, s.id); e.positions[pos.id] = pos
        e._update_positions({"EUR_USD": {"price": 102.0}}, now)
        assert spy.partials and spy.partials[0][1] == 500.0
        assert spy.mods and abs(spy.mods[0][1] - 100.1) < 1e-6   # stop déplacé à BE (~entry)
        assert pos.id in e.positions                              # le courtier gère la clôture

        # B) broker-backed : prix sous le stop -> PAS de clôture interne
        pos2 = newpos(e, s.id, "6"); e.positions[pos2.id] = pos2
        e._update_positions({"EUR_USD": {"price": 97.0}}, now)
        assert pos2.id in e.positions
        # une position INTERNE, elle, se ferme au stop
        posi = newpos(e, s.id, "7"); posi.broker_trade_id = None; posi.venue = "interne"
        e.positions[posi.id] = posi
        e._update_positions({"EUR_USD": {"price": 97.0}}, now)
        assert posi.id not in e.positions

        # C) clôture côté courtier -> book avec le PnL RÉEL du courtier
        booked.clear()
        pos3 = newpos(e, s.id, "8"); e.positions[pos3.id] = pos3
        spy.omap = {}; spy._pnl = 7.5
        e._reconcile_broker(now)
        assert pos3.id not in e.positions
        bro = [t for t in booked if getattr(t, "exit_reason", "") == "BROKER"]
        assert bro and abs(bro[0].pnl - 7.5) < 1e-6              # PnL courtier, pas repli R
    finally:
        X.executor_for = orig
    print("OK write-back (stop+partielle poussés, broker non fermé en interne, PnL réel courtier)")




def t_alpaca_symbol():
    """Régression live : Alpaca renvoie 'BTCUSD' (sans slash) ; la réconciliation
    doit matcher la position pair='BTC/USD' et NE PAS la clôturer à tort."""
    import paper_engine as PE
    from datetime import datetime, timezone
    class Spy:
        name = "alpaca:paper"; venue = "Alpaca paper"
        def open_map(self): return {"BTCUSD": {"unrealized": 4.0, "price": 60000}}  # sans slash
        def nav(self): return 100000.0
        def trade_pnl(self, ref): return None
        def close(self, ref, instrument=None): return X.ExecResult(ok=True)
    orig = X.executor_for
    X.executor_for = lambda mode, asset, cache=None: Spy()
    try:
        e = PE.PaperEngine(starting_balance=5000.0)
        e.journal = type("J", (), {"record": lambda self, t: None})()
        e.manager.record_trade_pnl = lambda sid, pnl: None
        s = e.open_session(budget=500.0, instrument="BTC/USD", mode="apprentissage")
        pos = PE.PaperPosition(pending_id="x", session_id=s.id, pair="BTC/USD", side="buy",
            units=0.002, entry_price=60000, stop_loss=59000, take_profit=62000, initial_risk=10.0,
            confidence=.7, caution=1., entry_time="2026-06-29T12:00:00Z", r_unit=1000.0,
            broker_trade_id="ORD1", venue="Alpaca paper")
        e.positions[pos.id] = pos
        e._reconcile_broker(datetime(2026, 6, 29, 13, tzinfo=timezone.utc))
        assert pos.id in e.positions          # PAS clôturée à tort
        assert pos.broker_unreal == 4.0       # latent courtier appliqué
    finally:
        X.executor_for = orig
    print("OK Alpaca symbole (BTCUSD <-> BTC/USD : matche, pas de fausse clôture)")


def t_net_hardening():
    """Durcissement réseau : retry 429, circuit-breaker, throttle, idempotence."""
    X.breaker_reset()
    slept = []
    sleep = lambda s: slept.append(s)

    class R:
        def __init__(self, sc, headers=None):
            self.status_code = sc
            self.headers = headers or {}

    # 1) 429 puis 200 -> with_retry réessaie et renvoie le succès
    seq = [R(429, {"Retry-After": "0"}), R(200)]
    n = {"i": 0}
    def do_ok():
        r = seq[n["i"]]; n["i"] += 1; return r
    r = X.with_retry("t1", do_ok, sleep=sleep)
    assert r.status_code == 200 and n["i"] == 2 and slept

    # 2) 3 appels échoués -> disjoncteur ouvert -> CircuitOpen instantané
    X.breaker_reset("t2")
    def do_fail():
        raise RuntimeError("down")
    for _ in range(3):
        try:
            X.with_retry("t2", do_fail, sleep=sleep, retries=1)
        except RuntimeError:
            pass
    try:
        X.with_retry("t2", do_fail, sleep=sleep, retries=1)
        assert False, "CircuitOpen attendu"
    except X.CircuitOpen:
        pass

    # 3) throttle si quota restant bas
    X.breaker_reset("t3"); slept.clear()
    X.with_retry("t3", lambda: R(200, {"X-RateLimit-Remaining": "5"}), sleep=sleep)
    assert 0.5 in slept

    # 4) idempotence : client_order_id déterministe-unique présent sur l'ordre
    fa = FakeAlpaca()
    X.AlpacaExecutor("paper", session=fa, key="k", secret="s").place("BTC/USD", 0.5, 0, 0)
    body = fa.calls[-1][2]
    assert "client_order_id" in body and body["client_order_id"].startswith("af-")
    print("OK durcissement réseau (retry 429, circuit-breaker, throttle, idempotence)")


if __name__ == "__main__":
    t_gating(); t_place(); t_routing(); t_engine_hook(); t_alpaca(); t_close_broker(); t_reconcile(); t_writeback(); t_alpaca_symbol(); t_net_hardening()
    print("\n=== Exécution courtier (incréments 1-2) : tous les tests passent ===")
