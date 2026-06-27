"""
Backtester événementiel (intraday).

Rejoue l'historique bougie par bougie, EN UTILISANT les vraies couches du
projet : SignalEngine pour proposer, RiskManager pour dimensionner. Aucune
logique de décision dupliquée ici — on teste le système réel.

Hypothèses (volontairement prudentes) :
  - entrée à la clôture de la bougie qui déclenche le signal,
  - stop et take-profit vérifiés sur le high/low des bougies suivantes,
  - si une bougie touche stop ET tp, on suppose le STOP d'abord (pessimiste),
  - coût de spread appliqué à l'aller-retour,
  - une seule position à la fois (simple et lisible).

Ce n'est pas un simulateur tick par tick : les résultats sont indicatifs,
pas une promesse. Un backtest flatteur ne garantit rien en réel.
"""

from dataclasses import dataclass, field

from signals import SignalEngine
from risk_manager import RiskManager, Profile
from indicators import atr
from journal import Trade


@dataclass
class BacktestResult:
    start_equity: float
    end_equity: float
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    max_drawdown_pct: float = 0.0
    equity_curve: list = field(default_factory=list)
    trade_log: list = field(default_factory=list)   # liste de Trade (journal)

    @property
    def return_pct(self):
        if self.start_equity <= 0:
            return 0.0
        return round((self.end_equity / self.start_equity - 1) * 100, 2)

    @property
    def win_rate(self):
        return round(self.wins / self.trades * 100, 1) if self.trades else 0.0

    @property
    def profit_factor(self):
        return round(self.gross_win / self.gross_loss, 2) if self.gross_loss else float("inf")

    def summary(self):
        return (
            f"Trades: {self.trades} | Gagnants: {self.wins} | Perdants: {self.losses}\n"
            f"Taux de réussite: {self.win_rate}% | Profit factor: {self.profit_factor}\n"
            f"Rendement: {self.return_pct}% | Drawdown max: {self.max_drawdown_pct}%\n"
            f"Capital: {self.start_equity:.0f} -> {self.end_equity:.2f}"
        )


class Backtester:
    def __init__(
        self,
        engine: SignalEngine = None,
        profile: Profile = Profile.DOUX,
        spread_price: float = 0.00008,   # ~0.8 pip sur EUR/USD
        slippage_price: float = 0.00004, # glissement réaliste à l'exécution
        friction_mult: float = 1.0,      # 1.5–2.0 pour un test pessimiste
        quote_to_account: float = 1.36,  # conversions figées pour la démo
        base_to_account: float = 1.47,
        warmup: int = 80,
    ):
        self.engine = engine or SignalEngine()
        self.profile = profile
        # friction totale par trade = (spread + slippage) × multiplicateur
        self.friction = (spread_price + slippage_price) * friction_mult
        self.friction_mult = friction_mult
        self.q2a = quote_to_account
        self.b2a = base_to_account
        self.warmup = warmup

    def run(self, pair, candles, start_equity=5000.0) -> BacktestResult:
        rm = RiskManager(profile=self.profile)
        equity = start_equity
        res = BacktestResult(start_equity=start_equity, end_equity=equity)
        peak = equity
        position = None  # dict: side, units, entry, stop, tp

        for i in range(self.warmup, len(candles)):
            bar = candles[i]

            # 1) Gérer une position ouverte sur la bougie courante.
            if position:
                exit_price = None
                if position["side"] == "buy":
                    if bar["l"] <= position["stop"]:
                        exit_price = position["stop"]
                    elif bar["h"] >= position["tp"]:
                        exit_price = position["tp"]
                else:  # sell
                    if bar["h"] >= position["stop"]:
                        exit_price = position["stop"]
                    elif bar["l"] <= position["tp"]:
                        exit_price = position["tp"]

                if exit_price is not None:
                    move = exit_price - position["entry"]
                    pnl_quote = position["units"] * move            # units signé
                    friction_cost = abs(position["units"]) * self.friction
                    pnl_account = (pnl_quote - friction_cost) * self.q2a
                    equity += pnl_account
                    rm.register_pnl(pnl_account)

                    res.trades += 1
                    if pnl_account >= 0:
                        res.wins += 1
                        res.gross_win += pnl_account
                    else:
                        res.losses += 1
                        res.gross_loss += abs(pnl_account)

                    # Journal : enregistrer le trade avec son contexte.
                    reason = "TP" if exit_price == position["tp"] else "STOP"
                    res.trade_log.append(Trade(
                        pair=pair, side=position["side"], units=position["units"],
                        entry_price=position["entry"], stop_loss=position["stop"],
                        take_profit=position["tp"], entry_time=position["entry_time"],
                        initial_risk=position["init_risk"],
                        exit_price=exit_price, exit_time=bar.get("time", str(i)),
                        exit_reason=reason, pnl=round(pnl_account, 2),
                        profile=self.profile.value, signal_confidence=position["conf"],
                    ))
                    position = None
                    rm.set_open_positions(0)

            # 2) Suivi de l'equity et du drawdown.
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            res.max_drawdown_pct = max(res.max_drawdown_pct, round(dd, 2))
            res.equity_curve.append(round(equity, 2))

            # 3) Chercher une nouvelle entrée (si pas de position).
            if position is None:
                window = candles[: i + 1]
                sig = self.engine.evaluate(pair, window)
                if sig.proposal:
                    a_cur, a_avg = atr(window, 14)
                    sized = rm.size_position(
                        proposal=sig.proposal,
                        equity_account_ccy=equity,
                        quote_to_account_rate=self.q2a,
                        base_to_account_rate=self.b2a,
                        current_atr=a_cur, average_atr=a_avg,
                    )
                    if sized.accepted and sized.units != 0:
                        # risque initial prévu (devise du compte) = base du R-multiple
                        init_risk = abs(sized.units) * abs(
                            sig.proposal.entry_price - sig.proposal.stop_loss) * self.q2a
                        position = {
                            "side": sig.proposal.side,
                            "units": sized.units,
                            "entry": sig.proposal.entry_price,
                            "stop": sig.proposal.stop_loss,
                            "tp": sig.proposal.take_profit,
                            "entry_time": bar.get("time", str(i)),
                            "init_risk": init_risk,
                            "conf": sig.confidence,
                        }
                        rm.set_open_positions(1)

        res.end_equity = round(equity, 2)
        return res


def robustness_report(pair, candles, profile=Profile.DOUX, start_equity=5000.0):
    """
    Méthode 'backtest-expert' : on ne cherche pas le meilleur chiffre, on
    cherche ce qui SURVIT quand on durcit les hypothèses.

      1. Friction croissante (×1, ×1,5, ×2) — un edge fragile s'effondre vite.
      2. Découpage en 3 segments — un edge réel est un minimum constant,
         pas porté par une seule période chanceuse.

    Renvoie un texte de synthèse.
    """
    from journal import analyze

    lines = ["Test de robustesse — l'edge survit-il quand on durcit ?", ""]

    # 1) Friction croissante
    lines.append("Friction croissante (spread+slippage) :")
    for mult in (1.0, 1.5, 2.0):
        bt = Backtester(profile=profile, friction_mult=mult)
        r = bt.run(pair, candles, start_equity)
        pm = analyze(r.trade_log)
        verdict = "survit" if pm.expectancy_R > 0 else "s'effondre"
        lines.append(f"  ×{mult:<4} → espérance {pm.expectancy_R:+.2f}R "
                     f"sur {pm.trades} trades — {verdict}")

    # 2) Cohérence par segments
    lines.append("\nCohérence par tiers de l'historique :")
    third = len(candles) // 3
    for k in range(3):
        seg = candles[k * third:(k + 1) * third]
        bt = Backtester(profile=profile)
        r = bt.run(pair, seg, start_equity)
        pm = analyze(r.trade_log)
        lines.append(f"  segment {k+1} → espérance {pm.expectancy_R:+.2f}R "
                     f"sur {pm.trades} trades")

    lines.append("\nLecture : un edge réel reste positif sous ×2 friction ET "
                 "sur les trois segments. Sinon, c'est probablement du sur-apprentissage.")
    return "\n".join(lines)
