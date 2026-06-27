"""
Rapport de session + boucle de calibration ("apprentissage" du bot).

Deux choses distinctes, et il faut être honnête sur chacune :

1) RAPPORT DE SESSION — purement factuel : à la fin d'une session, gain ou
   perte, de combien, en valeur et en % du budget, combien de trades.

2) CALIBRATION — la seule "amélioration" légitime. On regroupe les trades
   clôturés par tranche de FIABILITÉ du signal et on mesure l'espérance (R
   moyen) de chaque tranche. Si les occasions peu fiables perdent, le bot
   relève son seuil et les IGNORE. Le bot n'apprend pas à mieux PRÉDIRE ; il
   apprend à mieux SE RETIRER.

   Garde-fou anti-sur-apprentissage : on ne tire AUCUNE conclusion sous un
   nombre minimum de trades par tranche. Sur peu de données, on apprend le
   bruit, pas un avantage. La calibration n'a de sens que sur l'historique
   réel accumulé, pas sur trois trades.
"""

from dataclasses import dataclass, field


@dataclass
class SessionReport:
    session_id: str
    allocated: float
    pnl: float
    trades: int
    mode: str = ""
    risk_level: str = ""
    duration_min: int = 0
    close_reason: str = ""

    @property
    def verdict(self):
        if abs(self.pnl) < 1e-9:
            return "NEUTRE"
        return "GAIN" if self.pnl > 0 else "PERTE"

    @property
    def pct(self):
        return round(self.pnl / self.allocated * 100, 2) if self.allocated else 0.0

    def summary(self):
        sign = "+" if self.pnl >= 0 else ""
        return (
            f"Session {self.session_id} — {self.verdict}\n"
            f"  Résultat : {sign}{self.pnl:.2f} $  ({sign}{self.pct:.2f} % du budget)\n"
            f"  Budget   : {self.allocated:.0f} $ → rendu {self.allocated + self.pnl:.2f} $\n"
            f"  Trades   : {self.trades} · mode {self.mode} · risque {self.risk_level}\n"
            f"  Clôture  : {self.close_reason or 'période terminée'}"
        )


def report_for(session) -> SessionReport:
    return SessionReport(
        session_id=session.id, allocated=session.allocated,
        pnl=session.realized_pnl, trades=session.trades,
        mode=getattr(session, "tutelle").value if hasattr(session, "tutelle") else "",
        risk_level=getattr(session, "risk_level", ""),
        duration_min=getattr(session, "duration_min", 0),
        close_reason=getattr(session, "close_reason", ""),
    )


# Tranches de fiabilité (confiance du signal).
BANDS = [("< 0,70", 0.0, 0.70), ("0,70–0,80", 0.70, 0.80), ("≥ 0,80", 0.80, 1.01)]


@dataclass
class Calibration:
    bands: list = field(default_factory=list)   # (label, n, expectancy_R)
    recommended_min_confidence: float = 0.0
    enough_data: bool = False
    note: str = ""

    def summary(self):
        lines = ["Calibration par fiabilité du signal :"]
        for label, n, exp in self.bands:
            verdict = "—" if n == 0 else ("positif" if exp > 0 else "négatif")
            lines.append(f"  fiabilité {label:<10} : {n:>3} trades, espérance "
                         f"{exp:+.2f}R ({verdict})")
        if self.enough_data:
            lines.append(f"→ Seuil retenu : le bot privilégie les occasions de "
                         f"fiabilité ≥ {self.recommended_min_confidence:.2f}.")
        else:
            lines.append("→ Données insuffisantes : aucun seuil appris pour l'instant "
                         "(on n'apprend pas du bruit).")
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)


def calibrate(closed_trades, min_samples_per_band=10) -> Calibration:
    """
    Regroupe les trades clôturés par tranche de fiabilité et calcule
    l'espérance en R de chacune. Recommande le seuil = plus basse tranche
    à espérance positive, à condition d'avoir assez d'échantillons.
    """
    cal = Calibration()
    usable = [t for t in closed_trades
              if getattr(t, "is_closed", False) and t.initial_risk > 0]

    for label, lo, hi in BANDS:
        rs = [t.r_multiple for t in usable if lo <= t.signal_confidence < hi]
        exp = round(sum(rs) / len(rs), 2) if rs else 0.0
        cal.bands.append((label, len(rs), exp))

    # Seuil recommandé : la plus basse tranche FIABLE (assez d'échantillons)
    # et rentable. Sinon, pas de seuil (prudence).
    for label, lo, hi in BANDS:
        n = next(b[1] for b in cal.bands if b[0] == label)
        exp = next(b[2] for b in cal.bands if b[0] == label)
        if n >= min_samples_per_band and exp > 0:
            cal.recommended_min_confidence = lo
            cal.enough_data = True
            break

    if not cal.enough_data:
        cal.note = ("Accumulez plus de sessions réelles : la calibration ne devient "
                    "fiable qu'avec assez de trades par tranche.")
    return cal
