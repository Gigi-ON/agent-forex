"""
Journal de trades + post-mortem — la boucle d'apprentissage du système.

Inspiré de la démarche de claude-trading-skills (trader-memory-core +
signal-postmortem), adapté au forex et à notre architecture.

Idée centrale : un système qui trade sans rien enregistrer ne peut pas
apprendre. Ici, chaque trade clôturé devient une ligne durable, et le
post-mortem en tire des métriques HONNÊTES :

  - R-multiple : gain/perte exprimé en multiples du risque initial.
    C'est LA métrique de pro : un trade qui gagne 2× ce qu'il risquait = +2R.
  - Espérance (expectancy) : R moyen par trade. > 0 = système viable.
    Plus parlant que le « taux de réussite » : on peut gagner 36 % du temps
    et être largement positif si les gains font +2R et les pertes −1R.
  - Écarts de discipline : trade sans stop, perte supérieure au risque prévu
    (slippage/gap), entrée pendant un blackout macro… signalés, pas masqués.
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DB = Path(__file__).parent / "data" / "journal.db"


@dataclass
class Trade:
    pair: str
    side: str                      # "buy" / "sell"
    units: int                     # signé
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: str
    initial_risk: float            # risque prévu, devise du compte (> 0)
    # remplis à la clôture :
    exit_price: float = 0.0
    exit_time: str = ""
    exit_reason: str = ""          # STOP / TP / TIME / MANUAL
    pnl: float = 0.0               # P&L réalisé, devise du compte
    # contexte :
    profile: str = ""
    signal_confidence: float = 0.0
    caution_factor: float = 1.0
    regime: str = ""
    blackout_violation: bool = False
    notes: str = ""
    id: int = 0

    @property
    def is_closed(self):
        return bool(self.exit_time)

    @property
    def r_multiple(self):
        """Gain/perte en multiples du risque initial."""
        if self.initial_risk <= 0:
            return 0.0
        return round(self.pnl / self.initial_risk, 2)

    @property
    def outcome(self):
        if abs(self.pnl) < 1e-9:
            return "BREAKEVEN"
        return "WIN" if self.pnl > 0 else "LOSS"

    def discipline_flags(self):
        """Écarts de discipline détectés sur ce trade."""
        flags = []
        if self.stop_loss == 0 or self.stop_loss == self.entry_price:
            flags.append("Aucun stop-loss défini")
        if self.r_multiple < -1.1:
            flags.append(f"Perte ({self.r_multiple}R) supérieure au risque prévu")
        if self.blackout_violation:
            flags.append("Entrée pendant un blackout macro")
        return flags


class JournalStore:
    """Persistance SQLite des trades."""

    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False : la connexion est partagée entre le thread de
        # tick et les threads des requêtes HTTP (écritures sérialisées par un verrou).
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT, side TEXT, units INTEGER,
                entry_price REAL, stop_loss REAL, take_profit REAL,
                entry_time TEXT, initial_risk REAL,
                exit_price REAL, exit_time TEXT, exit_reason TEXT, pnl REAL,
                profile TEXT, signal_confidence REAL, caution_factor REAL,
                regime TEXT, blackout_violation INTEGER, notes TEXT
            )
        """)
        self.conn.commit()

    def record(self, t: Trade) -> int:
        cur = self.conn.execute("""
            INSERT INTO trades(pair,side,units,entry_price,stop_loss,take_profit,
                entry_time,initial_risk,exit_price,exit_time,exit_reason,pnl,
                profile,signal_confidence,caution_factor,regime,blackout_violation,notes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (t.pair, t.side, t.units, t.entry_price, t.stop_loss, t.take_profit,
              t.entry_time, t.initial_risk, t.exit_price, t.exit_time, t.exit_reason,
              t.pnl, t.profile, t.signal_confidence, t.caution_factor, t.regime,
              int(t.blackout_violation), t.notes))
        self.conn.commit()
        return cur.lastrowid

    def closed_trades(self):
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE exit_time != '' ORDER BY entry_time").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["blackout_violation"] = bool(d["blackout_violation"])
            out.append(Trade(**d))
        return out

    def close(self):
        self.conn.close()


@dataclass
class PostMortem:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    expectancy_R: float = 0.0      # R moyen par trade (LA métrique clé)
    total_R: float = 0.0
    avg_win_R: float = 0.0
    avg_loss_R: float = 0.0
    worst_R: float = 0.0
    profit_factor: float = 0.0
    by_exit: dict = field(default_factory=dict)
    by_pair: dict = field(default_factory=dict)
    discipline: list = field(default_factory=list)

    @property
    def win_rate(self):
        return round(self.wins / self.trades * 100, 1) if self.trades else 0.0

    def summary(self):
        verdict = ("système viable (espérance positive)" if self.expectancy_R > 0
                   else "non viable en l'état (espérance négative)")
        lines = [
            f"Trades clôturés : {self.trades}  ({self.wins} gagnants / {self.losses} perdants, {self.win_rate}%)",
            f"Espérance       : {self.expectancy_R:+.2f}R par trade  →  {verdict}",
            f"Total           : {self.total_R:+.2f}R cumulés",
            f"Gain moyen      : {self.avg_win_R:+.2f}R   |   Perte moyenne : {self.avg_loss_R:+.2f}R",
            f"Pire trade      : {self.worst_R:+.2f}R     |   Profit factor : {self.profit_factor}",
            f"Sorties         : " + ", ".join(f"{k} {v}" for k, v in self.by_exit.items()),
            f"Par paire       : " + ", ".join(f"{k} {v:+.2f}R" for k, v in self.by_pair.items()),
        ]
        if self.discipline:
            lines.append("Écarts de discipline :")
            lines += [f"  ⚠ {d}" for d in self.discipline]
        else:
            lines.append("Discipline : aucun écart détecté ✓")
        return "\n".join(lines)


def analyze(trades) -> PostMortem:
    """Produit le rapport post-mortem à partir d'une liste de Trade clôturés."""
    closed = [t for t in trades if t.is_closed]
    pm = PostMortem(trades=len(closed))
    if not closed:
        return pm

    rs = [t.r_multiple for t in closed]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    pm.wins, pm.losses = len(wins), len(losses)
    pm.total_R = round(sum(rs), 2)
    pm.expectancy_R = round(sum(rs) / len(rs), 2)
    pm.avg_win_R = round(sum(wins) / len(wins), 2) if wins else 0.0
    pm.avg_loss_R = round(sum(losses) / len(losses), 2) if losses else 0.0
    pm.worst_R = round(min(rs), 2)
    gross_win = sum(t.pnl for t in closed if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in closed if t.pnl < 0))
    pm.profit_factor = round(gross_win / gross_loss, 2) if gross_loss else float("inf")

    for t in closed:
        pm.by_exit[t.exit_reason] = pm.by_exit.get(t.exit_reason, 0) + 1
        pm.by_pair[t.pair] = round(pm.by_pair.get(t.pair, 0.0) + t.r_multiple, 2)
        for flag in t.discipline_flags():
            pm.discipline.append(f"{t.pair} {t.entry_time[:10]} — {flag}")
    return pm
