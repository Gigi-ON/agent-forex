"""
Moteur de paper-trading serveur.

Principe : signaux RÉELS, exécutions SIMULÉES, état PERSISTANT, aucun ordre
OANDA. C'est l'étage « Pratique / Apprentissage » de la plateforme — il rend
les sessions et la supervision réelles (vs la simulation navigateur) sans
jamais toucher d'argent ni la chaîne d'exécution réelle.

Chaîne :
  signal (SignalEngine, cours réels) -> proposition supervisée (intervalle
  d'acceptation §10) -> position PAPIER ouverte -> suivie contre les prix réels
  -> clôturée sur stop / objectif / temps -> trade JOURNALISÉ.

Les trades journalisés alimentent automatiquement /api/journal, /api/learning
et /api/mastery. L'exécution réelle OANDA reste réservée au mode Réel armé,
verrouillée par config.LIVE_TRADING + double authentification.

Comptabilité P&L (déterministe, en R = multiples du risque initial) :
  - stop atteint      -> -1R           (pnl = -risque_initial)
  - objectif atteint  -> +R où R = |tp-entrée| / |entrée-stop|
  - sortie au temps   -> R partiel selon le déplacement réel du prix
Tout reste cohérent avec le journal et les analyses (espérance en R).
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from session import SessionManager, Tutelle, SessionState
from risk_manager import Profile
from supervisor import Supervisor
from journal import Trade, JournalStore


def _now():
    return datetime.now(timezone.utc)


@dataclass
class PaperPosition:
    pending_id: str
    session_id: str
    pair: str
    side: str                       # "buy" / "sell"
    units: int
    entry_price: float
    stop_loss: float
    take_profit: float
    initial_risk: float             # devise du compte, > 0
    confidence: float
    caution: float
    entry_time: str
    max_hold_min: int = 240
    # --- Phase 1 : gestion de sortie (break-even / partielle / trailing) ---
    r_unit: float = 0.0             # 1R fixe = distance entrée->stop INITIAL
    original_stop: float = 0.0      # stop d'origine (référence)
    be_done: bool = False           # break-even déjà appliqué ?
    partial_done: bool = False      # prise partielle déjà faite ?
    hwm: float = 0.0                # plus haut atteint depuis l'entrée (buy)
    lwm: float = 0.0                # plus bas atteint depuis l'entrée (sell)
    broker_trade_id: str = None     # id du trade chez le courtier (Apprentissage/Réel)
    venue: str = "interne"          # lieu d'exécution : interne | OANDA practice | ...
    broker_unreal: float = None     # P&L latent rapporté par le courtier (si dispo)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def risk_distance(self):
        return abs(self.entry_price - self.stop_loss)

    def realized_R(self, price):
        """R réalisé pour un prix de sortie donné (signé selon le sens).
        Base = r_unit (1R fixe à l'entrée) pour rester correct même après que le
        stop a été déplacé à break-even ou trailé."""
        d = self.r_unit if self.r_unit > 0 else self.risk_distance
        if d <= 0:
            return 0.0
        move = (price - self.entry_price) if self.side == "buy" else (self.entry_price - price)
        return move / d


class PaperEngine:
    """Un moteur = un compte papier (un solde commun, plusieurs sessions)."""

    def __init__(self, starting_balance=5000.0, journal_store=None,
                 engine=None, modulator=None):
        self.manager = SessionManager(starting_balance)
        self.journal = journal_store or JournalStore()
        self.activity = []           # journal d'activité (audit)
        self.last_tick = None        # battement de cœur (ISO)
        self._halt_logged = False
        self.last_price = {}         # pair -> dernier prix (pour le P&L latent)
        # Le superviseur ne journalise PAS à l'ouverture (journal=None) ;
        # son alert_sink=self alimente le journal d'activité.
        self.supervisor = Supervisor(self.manager, journal_store=None,
                                     alert_sink=self, engine=engine, modulator=modulator)
        self.positions = {}          # id -> PaperPosition (ouvertes)
        self._has_pos = set()        # pending_ids déjà transformés en position
        # --- garde-fous (Phase A) ---
        self.running = True          # pause / kill-switch -> stoppe les NOUVELLES propositions
        self._day = None             # date UTC courante (coupe-circuit journalier)
        self._day_pnl = 0.0          # P&L réalisé du jour
        self._day_start_balance = float(starting_balance)
        from config import HARD_LIMITS as _HL
        self._HL = _HL
        try:
            from config import PHASE1 as _P1
        except Exception:
            _P1 = {}
        self._P1 = _P1 or {}
        try:
            from config import PHASE2 as _P2
        except Exception:
            _P2 = {}
        self._P2 = _P2 or {}
        # --- Phase 2 : suivi pour survie & anti-overtrading ---
        self._loss_streak = 0           # pertes consécutives (de-risking)
        self._win_streak = 0
        self._trades_today = 0          # plafond de trades/jour
        self._last_loss_time = {}       # session_id -> datetime de la dernière perte
        self._sess_stats = {}           # session_id -> {wins, losses, last:{pair,reason,pnl,R,ts}}
        self._last_entry_time = {}      # pair -> datetime de la dernière entrée
        self._broker_nav = {}           # venue -> NAV rapporté par le courtier (Apprentissage/Réel)

    # -- sessions -----------------------------------------------------------
    def open_session(self, budget, accept_min=None, accept_max=None,
                     profile=Profile.RESERVE, risk_level="reserve",
                     duration_min=240, tutelle=Tutelle.MANUEL, instrument=None,
                     mode="pratique", trader="deterministe"):
        s = self.manager.open_session(allocated=budget, profile=profile,
                                      tutelle=tutelle, duration_min=duration_min,
                                      risk_level=risk_level)
        s.accept_min = accept_min
        s.accept_max = accept_max
        s.instrument = instrument
        s.mode = mode
        s.trader = trader
        self._log("session", "Session ouverte #%s · %d$%s" % (s.id, int(budget), (" · " + instrument) if instrument else ""))
        return s

    def close_session(self, session_id, reason="clôture manuelle"):
        self._log("session", "Session clôturée #%s (%s)" % (session_id, reason))
        return self.manager.close_session(session_id, reason=reason)

    def pause_session(self, session_id):
        s = self.manager.sessions.get(session_id)
        if s and s.state == SessionState.ACTIVE:
            s.paused = True
            self._log("session", "Session #%s en pause" % session_id)
        return s

    def resume_session(self, session_id):
        s = self.manager.sessions.get(session_id)
        if s:
            s.paused = False
            self._log("session", "Session #%s reprise" % session_id)
        return s

    def stop_session(self, session_id, market=None, now=None):
        """Stop = clôture immédiate des positions de la session (flatten) + fin de session."""
        now = now or _now()
        for pos in list(self.positions.values()):
            if pos.session_id == session_id:
                m = (market or {}).get(pos.pair)
                price = float(m["price"]) if (m and m.get("price") is not None) else pos.entry_price
                self._close(pos, price, "MANUAL", now)
        self.close_session(session_id, reason="arrêt manuel")
        return self.snapshot(now)

    # -- propositions (manuel / semi-auto / auto) ---------------------------
    def decide(self, pending_id, action, now=None):
        """Décision humaine : 'approve' ou 'reject'. Clic toujours possible."""
        now = now or _now()
        if action == "approve":
            p = self.supervisor.approve(pending_id, now)
        elif action == "reject":
            p = self.supervisor.reject(pending_id)
        else:
            return None
        self._sync_positions(now)
        return p

    # -- tick : à appeler périodiquement (ex. toutes les 15 s) --------------
    def tick(self, market, now=None):
        """
        market = { pair: {"candles":[...], "price":float, "stale":bool,
                          "news":[...], "q2a":float, "b2a":float} }
        Garde-fous appliqués : coupe-circuit journalier, plafond d'exposition
        global, fraîcheur des données, heures de marché, pause/kill.
        Les positions ouvertes sont TOUJOURS suivies (SL/TP honorés même en
        pause), seules les NOUVELLES propositions sont bloquées.
        """
        now = now or _now()
        self.last_tick = now.isoformat()
        self._roll_day(now)
        if self.daily_halted and not self._halt_logged:
            self._log("garde-fou", "Coupe-circuit journalier : nouvelles entrées bloquées")
            self._halt_logged = True
        self.supervisor.sweep(now)
        self.manager.sweep_expired(now)
        self._update_positions(market, now)
        for _pair, _m in (market or {}).items():
            if _m.get("price") is not None:
                self.last_price[_pair] = float(_m["price"])
        self._reconcile_broker(now)

        if self.running and not self.daily_halted:
            import strategy as _S
            _p2 = _S.P2()
            _maxtd = _p2.get("max_trades_per_day", 12)
            _cool = _p2.get("cooldown_min_after_loss", 0) * 60
            _space = _p2.get("min_minutes_between_same_pair", 0) * 60
            for session in list(self.manager.active):
                if not self._can_open_more():
                    break
                if self._trades_today >= _maxtd:
                    break  # plafond de trades du jour atteint -> stop pour aujourd'hui
                if getattr(session, "paused", False):
                    continue
                # cooldown : après une perte, la session attend avant de reprendre
                _lt = self._last_loss_time.get(session.id)
                if _lt and (now - _lt).total_seconds() < _cool:
                    continue
                # une session = au plus UNE position ouverte (ou une proposition
                # en attente) à la fois -> pas d'empilement sur un signal persistant
                if any(pos.session_id == session.id for pos in self.positions.values()):
                    continue
                if any(pp.session_id == session.id and pp.status == "pending"
                       for pp in self.supervisor.pending.values()):
                    continue
                pairs = [session.instrument] if getattr(session, "instrument", None) else list(market.keys())
                for pair in pairs:
                    if not self._can_open_more():
                        break
                    # espacement minimal entre deux entrées sur la même paire
                    _et = self._last_entry_time.get(pair)
                    if _et and (now - _et).total_seconds() < _space:
                        continue
                    m = market.get(pair)
                    if not m or not self._tradeable(pair, m, now):
                        continue
                    self.supervisor.propose(
                        session, pair, m.get("candles", []), m.get("news", []),
                        m.get("q2a", 1.0), m.get("b2a", 1.0), now,
                        spread=m.get("spread"),
                        portfolio=self._portfolio(), risk_scale=self._risk_scale())
                    self._sync_positions(now)   # MAJ immédiate du compte de positions
            self._sync_positions(now)
        return self.snapshot(now)

    # -- garde-fous ----------------------------------------------------------
    def _roll_day(self, now):
        d = now.date()
        if self._day != d:
            self._day = d
            self._day_pnl = 0.0
            self._day_start_balance = self.manager.balance
            self._halt_logged = False
            self._trades_today = 0      # nouveau jour -> compteur de trades remis à zéro

    @property
    def daily_halted(self):
        cap = self._HL.get("max_daily_loss_pct", 4.0)
        return self._day_pnl <= -(self._day_start_balance * cap / 100.0)

    @staticmethod
    def _legs(pair):
        x = pair.replace("/", "_")
        if "_" in x:
            a, b = x.split("_", 1)
            return a, b
        return pair, None

    def _ccy_exposure(self):
        """Exposition NETTE par devise (en montant de risque), tous open confondus.
        buy EUR_USD = +EUR / -USD ; sell = l'inverse."""
        exp = {}
        for pos in self.positions.values():
            a, b = self._legs(pos.pair)
            sgn = pos.initial_risk if pos.side == "buy" else -pos.initial_risk
            if a:
                exp[a] = exp.get(a, 0.0) + sgn
            if b:
                exp[b] = exp.get(b, 0.0) - sgn
        return exp

    def _portfolio(self):
        return {"open_risk": self._open_risk(), "equity": self.manager.balance,
                "ccy_exposure": self._ccy_exposure()}

    def _risk_scale(self):
        """De-risking anti-martingale : <1 après des pertes consécutives."""
        import strategy as _S
        _p2 = _S.P2()
        return max(_p2.get("derisk_floor", 0.4),
                   1.0 - _p2.get("derisk_step", 0.25) * self._loss_streak)

    def _open_risk(self):
        return sum(p.initial_risk for p in self.positions.values())

    def _can_open_more(self):
        if len(self.positions) >= self._HL.get("max_open_positions", 3):
            return False
        risk_cap = self._day_start_balance * (
            self._HL.get("max_open_positions", 3)
            * self._HL.get("max_risk_per_trade_pct", 2.0) / 100.0)
        return self._open_risk() < risk_cap

    @staticmethod
    def _is_crypto(pair):
        s = pair.upper()
        return any(c in s for c in
                   ("BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "USDT", "USDC"))

    @staticmethod
    def _forex_open(now):
        # Forex fermé ~ vendredi 21:00 UTC -> dimanche 21:00 UTC.
        wd, h = now.weekday(), now.hour     # lundi=0 ... dimanche=6
        if wd == 5:
            return False
        if wd == 4 and h >= 21:
            return False
        if wd == 6 and h < 21:
            return False
        return True

    def _tradeable(self, pair, m, now):
        if m.get("price") is None or not m.get("candles"):
            return False
        if m.get("stale"):
            return False
        if not self._is_crypto(pair) and not self._forex_open(now):
            return False
        return True

    def on_external_price(self, pair, price, now=None):
        """Applique un prix reçu d'un flux natif (OANDA/Kraken) : maj du dernier
        prix + clôture éventuelle SL/TP de la position de cette paire.
        Renvoie True si une position a été clôturée (état durable à sauvegarder)."""
        now = now or _now()
        try:
            self.last_price[pair] = float(price)
        except Exception:
            return False
        before = len(self.positions)
        self._update_positions({pair: {"price": self.last_price[pair]}}, now)
        return before != len(self.positions)

    def price_tick(self, prices, now=None):
        """Mise à jour RAPIDE des prix (sans signaux) : P&L latent + clôtures SL/TP.
        Appelée toutes les ~2 s pour la réactivité ; les signaux restent au tick lent."""
        now = now or _now()
        for pair, price in (prices or {}).items():
            if price is not None:
                self.last_price[pair] = float(price)
        market = {pair: {"price": px} for pair, px in self.last_price.items()}
        self._update_positions(market, now)
        return self.snapshot(now)

    def pause(self):
        self.running = False
        self._log("système", "Pause du moteur")

    def resume(self):
        self.running = True
        self._log("système", "Reprise du moteur")

    def kill(self, market=None, now=None):
        """Arrêt d'urgence : ferme toutes les positions au prix courant, stoppe le moteur."""
        now = now or _now()
        if market:
            for pos in list(self.positions.values()):
                m = market.get(pos.pair)
                price = float(m["price"]) if (m and m.get("price") is not None) else pos.entry_price
                self._close(pos, price, "KILL", now)
        self.running = False
        self._log("système", "Arrêt d'urgence — positions fermées")
        return self.snapshot(now)

    # -- transforme les propositions approuvées en positions papier ---------
    def _sync_positions(self, now):
        for p in self.supervisor.pending.values():
            if p.status == "approved" and p.id not in self._has_pos:
                self._has_pos.add(p.id)
                pos = PaperPosition(
                    pending_id=p.id, session_id=p.session_id, pair=p.pair,
                    side=p.proposal.side, units=p.units,
                    entry_price=p.proposal.entry_price,
                    stop_loss=p.proposal.stop_loss,
                    take_profit=p.proposal.take_profit,
                    initial_risk=p.risk, confidence=p.confidence,
                    caution=p.caution,
                    entry_time=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    r_unit=abs(p.proposal.entry_price - p.proposal.stop_loss),
                    original_stop=p.proposal.stop_loss,
                    hwm=p.proposal.entry_price, lwm=p.proposal.entry_price)
                self.positions[pos.id] = pos
                self._trades_today += 1
                self._last_entry_time[pos.pair] = now
                self._log("position", "Position ouverte %s %s" % (pos.pair, pos.side))
                self._route_execution(pos)

    def _manage_exit(self, pos, price, now):
        """Phase 1 — pilotage de sortie en multiples de R (break-even, prise
        partielle, trailing). Ne ferme rien : ajuste le stop / réduit la taille.
        La fermeture reste gérée par le contrôle SL/TP qui suit."""
        import strategy as _S
        P = _S.P1()
        # suivi des extrêmes depuis l'entrée
        if pos.side == "buy":
            pos.hwm = max(pos.hwm or pos.entry_price, price)
        else:
            pos.lwm = min(pos.lwm or pos.entry_price, price)
        R = pos.realized_R(price)
        broker = self._is_broker(pos)
        # a) prise partielle à +partial_trigger_R (poussée au courtier si broker-backed)
        if (not pos.partial_done and P.get("partial_frac", 0) > 0
                and R >= P.get("partial_trigger_R", 1.0) and abs(pos.units) > 0):
            qty = abs(pos.units * max(0.0, min(0.9, P["partial_frac"])))
            self._scale_out(pos, price, P["partial_frac"], now)
            pos.partial_done = True
            if broker and qty > 0:
                self._broker_partial(pos, qty)
        # b) passage à break-even à +be_trigger_R
        old_stop = pos.stop_loss
        if not pos.be_done and R >= P.get("be_trigger_R", 1.0) and pos.r_unit > 0:
            buf = P.get("be_buffer_R", 0.0) * pos.r_unit
            pos.stop_loss = (pos.entry_price + buf) if pos.side == "buy" else (pos.entry_price - buf)
            pos.be_done = True
        # c) trailing une fois à break-even
        if pos.be_done and P.get("trail_mult_R", 0) > 0 and pos.r_unit > 0:
            td = P["trail_mult_R"] * pos.r_unit
            if pos.side == "buy":
                pos.stop_loss = max(pos.stop_loss, pos.hwm - td)
            else:
                pos.stop_loss = min(pos.stop_loss, pos.lwm + td)
        # propager le nouveau stop au courtier (BE / trailing)
        if broker and abs(pos.stop_loss - old_stop) > 1e-12:
            self._broker_modify_stop(pos)

    def _scale_out(self, pos, price, frac, now):
        """Clôture une fraction de la position et la journalise (PARTIAL)."""
        frac = max(0.0, min(0.9, frac))
        closed_units = pos.units * frac          # signé
        portion_risk = pos.initial_risk * frac
        pnl = round(portion_risk * pos.realized_R(price), 2)
        if self.journal:
            self.journal.record(Trade(
                pair=pos.pair, side=pos.side, units=closed_units,
                entry_price=pos.entry_price, stop_loss=pos.stop_loss,
                take_profit=pos.take_profit, entry_time=pos.entry_time,
                initial_risk=portion_risk, exit_price=round(price, 5),
                exit_time=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                exit_reason="PARTIAL", pnl=pnl, profile=pos.side,
                signal_confidence=pos.confidence, caution_factor=pos.caution,
                notes="partial %d%% session=%s" % (int(frac * 100), pos.session_id)))
        self.manager.record_trade_pnl(pos.session_id, pnl)
        self._day_pnl += pnl
        self._log("trade", "%s PARTIAL %+.2f$" % (pos.pair, pnl))
        pos.units -= closed_units
        pos.initial_risk -= portion_risk

    def _route_execution(self, pos):
        """Apprentissage/Réel : place l'ordre chez le courtier (Pratique = interne).
        Incrément 1 : on PLACE l'ordre sur OANDA practice et on enregistre le
        trade_id ; la comptabilité reste interne (le basculement vers la vérité
        courtier viendra une fois le round-trip live validé). Jamais bloquant."""
        try:
            sess = self.manager.sessions.get(pos.session_id)
            mode = getattr(sess, "mode", "pratique") if sess else "pratique"
            if mode == "pratique":
                return
            import execution
            ex = execution.executor_for(mode, execution.asset_of(pos.pair))
            if getattr(ex, "name", "internal") == "internal":
                return
            res = ex.place(pos.pair, pos.units, pos.stop_loss, pos.take_profit)
            pos.venue = getattr(ex, "venue", "courtier")
            if res.ok:
                pos.broker_trade_id = res.trade_id
                self._log("execution", "Ordre %s placé sur %s (trade %s)"
                          % (pos.pair, pos.venue, res.trade_id or "?"))
            elif res.blocked:
                self._log("execution", "Ordre %s NON envoyé (%s) — %s"
                          % (pos.pair, pos.venue, res.error or "verrou"))
            else:
                self._log("execution", "Échec ordre %s sur %s : %s"
                          % (pos.pair, pos.venue, res.error or "?"))
        except Exception as e:
            try:
                self._log("execution", "Routage exécution ignoré : %s" % e)
            except Exception:
                pass

    def _reconcile_broker(self, now):
        """Incrément 1b-α — LECTURE de la vérité courtier pour Apprentissage/Réel :
        met à jour le P&L latent depuis le courtier, le NAV, et BOOKE les positions
        que le courtier a clôturées de son côté (SL/TP attaché OANDA, etc.).
        Guardé et best-effort : si le courtier ne répond pas, on ne touche à rien
        (repli silencieux sur la compta interne). Pratique : aucun appel."""
        try:
            import execution
        except Exception:
            return
        # regrouper les positions broker-backed par adaptateur
        groups = {}
        for pos in list(self.positions.values()):
            if not getattr(pos, "broker_trade_id", None) or getattr(pos, "venue", "interne") == "interne":
                continue
            sess = self.manager.sessions.get(pos.session_id)
            mode = getattr(sess, "mode", "pratique") if sess else "pratique"
            ex = execution.executor_for(mode, execution.asset_of(pos.pair))
            if getattr(ex, "name", "internal") == "internal":
                continue
            groups.setdefault(ex.name, [ex, []])[1].append(pos)
        for name, (ex, poss) in groups.items():
            try:
                omap = ex.open_map()
            except Exception:
                continue
            nav = None
            try:
                nav = ex.nav()
            except Exception:
                nav = None
            if nav is not None:
                self._broker_nav[getattr(ex, "venue", name)] = round(nav, 2)
            for pos in poss:
                # OANDA : clé = trade id ; Alpaca : clé = symbole normalisé (sans slash)
                ref = (pos.broker_trade_id if str(pos.venue).startswith("OANDA")
                       else pos.pair.replace("/", ""))
                info = omap.get(ref)
                if info is not None:
                    pos.broker_unreal = info.get("unrealized")
                else:
                    # le courtier l'a clôturée -> on la booke avec SON PnL réalisé
                    # (repli R au dernier prix si le courtier ne le fournit pas)
                    price = self.last_price.get(pos.pair, pos.entry_price)
                    bp = None
                    try:
                        bp = ex.trade_pnl(ref)
                    except Exception:
                        bp = None
                    self._close(pos, price, "BROKER", now, pnl=bp)

    def _is_broker(self, pos):
        return (bool(getattr(pos, "broker_trade_id", None))
                and getattr(pos, "venue", "interne") != "interne")

    def _executor_of(self, pos):
        try:
            import execution
            sess = self.manager.sessions.get(pos.session_id)
            mode = getattr(sess, "mode", "pratique") if sess else "pratique"
            ex = execution.executor_for(mode, execution.asset_of(pos.pair))
            return ex if getattr(ex, "name", "internal") != "internal" else None
        except Exception:
            return None

    def _broker_ref(self, pos):
        return pos.broker_trade_id if str(pos.venue).startswith("OANDA") else pos.pair

    def _broker_modify_stop(self, pos):
        ex = self._executor_of(pos)
        if not ex:
            return
        try:
            ex.modify_stop(self._broker_ref(pos), pos.stop_loss, instrument=pos.pair)
            self._log("execution", "Stop courtier %s -> %.5f" % (pos.pair, pos.stop_loss))
        except Exception as e:
            self._log("execution", "MAJ stop courtier ignorée : %s" % e)

    def _broker_partial(self, pos, qty):
        ex = self._executor_of(pos)
        if not ex:
            return
        try:
            ex.partial_close(self._broker_ref(pos), qty, side=pos.side, instrument=pos.pair)
            self._log("execution", "Prise partielle courtier %s (%s u)" % (pos.pair, qty))
        except Exception as e:
            self._log("execution", "Partielle courtier ignorée : %s" % e)

    def _close_broker(self, pos):
        """Aplatit la position chez le courtier quand le moteur la ferme.
        Indispensable pour Alpaca (pas de SL/TP attaché). Best-effort, jamais bloquant.
        OANDA : si déjà clôturée par son SL/TP attaché, l'appel échoue -> absorbé."""
        if not getattr(pos, "broker_trade_id", None) or getattr(pos, "venue", "interne") == "interne":
            return
        try:
            sess = self.manager.sessions.get(pos.session_id)
            mode = getattr(sess, "mode", "pratique") if sess else "pratique"
            import execution
            ex = execution.executor_for(mode, execution.asset_of(pos.pair))
            if getattr(ex, "name", "internal") == "internal":
                return
            ex.close(pos.broker_trade_id, pos.pair)
            self._log("execution", "Clôture courtier %s (%s)" % (pos.pair, pos.venue))
        except Exception as e:
            try:
                self._log("execution", "Clôture courtier ignorée : %s" % e)
            except Exception:
                pass

    def _update_positions(self, market, now):
        for pos in list(self.positions.values()):
            m = market.get(pos.pair)
            if not m or m.get("price") is None:
                continue
            price = float(m["price"])
            self._manage_exit(pos, price, now)
            reason, exit_price = None, price
            # broker-backed : le courtier détient le SL/TP -> on NE ferme PAS en interne
            # (la clôture est constatée par _reconcile_broker). On garde la sortie TEMPS.
            if not self._is_broker(pos):
                if pos.side == "buy":
                    if price <= pos.stop_loss:
                        reason, exit_price = "STOP", pos.stop_loss
                    elif price >= pos.take_profit:
                        reason, exit_price = "TP", pos.take_profit
                else:
                    if price >= pos.stop_loss:
                        reason, exit_price = "STOP", pos.stop_loss
                    elif price <= pos.take_profit:
                        reason, exit_price = "TP", pos.take_profit
            if reason is None and self._expired(pos, now):
                reason, exit_price = "TIME", price
            if reason:
                self._close(pos, exit_price, reason, now)

    def _expired(self, pos, now):
        try:
            t0 = datetime.strptime(pos.entry_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            return False
        return (now - t0).total_seconds() >= pos.max_hold_min * 60

    def _close(self, pos, exit_price, reason, now, pnl=None):
        # PnL : si le courtier fournit le PnL réalisé (Apprentissage/Réel), on l'utilise ;
        # sinon repli sur le R interne (un STOP d'origine = -1R, un stop trailé peut être >= 0).
        if pnl is None:
            pnl = round(pos.initial_risk * pos.realized_R(exit_price), 2)
        else:
            pnl = round(pnl, 2)
        self.journal.record(Trade(
            pair=pos.pair, side=pos.side, units=pos.units,
            entry_price=pos.entry_price, stop_loss=pos.stop_loss,
            take_profit=pos.take_profit, entry_time=pos.entry_time,
            initial_risk=pos.initial_risk,
            exit_price=round(exit_price, 5),
            exit_time=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            exit_reason=reason, pnl=pnl, profile=pos.side,
            signal_confidence=pos.confidence, caution_factor=pos.caution,
            notes=f"paper session={pos.session_id}"))
        self.manager.record_trade_pnl(pos.session_id, pnl)
        self._day_pnl += pnl
        # Phase 2 : séries gagnantes/perdantes pour le de-risking
        if pnl < 0:
            self._loss_streak += 1
            self._win_streak = 0
            self._last_loss_time[pos.session_id] = now
        elif pnl > 0:
            self._win_streak += 1
            self._loss_streak = 0
        _st = self._sess_stats.setdefault(pos.session_id, {"wins": 0, "losses": 0, "last": None})
        if pnl > 0:
            _st["wins"] += 1
        elif pnl < 0:
            _st["losses"] += 1
        _st["last"] = {"pair": pos.pair, "reason": reason, "pnl": pnl,
                       "R": round(pnl / pos.initial_risk, 2) if pos.initial_risk > 0 else 0.0,
                       "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        self._log("trade", "%s %s · %+.2f$" % (pos.pair, reason, pnl))
        self._close_broker(pos)
        self.positions.pop(pos.id, None)

    def _pos_unreal(self, pos):
        bu = getattr(pos, "broker_unreal", None)
        if bu is not None:                       # vérité courtier (Apprentissage/Réel)
            return round(bu, 2)
        price = self.last_price.get(pos.pair)
        if price is None:
            return 0.0
        return round(pos.initial_risk * pos.realized_R(price), 2)

    # -- vue pour l'API ------------------------------------------------------
    def snapshot(self, now=None):
        now = now or _now()
        return {
            "balance": round(self.manager.balance, 2),
            "available": self.manager.available,
            "reserved": self.manager.reserved,
            "running": self.running,
            "daily_halted": self.daily_halted,
            "day_pnl": round(self._day_pnl, 2),
            "sizing": {"uniform": bool(getattr(__import__("config"), "UNIFORM_SIZING", False)),
                       "base": float(getattr(__import__("config"), "RISK_BASE_CCY", 0) or 0)},
            "open_risk": round(self._open_risk(), 2),
            "broker_nav": self._broker_nav,
            "last_tick": self.last_tick,
            "sessions": [{
                "id": s.id, "allocated": s.allocated, "equity": s.equity,
                "realized_pnl": s.realized_pnl, "trades": s.trades,
                "live_pnl": round(s.realized_pnl + sum(self._pos_unreal(p) for p in self.positions.values() if p.session_id == s.id), 2),
                "tutelle": s.tutelle.value if hasattr(s.tutelle, "value") else s.tutelle,
                "risk_level": s.risk_level,
                "accept_min": s.accept_min, "accept_max": s.accept_max,
                "instrument": getattr(s, "instrument", None),
                "mode": getattr(s, "mode", "pratique"),
                "trader": getattr(s, "trader", "deterministe"),
                "paused": getattr(s, "paused", False),
                "last_look": self.supervisor.last_look.get(s.id),
                "state": s.state.value if hasattr(s.state, "value") else s.state,
                "wins": self._sess_stats.get(s.id, {}).get("wins", 0),
                "losses": self._sess_stats.get(s.id, {}).get("losses", 0),
                "last_event": self._sess_stats.get(s.id, {}).get("last"),
                "has_position": any(x.session_id == s.id for x in self.positions.values()),
            } for s in self.manager.sessions.values()],
            "pending": [{
                "id": p.id, "session_id": p.session_id, "pair": p.pair,
                "side": p.proposal.side, "units": p.units, "risk": p.risk,
                "confidence": p.confidence, "status": p.status,
                "seconds_left": p.seconds_left(now),
                "entry": p.proposal.entry_price, "stop": p.proposal.stop_loss,
                "take_profit": p.proposal.take_profit,
            } for p in self.supervisor.pending.values()],
            "positions": [{
                "id": pos.id, "session_id": pos.session_id, "pair": pos.pair,
                "side": pos.side, "entry": pos.entry_price, "stop": pos.stop_loss,
                "take_profit": pos.take_profit, "confidence": pos.confidence,
                "price": self.last_price.get(pos.pair), "unreal": self._pos_unreal(pos),
            } for pos in self.positions.values()],
        }

    # -- journal d'activité --------------------------------------------------
    def _log(self, kind, msg):
        self.activity.append({"ts": _now().isoformat(), "kind": kind, "msg": msg})
        if len(self.activity) > 200:
            self.activity = self.activity[-200:]

    def emit(self, alert):
        """Reçoit les évènements du superviseur (proposition/validation/expiration)."""
        self._log(getattr(alert, "kind", "info"), getattr(alert, "title", ""))

    # -- persistance (survie aux redémarrages) ------------------------------
    def to_state(self):
        """Sérialise l'état durable (solde, sessions, positions ouvertes).
        Les propositions en attente sont éphémères (régénérées au prochain tick)."""
        return {
            "balance": self.manager.balance,
            "sessions": [{
                "id": s.id, "allocated": s.allocated,
                "profile": s.profile.value if hasattr(s.profile, "value") else s.profile,
                "tutelle": s.tutelle.value if hasattr(s.tutelle, "value") else s.tutelle,
                "duration_min": s.duration_min, "risk_level": s.risk_level,
                "started": s.started.isoformat(),
                "state": s.state.value if hasattr(s.state, "value") else s.state,
                "realized_pnl": s.realized_pnl, "trades": s.trades,
                "accept_min": s.accept_min, "accept_max": s.accept_max,
                "instrument": s.instrument, "paused": getattr(s, "paused", False),
                "mode": getattr(s, "mode", "pratique"),
                "trader": getattr(s, "trader", "deterministe"),
            } for s in self.manager.sessions.values()],
            "positions": [dict(vars(pos)) for pos in self.positions.values()],
            "running": self.running,
            "day": self._day.isoformat() if self._day else None,
            "day_pnl": self._day_pnl,
            "day_start_balance": self._day_start_balance,
            "activity": self.activity[-200:],
            "last_tick": self.last_tick,
            "sess_stats": self._sess_stats,
        }

    def load_state(self, d):
        from datetime import datetime as _dt
        from session import Session, SessionState
        if not d:
            return
        self.manager.balance = float(d.get("balance", self.manager.balance))
        self.manager.sessions = {}
        for sd in d.get("sessions", []):
            s = Session(allocated=sd["allocated"], profile=Profile(sd["profile"]),
                        tutelle=Tutelle(sd["tutelle"]), duration_min=sd["duration_min"],
                        risk_level=sd.get("risk_level", "reserve"))
            s.id = sd["id"]
            try:
                s.started = _dt.fromisoformat(sd["started"])
            except Exception:
                pass
            s.state = SessionState(sd.get("state", "active"))
            s.realized_pnl = sd.get("realized_pnl", 0.0)
            s.trades = sd.get("trades", 0)
            s.accept_min = sd.get("accept_min")
            s.accept_max = sd.get("accept_max")
            s.instrument = sd.get("instrument")
            s.mode = sd.get("mode", "pratique")
            s.trader = sd.get("trader", "deterministe")
            s.paused = sd.get("paused", False)
            self.manager.sessions[s.id] = s
        self.positions = {}
        self._has_pos = set()
        for pd in d.get("positions", []):
            pos = PaperPosition(**pd)
            self.positions[pos.id] = pos
            self._has_pos.add(pos.pending_id)
        self.running = d.get("running", True)
        from datetime import date as _date
        try:
            self._day = _date.fromisoformat(d["day"]) if d.get("day") else None
        except Exception:
            self._day = None
        self._day_pnl = d.get("day_pnl", 0.0)
        self._day_start_balance = d.get("day_start_balance", self.manager.balance)
        self.activity = d.get("activity", [])
        self.last_tick = d.get("last_tick")
        self._sess_stats = d.get("sess_stats", {})
