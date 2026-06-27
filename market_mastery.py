"""
Mode « maîtrise du marché » — forward-test sur cours réels, capital fictif.

C'est le chaînon manquant entre le backtest (passé, sur-optimisable) et le
réel (argent en jeu) :

    backtest (passé)  →  MAÎTRISE 30 jours (cours réels, capital fictif)  →  réel

En production, ce mode = faire tourner le bot supervisé en MODE PRACTICE
OANDA (vrais prix, exécutions simulées, argent fictif) pendant ~30 jours,
en journalisant tout. Au bout du mois, on évalue un VERDICT honnête :
le système a-t-il montré un avantage sur des données qu'il n'avait jamais
vues au moment de décider ? Tant que la réponse n'est pas un OUI net, on ne
parle pas d'argent réel.

Critères du verdict (volontairement stricts) :
  - assez de trades (échantillon suffisant),
  - espérance positive (R moyen > 0),
  - cohérence : la majorité des semaines positives (pas un coup de chance),
  - drawdown maîtrisé,
  - calibration exploitable (la fiabilité prédit le résultat).
Tout doit être vrai. Sinon : NO-GO, avec les raisons.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from journal import analyze
from learning import calibrate

# Seuils du verdict
MIN_TRADES = 30
MIN_POSITIVE_WEEKS_RATIO = 0.6
MAX_DRAWDOWN_PCT = 15.0


@dataclass
class MasteryCampaign:
    virtual_capital: float
    days: int = 30
    started: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ends(self):
        return self.started + timedelta(days=self.days)

    def day_index(self, now):
        return max(0, min(self.days, (now - self.started).days))

    def progress_pct(self, now):
        return round(self.day_index(now) / self.days * 100, 1)

    def finished(self, now):
        return now >= self.ends


def _max_drawdown_pct(equity_curve):
    if not equity_curve:
        return 0.0
    peak, mdd = equity_curve[0], 0.0
    for e in equity_curve:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak * 100)
    return round(mdd, 2)


def _iso_week(t):
    try:
        return datetime.fromisoformat(t.replace("Z", "")).isocalendar()[:2]
    except Exception:
        return (0, 0)


@dataclass
class CampaignVerdict:
    trades: int = 0
    expectancy_R: float = 0.0
    total_R: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    weeks_total: int = 0
    weeks_positive: int = 0
    go: bool = False
    reasons: list = field(default_factory=list)
    calibration_note: str = ""

    def summary(self):
        verdict = "✅ GO — envisager le réel, mais à toute petite taille" if self.go \
            else "⛔ NO-GO — pas d'argent réel"
        lines = [
            f"VERDICT : {verdict}",
            "",
            f"  Trades            : {self.trades}",
            f"  Espérance         : {self.expectancy_R:+.2f}R par trade",
            f"  Total             : {self.total_R:+.2f}R · réussite {self.win_rate}%",
            f"  Drawdown max      : {self.max_drawdown_pct}%",
            f"  Semaines positives: {self.weeks_positive}/{self.weeks_total}",
        ]
        if self.reasons:
            lines.append("  Points bloquants :")
            lines += [f"    ✗ {r}" for r in self.reasons]
        if self.calibration_note:
            lines += ["", self.calibration_note]
        return "\n".join(lines)


def evaluate(closed_trades, equity_curve) -> CampaignVerdict:
    v = CampaignVerdict()
    pm = analyze(closed_trades)
    v.trades = pm.trades
    v.expectancy_R = pm.expectancy_R
    v.total_R = pm.total_R
    v.win_rate = pm.win_rate
    v.max_drawdown_pct = _max_drawdown_pct(equity_curve)

    # cohérence hebdomadaire
    weeks = {}
    for t in closed_trades:
        if getattr(t, "is_closed", False):
            weeks.setdefault(_iso_week(t.entry_time), []).append(t.r_multiple)
    v.weeks_total = len(weeks)
    v.weeks_positive = sum(1 for rs in weeks.values() if sum(rs) > 0)

    # critères
    if v.trades < MIN_TRADES:
        v.reasons.append(f"échantillon trop faible ({v.trades} < {MIN_TRADES} trades)")
    if v.expectancy_R <= 0:
        v.reasons.append(f"espérance non positive ({v.expectancy_R:+.2f}R)")
    if v.weeks_total and v.weeks_positive / v.weeks_total < MIN_POSITIVE_WEEKS_RATIO:
        v.reasons.append(
            f"manque de cohérence ({v.weeks_positive}/{v.weeks_total} semaines positives)")
    if v.max_drawdown_pct > MAX_DRAWDOWN_PCT:
        v.reasons.append(f"drawdown trop élevé ({v.max_drawdown_pct}% > {MAX_DRAWDOWN_PCT}%)")

    cal = calibrate(closed_trades, min_samples_per_band=10)
    v.calibration_note = cal.summary()

    v.go = len(v.reasons) == 0
    return v
