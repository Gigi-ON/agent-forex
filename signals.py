"""
Couche de signaux techniques — Phase 1 (qualité « trader aguerri »).

RÈGLE D'OR inchangée : ce module PROPOSE, il ne DÉCIDE pas. Il sort des
TradeProposal qui passent OBLIGATOIREMENT par le RiskManager et le superviseur.

Cadre Phase 1 (ce que font les traders qui durent) :
  1. FILTRE DE RÉGIME (ADX) : on ne suit la tendance que si le marché TEND
     vraiment. En range (ADX faible) -> pas de trade (cause n°1 des faux signaux).
  2. CONFLUENCE HORIZON SUPÉRIEUR : l'entrée M15 doit s'aligner sur la tendance
     H1 (bougies ré-agrégées). On ne rame pas à contre-courant du grand bateau.
  3. ENTRÉE SUR REPLI : on n'entre pas quand le prix s'est déjà envolé loin de
     l'EMA (on chasse) — on attend qu'il revienne près de la moyenne.
  4. STOP STRUCTUREL : stop sous le dernier swing (+ tampon ATR), borné, au lieu
     d'une distance aveugle. L'objectif garde le ratio gain/risque visé.
  5. CONFIANCE RECALIBRÉE : agrège les confluences (régime, HTF, repli, momentum)
     au lieu d'une heuristique arbitraire. Toujours bornée, jamais une promesse.

La valeur vient de la discipline et du filtrage, pas de la prédiction.
"""

from dataclasses import dataclass

from indicators import atr, ema, rsi, adx, resample, recent_swing_low, recent_swing_high
from risk_manager import TradeProposal

try:
    from config import PHASE1 as _P1
except Exception:
    _P1 = {}


@dataclass
class Signal:
    instrument: str
    proposal: TradeProposal | None
    confidence: float
    notes: list


class SignalEngine:
    def __init__(
        self,
        ema_fast: int = 20,
        ema_slow: int = 50,
        rsi_period: int = 14,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        rr_target: float = 2.0,
        rsi_buy_zone=(50, 70),
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
        # Réglages Phase 1 (overridables via config.PHASE1)
        self.adx_period = _P1.get("adx_period", 14)
        self.adx_min = _P1.get("adx_min", 20.0)
        self.htf_factor = _P1.get("htf_factor", 4)
        self.htf_ema_fast = _P1.get("htf_ema_fast", 20)
        self.htf_ema_slow = _P1.get("htf_ema_slow", 50)
        self.pullback_atr_mult = _P1.get("pullback_atr_mult", 1.5)
        self.swing_lookback = _P1.get("swing_lookback", 10)
        self.swing_buffer_atr = _P1.get("swing_buffer_atr", 0.5)
        self.stop_min_atr = _P1.get("stop_min_atr", 2.0)
        self.stop_max_atr = _P1.get("stop_max_atr", 4.0)

    def _htf_trend(self, candles):
        """Tendance de l'horizon supérieur. Renvoie 'up'/'down'/None (indispo)."""
        htf = resample(candles, self.htf_factor)
        closes = [c["c"] for c in htf]
        if len(closes) < self.htf_ema_slow + 1:
            return None
        ef = ema(closes, self.htf_ema_fast)
        es = ema(closes, self.htf_ema_slow)
        return "up" if ef[-1] > es[-1] else "down"

    def evaluate(self, instrument: str, candles: list) -> Signal:
        notes = []
        closes = [c["c"] for c in candles]
        need = max(self.ema_slow, 2 * self.adx_period) + self.rsi_period + 2
        if len(closes) < need:
            return Signal(instrument, None, 0.0,
                          [f"Pas assez de bougies ({len(closes)}/{need})."])

        ef = ema(closes, self.ema_fast)
        es = ema(closes, self.ema_slow)
        rsis = rsi(closes, self.rsi_period)
        atr_cur, _ = atr(candles, self.atr_period)
        adx_val = adx(candles, self.adx_period)
        price = closes[-1]
        if atr_cur <= 0:
            return Signal(instrument, None, 0.0, ["ATR nul : volatilité illisible."])

        uptrend = ef[-1] > es[-1]
        downtrend = ef[-1] < es[-1]
        crossed_up = ef[-2] <= es[-2] and ef[-1] > es[-1]
        crossed_down = ef[-2] >= es[-2] and ef[-1] < es[-1]
        last_rsi = rsis[-1]
        notes.append(f"EMA{self.ema_fast}={ef[-1]:.5f} vs EMA{self.ema_slow}={es[-1]:.5f}")
        notes.append(f"RSI={last_rsi:.1f} · ADX={adx_val:.1f}")

        # 1) FILTRE DE RÉGIME : pas de suivi de tendance en range.
        if adx_val < self.adx_min:
            notes.append(f"ADX {adx_val:.1f} < {self.adx_min} : marché sans tendance (range) — pas de trade.")
            return Signal(instrument, None, 0.0, notes)

        # 2) Direction de base : tendance + momentum sain (RSI dans la zone).
        lo_b, hi_b = self.rsi_buy_zone
        lo_s, hi_s = self.rsi_sell_zone
        side = None
        if uptrend and lo_b < last_rsi < hi_b:
            side = "buy"; notes.append("Tendance haussière + momentum sain.")
        elif downtrend and lo_s < last_rsi < hi_s:
            side = "sell"; notes.append("Tendance baissière + momentum sain.")
        else:
            if uptrend and last_rsi >= hi_b:
                notes.append("Haussier mais RSI suracheté : on n'entre pas (trop tard).")
            elif downtrend and last_rsi <= lo_s:
                notes.append("Baissier mais RSI survendu : on n'entre pas (trop tard).")
            else:
                notes.append("Pas d'alignement tendance/momentum : pas de trade.")
            return Signal(instrument, None, 0.0, notes)

        # 3) CONFLUENCE HORIZON SUPÉRIEUR.
        htf = self._htf_trend(candles)
        if htf is not None:
            if (side == "buy" and htf != "up") or (side == "sell" and htf != "down"):
                notes.append(f"Horizon supérieur {htf} non aligné avec {side} : pas de trade.")
                return Signal(instrument, None, 0.0, notes)
            notes.append(f"Horizon supérieur {htf} aligné.")
        else:
            notes.append("Horizon supérieur indisponible (historique court) : filtre ignoré.")

        # 4) ENTRÉE SUR REPLI : refuser si trop étendu par rapport à l'EMA rapide.
        extension = (price - ef[-1]) if side == "buy" else (ef[-1] - price)
        if extension > self.pullback_atr_mult * atr_cur:
            notes.append(f"Prix trop étendu ({extension / atr_cur:.1f} ATR de l'EMA) : on attend un repli.")
            return Signal(instrument, None, 0.0, notes)
        notes.append(f"Proche de l'EMA ({extension / atr_cur:.1f} ATR) : entrée sur repli OK.")

        # 5) STOP STRUCTUREL borné, puis objectif au ratio visé.
        if side == "buy":
            swing = recent_swing_low(candles, self.swing_lookback)
            cand = (swing - self.swing_buffer_atr * atr_cur) if swing is not None else price - self.atr_stop_mult * atr_cur
            dist = price - cand
        else:
            swing = recent_swing_high(candles, self.swing_lookback)
            cand = (swing + self.swing_buffer_atr * atr_cur) if swing is not None else price + self.atr_stop_mult * atr_cur
            dist = cand - price
        # bornage de la distance de stop
        dist = max(self.stop_min_atr * atr_cur, min(self.stop_max_atr * atr_cur, dist))
        if side == "buy":
            stop = price - dist; take = price + dist * self.rr_target
        else:
            stop = price + dist; take = price - dist * self.rr_target

        proposal = TradeProposal(
            instrument=instrument, side=side,
            entry_price=round(price, 5), stop_loss=round(stop, 5),
            take_profit=round(take, 5))

        # 6) CONFIANCE = agrégat de confluences (bornée, jamais une probabilité).
        adx_strength = max(0.0, min(1.0, (adx_val - self.adx_min) / 20.0))
        fresh = (side == "buy" and crossed_up) or (side == "sell" and crossed_down)
        htf_score = 1.0 if (htf is not None) else 0.5
        pullback_q = max(0.0, 1.0 - max(0.0, extension) / (self.pullback_atr_mult * atr_cur))
        centred = (1.0 - abs(last_rsi - 60) / 20) if side == "buy" else (1.0 - abs(last_rsi - 40) / 20)
        centred = max(0.0, min(1.0, centred))
        confidence = (0.35
                      + 0.15 * (1.0 if fresh else 0.0)
                      + 0.15 * adx_strength
                      + 0.15 * htf_score
                      + 0.10 * pullback_q
                      + 0.10 * centred)
        confidence = round(min(1.0, confidence), 2)
        if fresh:
            notes.append("Croisement EMA frais (renforce).")

        return Signal(instrument, proposal, confidence, notes)
