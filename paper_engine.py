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

from session import SessionManager, Tutelle
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
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def risk_distance(self):
        return abs(self.entry_price - self.stop_loss)

    def realized_R(self, price):
        """R réalisé pour un prix de sortie donné (signé selon le sens)."""
        d = self.risk_distance
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
        # Le superviseur ne journalise PAS à l'ouverture (journal=None) :
        # le moteur journalise à la CLÔTURE (trade complet avec P&L).
        self.supervisor = Supervisor(self.manager, journal_store=None,
                                     engine=engine, modulator=modulator)
        self.positions = {}          # id -> PaperPosition (ouvertes)
        self._has_pos = set()        # pending_ids déjà transformés en position
        # --- garde-fous (Phase A) ---
        self.running = True          # pause / kill-switch -> stoppe les NOUVELLES propositions
        self._day = None             # date UTC courante (coupe-circuit journalier)
        self._day_pnl = 0.0          # P&L réalisé du jour
        self._day_start_balance = float(starting_balance)
        from config import HARD_LIMITS as _HL
        self._HL = _HL

    # -- sessions -----------------------------------------------------------
    def open_session(self, budget, accept_min=None, accept_max=None,
                     profile=Profile.RESERVE, risk_level="reserve",
                     duration_min=240, tutelle=Tutelle.MANUEL):
        s = self.manager.open_session(allocated=budget, profile=profile,
                                      tutelle=tutelle, duration_min=duration_min,
                                      risk_level=risk_level)
        s.accept_min = accept_min
        s.accept_max = accept_max
        return s

    def close_session(self, session_id, reason="clôture manuelle"):
        return self.manager.close_session(session_id, reason=reason)

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
        self._roll_day(now)
        self.supervisor.sweep(now)
        self.manager.sweep_expired(now)
        self._update_positions(market, now)

        if self.running and not self.daily_halted:
            for session in list(self.manager.active):
                if not self._can_open_more():
                    break
                for pair, m in market.items():
                    if not self._can_open_more():
                        break
                    if not self._tradeable(pair, m, now):
                        continue
                    self.supervisor.propose(
                        session, pair, m.get("candles", []), m.get("news", []),
                        m.get("q2a", 1.0), m.get("b2a", 1.0), now)
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

    @property
    def daily_halted(self):
        cap = self._HL.get("max_daily_loss_pct", 4.0)
        return self._day_pnl <= -(self._day_start_balance * cap / 100.0)

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

    def pause(self):
        self.running = False

    def resume(self):
        self.running = True

    def kill(self, market=None, now=None):
        """Arrêt d'urgence : ferme toutes les positions au prix courant, stoppe le moteur."""
        now = now or _now()
        if market:
            for pos in list(self.positions.values()):
                m = market.get(pos.pair)
                price = float(m["price"]) if (m and m.get("price") is not None) else pos.entry_price
                self._close(pos, price, "KILL", now)
        self.running = False
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
                    entry_time=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
                self.positions[pos.id] = pos

    def _update_positions(self, market, now):
        for pos in list(self.positions.values()):
            m = market.get(pos.pair)
            if not m or m.get("price") is None:
                continue
            price = float(m["price"])
            reason, exit_price = None, price
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

    def _close(self, pos, exit_price, reason, now):
        R = -1.0 if reason == "STOP" else pos.realized_R(exit_price)
        pnl = round(pos.initial_risk * R, 2)
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
        self.positions.pop(pos.id, None)

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
            "open_risk": round(self._open_risk(), 2),
            "sessions": [{
                "id": s.id, "allocated": s.allocated, "equity": s.equity,
                "realized_pnl": s.realized_pnl, "trades": s.trades,
                "tutelle": s.tutelle.value if hasattr(s.tutelle, "value") else s.tutelle,
                "risk_level": s.risk_level,
                "accept_min": s.accept_min, "accept_max": s.accept_max,
                "state": s.state.value if hasattr(s.state, "value") else s.state,
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
            } for pos in self.positions.values()],
        }

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
            } for s in self.manager.sessions.values()],
            "positions": [dict(vars(pos)) for pos in self.positions.values()],
            "running": self.running,
            "day": self._day.isoformat() if self._day else None,
            "day_pnl": self._day_pnl,
            "day_start_balance": self._day_start_balance,
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
