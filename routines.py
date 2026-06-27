"""
Orchestrateur de routines — inspiré de la vidéo « Claude 24/7 trader »,
mais adapté au FOREX et à notre philosophie (moteur déterministe au cœur,
mode practice par défaut, garde-fous durs).

Différence clé avec la vidéo : ce n'est PAS le LLM qui calcule les tailles
ni qui place les ordres à l'aveugle. Les routines enchaînent nos modules
déterministes (signaux -> news -> risque -> journal). Un LLM pourra plus
tard enrichir la "recherche de session" en synthèse de contexte, mais
jamais décider seul ni faire l'arithmétique.

Adapté au forex : pas de "pré-marché / ouverture / clôture" actions, mais
des sessions (Tokyo, Londres, New York) et des créneaux d'annonces macro.

Les routines sont conçues pour être appelées par cron (voir
DEPLOIEMENT_HOSTINGER.md) via run_routine.py.
"""

from datetime import datetime, timezone

from signals import SignalEngine
from risk_manager import RiskManager, Profile
from news import RiskModulator
from journal import Trade, analyze


# Sessions forex (heures UTC approximatives). Sert au contexte, pas à interdire.
SESSIONS = {
    "Tokyo": (0, 9),
    "Londres": (7, 16),
    "New York": (13, 22),
}


def active_sessions(now: datetime):
    h = now.hour
    return [name for name, (a, b) in SESSIONS.items() if a <= h < b]


class TradingDay:
    """
    Enchaîne les routines de la journée. Dépendances injectées pour rester
    testable hors-ligne (on passe des données ; en prod run_routine.py câble
    OANDA + news réels).
    """

    def __init__(self, profile=Profile.RESERVE, journal_store=None,
                 engine=None, modulator=None):
        self.profile = profile
        self.store = journal_store
        self.engine = engine or SignalEngine()
        self.modulator = modulator or RiskModulator()
        self.rm = RiskManager(profile=profile)

    # -- 1) Recherche de session : pose la POSTURE du moment (pas d'ordre) ----
    def session_research(self, news_items, regime="neutre", now=None) -> dict:
        now = now or datetime.now(timezone.utc)
        sessions = active_sessions(now)
        # prudence agrégée sur les paires suivies
        cautions, blackouts = [], []
        for pair in ("EUR_USD", "EUR_CAD"):
            d = self.modulator.assess(news_items, pair, now)
            cautions.append(d.caution_factor)
            if d.blackout:
                blackouts.append(pair)
        posture = {
            "heure_utc": now.strftime("%H:%M"),
            "sessions_actives": sessions or ["aucune (faible liquidité)"],
            "regime_macro": regime,
            "prudence": min(cautions) if cautions else 1.0,
            "paires_bloquees": blackouts,
            "nouvelles_entrees": "non" if blackouts and len(blackouts) == 2 else "oui",
        }
        return posture

    # -- 2) Scan d'exécution : décide, dimensionne, JOURNALISE l'intention ----
    def execution_scan(self, pair, candles, news_items, equity,
                       quote_to_account, base_to_account, now=None) -> dict:
        now = now or datetime.now(timezone.utc)

        # a) le signal PROPOSE
        sig = self.engine.evaluate(pair, candles)
        if not sig.proposal:
            return {"pair": pair, "action": "aucune", "raison": sig.notes[-1]}

        # b) la couche macro peut BLOQUER ou réduire
        decision = self.modulator.assess(news_items, pair, now)
        if decision.blackout:
            return {"pair": pair, "action": "bloque",
                    "raison": decision.reasons[0]}

        # c) le moteur de risque DISPOSE (déterministe)
        from indicators import atr
        a_cur, a_avg = atr(candles, 14)
        sized = self.rm.size_position(
            proposal=sig.proposal, equity_account_ccy=equity,
            quote_to_account_rate=quote_to_account,
            base_to_account_rate=base_to_account,
            current_atr=a_cur, average_atr=a_avg,
            external_caution=decision.caution_factor,
        )
        if not sized.accepted or sized.units == 0:
            return {"pair": pair, "action": "refuse",
                    "raison": "; ".join(sized.reasons) or "taille nulle"}

        # d) intention d'ordre (en practice : journalisée, jamais envoyée réelle)
        return {
            "pair": pair, "action": "ordre",
            "sens": sig.proposal.side, "unites": sized.units,
            "entree": sig.proposal.entry_price, "stop": sig.proposal.stop_loss,
            "tp": sig.proposal.take_profit,
            "risque": sized.risk_amount_account_ccy,
            "levier": sized.effective_leverage,
            "confiance": sig.confidence,
            "prudence_macro": decision.caution_factor,
        }

    # -- 3) Surveillance : coupe-circuit journalier ---------------------------
    def monitor(self, equity) -> dict:
        breached = self.rm.daily_loss_breached(equity)
        return {"perte_journaliere_depassee": breached,
                "action": "tout fermer" if breached else "rien"}

    # -- 4) Bilan de fin de journée : post-mortem -----------------------------
    def end_of_day(self) -> str:
        if not self.store:
            return "Pas de journal disponible."
        pm = analyze(self.store.closed_trades())
        return pm.summary()

    # -- 5) Revue hebdomadaire (même post-mortem, à fréquence hebdo) ----------
    def weekly_review(self) -> str:
        return self.end_of_day()
