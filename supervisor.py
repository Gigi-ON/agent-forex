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
import config
from risk_manager import RiskManager
from news import RiskModulator
from indicators import atr
from journal import Trade
from session import Tutelle, AUTO_RISK_CAP
from alerts import Alert

try:
    from config import PHASE1 as _P1
except Exception:
    _P1 = {}
try:
    from config import PHASE2 as _P2
except Exception:
    _P2 = {}


def _legs(pair):
    """Devise de base et de cotation d'une paire ('EUR_USD' ou 'BTC/USD')."""
    x = pair.replace("/", "_")
    return (x.split("_", 1) + [None, None])[:2] if "_" in x else (pair, None)


EXPIRY_SECONDS = 20


def _journal_decision(session_id, pair, side, conf, risk, kind, reason, now=None):
    """Enregistre la décision dans le journal (best-effort, jamais bloquant)."""
    try:
        import decisions
        ts = now.timestamp() if (now is not None and hasattr(now, "timestamp")) else None
        decisions.record(session_id, pair, side, conf, risk, kind, reason, ts=ts)
    except Exception:
        pass


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
        self.last_look = {}          # session_id -> dernière lecture du moteur (visibilité)
        self._grok_engine = None     # chasseur Grok (lazy)

    def _grok(self):
        if self._grok_engine is None:
            from grok_signals import GrokSignalEngine
            self._grok_engine = GrokSignalEngine()
        return self._grok_engine

    # -- proposer ------------------------------------------------------------
    def propose(self, session, pair, candles, news_items,
                quote_to_account, base_to_account, now=None, spread=None,
                portfolio=None, risk_scale=1.0):
        now = now or datetime.now(timezone.utc)

        # ROUTAGE TRADER : Déterministe | Grok | Hybride (confluence). Grok jamais en Réel.
        trader = getattr(session, "trader", "deterministe")
        if trader in ("grok", "hybride") and getattr(session, "mode", "pratique") == "reel":
            trader = "deterministe"   # GATE paper-only : le LLM ne pilote jamais l'argent réel
        if trader == "grok":
            sig = self._grok().evaluate(pair, candles)
        elif trader == "hybride":
            sig = self.engine.evaluate(pair, candles)        # déterministe gate (peu coûteux)
            if sig.proposal:
                g = self._grok().evaluate(pair, candles)     # Grok consulté seulement si setup
                if g.proposal and g.proposal.side == sig.proposal.side:
                    sig.confidence = round(min(1.0, sig.confidence + 0.15), 2)
                    sig.notes.append("Grok confirme (confluence) +0.15")
                elif g.proposal and g.proposal.side != sig.proposal.side:
                    sig.confidence = round(max(0.0, sig.confidence - 0.15), 2)
                    sig.notes.append("Grok en désaccord -0.15")
                else:
                    sig.notes.append("Grok : pas de confirmation")
        else:
            sig = self.engine.evaluate(pair, candles)
        self.last_look[session.id] = {
            "pair": pair,
            "note": (sig.notes[-1] if sig.notes else ""),
            "has_signal": bool(sig.proposal),
            "conf": round(sig.confidence, 2),
        }
        if not sig.proposal:
            return None
        decision = self.modulator.assess(news_items, pair, now)
        if decision.blackout:
            return None

        # FILTRE DE SPREAD : si le coût d'entrée mange une part trop grande du
        # risque (stop serré), l'edge disparaît -> on s'abstient.
        if spread is not None and spread > 0:
            stop_dist = abs(sig.proposal.entry_price - sig.proposal.stop_loss)
            import strategy as _S
            maxf = _S.P1().get("max_spread_frac", 0.30)
            if stop_dist > 0 and spread > maxf * stop_dist:
                self.last_look[session.id]["note"] = (
                    "Spread %.5f > %d%% du stop : trade ignoré." % (spread, int(maxf * 100)))
                return None

        # dimensionné sur le BUDGET DE LA SESSION (pas tout le compte)
        rm = RiskManager(profile=session.profile)
        a_cur, a_avg = atr(candles, 14)
        pf = portfolio or {}
        sized = rm.size_position(
            proposal=sig.proposal, equity_account_ccy=session.equity,
            risk_base=(config.RISK_BASE_CCY if getattr(config, 'UNIFORM_SIZING', False) else 0.0),
            quote_to_account_rate=quote_to_account,
            base_to_account_rate=base_to_account,
            current_atr=a_cur, average_atr=a_avg,
            external_caution=decision.caution_factor,
            whole_units=("/" not in pair),
            portfolio_open_risk=pf.get("open_risk", 0.0),
            portfolio_equity=pf.get("equity", 0.0),
            streak_scale=risk_scale)
        if not sized.accepted or sized.units == 0:
            _reason = sized.reasons[0] if sized.reasons else "refusé au dimensionnement"
            if sized.reasons:
                self.last_look[session.id]["note"] = _reason
            _journal_decision(session.id, pair, sig.proposal.side, sig.confidence, 0.0,
                               "rejected", _reason, now)
            return None

        # GARDE DE CORRÉLATION : refuser si ce trade pousse l'exposition NETTE
        # d'une devise au-delà du plafond (sinon on empile des paris corrélés,
        # ex. long EUR via EUR/USD + EUR/GBP = un seul gros pari déguisé).
        if pf.get("ccy_exposure") is not None and pf.get("equity", 0) > 0:
            base_c, quote_c = _legs(pair)
            sgn = 1.0 if sig.proposal.side == "buy" else -1.0
            r = sized.risk_amount_account_ccy
            exp = pf["ccy_exposure"]
            import strategy as _S
            cap = pf["equity"] * _S.P2().get("max_ccy_heat_pct", 4.0) / 100.0
            legs = [(base_c, sgn * r), (quote_c, -sgn * r)]
            for ccy, delta in legs:
                if ccy and abs(exp.get(ccy, 0.0) + delta) > cap:
                    _msg = "Exposition %s trop concentrée : trade ignoré (corrélation)." % ccy
                    self.last_look[session.id]["note"] = _msg
                    _journal_decision(session.id, pair, sig.proposal.side, sig.confidence,
                                      sized.risk_amount_account_ccy, "rejected", _msg, now)
                    return None

        p = Pending(
            session_id=session.id, pair=pair, proposal=sig.proposal,
            units=sized.units, risk=sized.risk_amount_account_ccy,
            leverage=sized.effective_leverage, confidence=sig.confidence,
            caution=decision.caution_factor, created=now)
        self.pending[p.id] = p

        # DÉCISION : auto-validation ou attente, avec RAISON explicite (observabilité UI)
        ok, why = self._auto_decision(session, p, now)
        self.last_look[session.id]["note"] = why
        self.last_look[session.id]["decision"] = "auto" if ok else "pending"
        _journal_decision(session.id, pair, p.proposal.side, p.confidence, p.risk,
                          "auto" if ok else "pending", why, now)
        if ok:
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

    def _auto_decision(self, session, p: Pending, now=None):
        """Renvoie (auto_ok, raison_lisible). La raison alimente l'UI -> on voit
        EXACTEMENT pourquoi un signal valide trade ou attend."""
        side = p.proposal.side
        cpct = round(p.confidence * 100)
        lo = getattr(session, "accept_min", None)
        hi = getattr(session, "accept_max", None)
        if lo is None or hi is None:
            return False, "⏳ Manuel — %s conf %d%% : à valider (Oui/Non)" % (side, cpct)
        if not (lo <= p.confidence <= hi):
            return False, "⏳ Conf %d%% hors bande %d–%d%% — à valider" % (cpct, round(lo * 100), round(hi * 100))
        if p.caution < 1.0:
            return False, "⏳ Prudence news (%.2f) — à valider" % p.caution
        cap = AUTO_RISK_CAP.get(session.risk_level, 0.5)
        risk_pct = p.risk / session.equity * 100 if session.equity else 99
        if risk_pct > cap:
            return False, "⏳ Risque %.1f%% > plafond %.1f%% — à valider" % (risk_pct, cap)
        # GARDE DE SESSION (Niveau 3) : forex hors séance -> pas d'auto (crypto 24/7 OK)
        import strategy as _S
        if _S.P1().get("session_guard", True) and "/" not in p.pair:
            try:
                import sessions_clock as _sc
                if _sc.score_pair(p.pair, _sc.open_sessions(_sc._now_utc())) <= 0:
                    return False, "⛔ Hors séance (%s) — auto bloqué, à valider" % p.pair.replace("_", "/")
            except Exception:
                pass
        return True, "✅ Auto-validé — %s conf %d%%" % (side, cpct)

    def _auto_ok(self, session, p: Pending) -> bool:
        return self._auto_decision(session, p, None)[0]

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
