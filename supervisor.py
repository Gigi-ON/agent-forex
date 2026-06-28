"""
Superviseur — le bot « sous tutelle ».

Pour chaque session active, il enchaîne le moteur déterministe
(signal -> macro -> risque, dimensionné sur le BUDGET DE LA SESSION) et,
au lieu d'exécuter, crée une VALIDATION EN ATTENTE avec un minuteur de 20 s.

  - Tutelle MANUEL : chaque proposition attend votre clic (approuver / rejeter
    / modifier). Sans réponse en 20 s -> EXPIRÉE -> aucune action (sûr).
  - Tutelle AUTO   : auto-approbation UNIQUEMENT si confiance élevée ET prudence
    macro à 1,0 ET risque sous le sous-plafond du niveau choisi. Sinon, on
    bascule sur validation manuelle. L'inaction reste l'issue par défaut.

L'exécution réelle reste verrouillée par config.LIVE_TRADING. En practice,
"exécuter" = journaliser le trade comme approuvé.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from signals import SignalEngine
from risk_manager import RiskManager
from news import RiskModulator
from indicators import atr
from journal import Trade
from session import Tutelle, AUTO_RISK_CAP
from alerts import Alert

EXPIRY_SECONDS = 20
AUTO_MIN_CONFIDENCE = 0.75


@dataclass
class Pending:
    session_id: str
    pair: str
    proposal: object             # TradeProposal
    units: int
    risk: float
    leverage: float
    confidence: float
    caution: float
    created: datetime
    expiry_s: int = EXPIRY_SECONDS
    status: str = "pending"      # pending|approved|rejected|expired|modified
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def expires_at(self):
        return self.created + timedelta(seconds=self.expiry_s)

    def seconds_left(self, now):
        return max(0, int((self.expires_at - now).total_seconds()))


class Supervisor:
    def __init__(self, manager, journal_store=None, alert_sink=None,
                 engine=None, modulator=None):
        self.manager = manager
        self.journal = journal_store
        self.alerts = alert_sink
        self.engine = engine or SignalEngine()
        self.modulator = modulator or RiskModulator()
        self.pending = {}            # id -> Pending

    # -- proposer ------------------------------------------------------------
    def propose(self, session, pair, candles, news_items,
                quote_to_account, base_to_account, now=None):
        now = now or datetime.now(timezone.utc)

        sig = self.engine.evaluate(pair, candles)
        if not sig.proposal:
            return None
        decision = self.modulator.assess(news_items, pair, now)
        if decision.blackout:
            return None

        # dimensionné sur le BUDGET DE LA SESSION (pas tout le compte)
        rm = RiskManager(profile=session.profile)
        a_cur, a_avg = atr(candles, 14)
        sized = rm.size_position(
            proposal=sig.proposal, equity_account_ccy=session.equity,
            quote_to_account_rate=quote_to_account,
            base_to_account_rate=base_to_account,
            current_atr=a_cur, average_atr=a_avg,
            external_caution=decision.caution_factor)
        if not sized.accepted or sized.units == 0:
            return None

        p = Pending(
            session_id=session.id, pair=pair, proposal=sig.proposal,
            units=sized.units, risk=sized.risk_amount_account_ccy,
            leverage=sized.effective_leverage, confidence=sig.confidence,
            caution=decision.caution_factor, created=now)
        self.pending[p.id] = p

        # AUTO : peut-on auto-approuver ?
        if self._auto_ok(session, p):
            self.approve(p.id, now, auto=True)
            return p

        # sinon : alerte de validation
        if self.alerts:
            self.alerts.emit(Alert(
                kind="approval",
                title=f"Validation requise — {pair} {sig.proposal.side}",
                body=(f"Session {session.id} · {sized.units} u · risque "
                      f"{sized.risk_amount_account_ccy} · conf {sig.confidence} · "
                      f"expire dans {EXPIRY_SECONDS}s"),
                session_id=session.id, approval_id=p.id))
        return p

    def _auto_ok(self, session, p: Pending) -> bool:
        # Auto-validation pilotee par la BANDE D'ACCEPTATION de la session :
        #   min <= confiance <= max  ET prudence macro pleine ET risque sous le
        #   sous-plafond. Sans bande definie -> jamais d'auto (mode manuel).
        lo = getattr(session, "accept_min", None)
        hi = getattr(session, "accept_max", None)
        if lo is None or hi is None:
            return False
        cap = AUTO_RISK_CAP.get(session.risk_level, 0.5)
        risk_pct = p.risk / session.equity * 100 if session.equity else 99
        return (lo <= p.confidence <= hi
                and p.caution >= 1.0
                and risk_pct <= cap)

    # -- décisions humaines --------------------------------------------------
    def approve(self, pending_id, now=None, auto=False):
        now = now or datetime.now(timezone.utc)
        p = self.pending.get(pending_id)
        if not p or p.status != "pending":
            return None
        p.status = "approved"
        self._execute(p, now, auto)
        return p

    def reject(self, pending_id):
        p = self.pending.get(pending_id)
        if not p or p.status != "pending":
            return None
        p.status = "rejected"
        if self.alerts:
            self.alerts.emit(Alert(kind="rejected", title=f"Ordre rejeté — {p.pair}",
                                   body="Aucune position ouverte.",
                                   session_id=p.session_id, channels=("inapp",)))
        return p

    def modify(self, pending_id, new_units=None, new_stop=None, now=None):
        """Modifier relance le minuteur (nouvelle proposition à valider)."""
        now = now or datetime.now(timezone.utc)
        p = self.pending.get(pending_id)
        if not p or p.status != "pending":
            return None
        p.status = "modified"
        if new_units is not None:
            p.units = new_units
        if new_stop is not None:
            p.proposal.stop_loss = new_stop
        clone = Pending(session_id=p.session_id, pair=p.pair, proposal=p.proposal,
                        units=p.units, risk=p.risk, leverage=p.leverage,
                        confidence=p.confidence, caution=p.caution, created=now)
        self.pending[clone.id] = clone
        return clone

    # -- expiration : INACTION par défaut ------------------------------------
    def sweep(self, now=None):
        now = now or datetime.now(timezone.utc)
        expired = []
        for p in self.pending.values():
            if p.status == "pending" and now >= p.expires_at:
                p.status = "expired"
                expired.append(p.id)
                if self.alerts:
                    self.alerts.emit(Alert(
                        kind="expired", title=f"Proposition expirée — {p.pair}",
                        body="Non validée à temps : aucune action (sûr).",
                        session_id=p.session_id, channels=("inapp",)))
        return expired

    # -- exécution (practice : journalise comme approuvé) --------------------
    def _execute(self, p: Pending, now, auto):
        if self.alerts:
            self.alerts.emit(Alert(
                kind="executed",
                title=f"{'Auto-' if auto else ''}exécuté — {p.pair} {p.proposal.side}",
                body=f"{p.units} u envoyés (mode practice).",
                session_id=p.session_id, channels=("toast", "inapp")))
        if self.journal:
            # entrée journalisée ; la clôture (P&L) viendra du suivi de position
            init_risk = abs(p.units) * abs(
                p.proposal.entry_price - p.proposal.stop_loss)
            self.journal.record(Trade(
                pair=p.pair, side=p.proposal.side, units=p.units,
                entry_price=p.proposal.entry_price, stop_loss=p.proposal.stop_loss,
                take_profit=p.proposal.take_profit,
                entry_time=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                initial_risk=init_risk, exit_time="", profile=p.proposal.side,
                signal_confidence=p.confidence, caution_factor=p.caution,
                notes=f"session={p.session_id} auto={auto}"))
