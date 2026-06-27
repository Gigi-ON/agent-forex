"""
Couche de signaux techniques.

RÈGLE D'OR : ce module PROPOSE, il ne DÉCIDE pas. Il sort des objets
TradeProposal qui doivent OBLIGATOIREMENT passer par le RiskManager avant
toute exécution. Aucun signal n'est un ordre.

Stratégie de départ : suivi de tendance prudent.
  - Tendance donnée par deux EMA (rapide vs lente).
  - RSI utilisé comme FILTRE : on n'achète pas en zone surachetée, on ne
    vend pas en zone survendue. Ça évite de courir après un mouvement épuisé.
  - Le stop-loss est placé à partir de l'ATR (volatilité réelle), pas à une
    distance arbitraire. Le take-profit en découle pour garantir le ratio
    gain/risque visé.

Ce n'est pas une stratégie « gagnante » magique — ça n'existe pas. C'est un
cadre de décision reproductible, testable, et compatible avec la gestion du
risque. La valeur vient de la discipline, pas de la prédiction.
"""

from dataclasses import dataclass

from indicators import atr, ema, rsi
from risk_manager import TradeProposal


@dataclass
class Signal:
    """Résultat de l'évaluation d'un instrument à un instant donné."""
    instrument: str
    proposal: TradeProposal | None   # None = pas de trade
    confidence: float                # indicateur grossier 0..1, jamais une promesse
    notes: list                      # explications (trend, rsi, raison du no-trade)


class SignalEngine:
    def __init__(
        self,
        ema_fast: int = 20,
        ema_slow: int = 50,
        rsi_period: int = 14,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,   # stop à 2x l'ATR (marge anti-bruit)
        rr_target: float = 2.0,       # objectif = 2x le risque
        rsi_buy_zone=(50, 70),        # acheter seulement dans cette zone RSI
        rsi_sell_zone=(30, 50),
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.rr_target = rr_target
        self.rsi_buy_zone = rsi_buy_zone
        self.rsi_sell_zone = rsi_sell_zone

    def evaluate(self, instrument: str, candles: list) -> Signal:
        notes = []
        closes = [c["c"] for c in candles]

        # Données suffisantes ?
        need = self.ema_slow + self.rsi_period + 2
        if len(closes) < need:
            return Signal(instrument, None, 0.0,
                          [f"Pas assez de bougies ({len(closes)}/{need})."])

        ef = ema(closes, self.ema_fast)
        es = ema(closes, self.ema_slow)
        rsis = rsi(closes, self.rsi_period)
        atr_cur, _ = atr(candles, self.atr_period)
        price = closes[-1]

        if atr_cur <= 0:
            return Signal(instrument, None, 0.0, ["ATR nul : volatilité illisible."])

        # Tendance et croisement
        uptrend = ef[-1] > es[-1]
        downtrend = ef[-1] < es[-1]
        crossed_up = ef[-2] <= es[-2] and ef[-1] > es[-1]
        crossed_down = ef[-2] >= es[-2] and ef[-1] < es[-1]
        last_rsi = rsis[-1]
        notes.append(f"EMA{self.ema_fast}={ef[-1]:.5f} vs EMA{self.ema_slow}={es[-1]:.5f}")
        notes.append(f"RSI={last_rsi:.1f}")

        side = None
        lo_b, hi_b = self.rsi_buy_zone
        lo_s, hi_s = self.rsi_sell_zone

        if uptrend and lo_b < last_rsi < hi_b:
            side = "buy"
            notes.append("Tendance haussière + momentum sain.")
            if crossed_up:
                notes.append("Croisement EMA haussier récent (renforce le signal).")
        elif downtrend and lo_s < last_rsi < hi_s:
            side = "sell"
            notes.append("Tendance baissière + momentum sain.")
            if crossed_down:
                notes.append("Croisement EMA baissier récent (renforce le signal).")
        else:
            if uptrend and last_rsi >= hi_b:
                notes.append("Haussier mais RSI suracheté : on n'entre pas (trop tard).")
            elif downtrend and last_rsi <= lo_s:
                notes.append("Baissier mais RSI survendu : on n'entre pas (trop tard).")
            else:
                notes.append("Pas d'alignement tendance/momentum : pas de trade.")
            return Signal(instrument, None, 0.0, notes)

        # Stop et objectif dérivés de l'ATR
        stop_dist = self.atr_stop_mult * atr_cur
        if side == "buy":
            stop = price - stop_dist
            take = price + stop_dist * self.rr_target
        else:
            stop = price + stop_dist
            take = price - stop_dist * self.rr_target

        proposal = TradeProposal(
            instrument=instrument,
            side=side,
            entry_price=round(price, 5),
            stop_loss=round(stop, 5),
            take_profit=round(take, 5),
        )

        # Confiance : heuristique simple, bornée. Le croisement frais ajoute
        # un peu, un RSI bien centré ajoute un peu. Ça n'est PAS une proba.
        confidence = 0.5
        if (side == "buy" and crossed_up) or (side == "sell" and crossed_down):
            confidence += 0.2
        centred = 1.0 - abs(last_rsi - 60) / 20 if side == "buy" \
            else 1.0 - abs(last_rsi - 40) / 20
        confidence += 0.3 * max(0.0, min(1.0, centred))
        confidence = round(min(1.0, confidence), 2)

        return Signal(instrument, proposal, confidence, notes)
