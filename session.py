"""
Sessions de trading.

Modèle demandé : pour lancer le bot, on lui alloue une somme prélevée du
capital DISPONIBLE du compte. La session trade avec ce budget sur une
période, puis rend "capital ± résultat" au solde commun. Jusqu'à 5 sessions
peuvent tourner en parallèle, chacune avec son propre budget et son propre
niveau de tutelle.

Comptabilité (source unique de vérité = `balance`) :
  - `balance`            : équity total du compte (bouge à chaque trade clôturé).
  - `allocated` (session): budget réservé à la session. Sert de base au
    dimensionnement (chaque session risque un % de SON budget) et de plafond.
  - `available`          : balance − somme des budgets des sessions actives.
    Empêche d'engager deux fois les mêmes dollars.
  - À la clôture d'une session, le résultat est déjà dans `balance` ; on
    libère simplement la réservation. Garde-fou : une session ne peut pas
    perdre plus que son budget (auto-clôture si seuil atteint).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from risk_manager import Profile


class SessionState(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class Tutelle(str, Enum):
    MANUEL = "manuel"   # chaque ordre validé à la main
    AUTO = "auto"       # auto-approbation dans des sous-limites (par niveau de risque)


# Pour le mode AUTO : sous-plafond de risque par trade selon le niveau choisi.
AUTO_RISK_CAP = {"reserve": 0.5, "doux": 1.0, "agressif": 1.5}
# Une session ne peut pas perdre plus que ce % de son budget -> auto-clôture.
SESSION_MAX_LOSS_PCT = 25.0


@dataclass
class Session:
    allocated: float
    profile: Profile
    tutelle: Tutelle
    duration_min: int
    risk_level: str = "reserve"          # utilisé en mode AUTO
    accept_min: float = None             # bande d'auto-validation (§ intervalle)
    accept_max: float = None             # min<=confiance<=max -> auto ; sinon attente->inaction
    instrument: str = None               # nom OANDA (ex EUR_USD) ; None = config.INSTRUMENTS
    mode: str = "pratique"               # pratique (sim interne) | apprentissage (paper courtier) | reel
    paused: bool = False                 # pause par session : gèle les nouvelles entrées
    started: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state: SessionState = SessionState.ACTIVE
    realized_pnl: float = 0.0
    trades: int = 0

    @property
    def equity(self):
        """Budget de travail courant de la session."""
        return round(self.allocated + self.realized_pnl, 2)

    @property
    def expires_at(self):
        return self.started + timedelta(minutes=self.duration_min)

    def time_expired(self, now):
        return now >= self.expires_at

    def loss_breached(self):
        return self.realized_pnl <= -self.allocated * SESSION_MAX_LOSS_PCT / 100.0

    def apply_pnl(self, pnl):
        self.realized_pnl = round(self.realized_pnl + pnl, 2)
        self.trades += 1


class SessionManager:
    MAX_CONCURRENT = 5

    def __init__(self, starting_balance):
        self.balance = float(starting_balance)
        self.sessions = {}          # id -> Session

    @property
    def active(self):
        return [s for s in self.sessions.values() if s.state == SessionState.ACTIVE]

    @property
    def reserved(self):
        return sum(s.allocated for s in self.active)

    @property
    def available(self):
        """Capital non engagé, mobilisable pour une nouvelle session."""
        return round(self.balance - self.reserved, 2)

    def open_session(self, allocated, profile=Profile.RESERVE,
                     tutelle=Tutelle.MANUEL, duration_min=120, risk_level="reserve"):
        if len(self.active) >= self.MAX_CONCURRENT:
            raise ValueError(f"Maximum {self.MAX_CONCURRENT} sessions simultanées atteint.")
        if allocated <= 0:
            raise ValueError("Le budget de session doit être positif.")
        if allocated > self.available:
            raise ValueError(
                f"Budget {allocated} > capital disponible {self.available}.")
        s = Session(allocated=allocated, profile=profile, tutelle=tutelle,
                    duration_min=duration_min, risk_level=risk_level)
        self.sessions[s.id] = s
        return s

    def record_trade_pnl(self, session_id, pnl):
        """À la clôture d'un trade : impacte la session ET le solde commun."""
        s = self.sessions[session_id]
        s.apply_pnl(pnl)
        self.balance = round(self.balance + pnl, 2)
        if s.loss_breached():
            self.close_session(session_id, reason="perte max de session atteinte")
        return s

    def close_session(self, session_id, reason="période terminée"):
        """Rend le budget au solde commun (le résultat y est déjà). Libère la réservation."""
        s = self.sessions[session_id]
        if s.state == SessionState.CLOSED:
            return s
        s.state = SessionState.CLOSED
        s.close_reason = reason
        return s

    def sweep_expired(self, now):
        """Clôture les sessions dont la période est écoulée."""
        closed = []
        for s in self.active:
            if s.time_expired(now):
                self.close_session(s.id, reason="période terminée")
                closed.append(s.id)
        return closed
