"""
Indicateurs techniques. Volontairement minimal pour l'instant :
on ne cherche pas à « prédire », juste à mesurer la volatilité pour
ajuster prudemment la taille des positions.
"""


def atr(candles, period: int = 14):
    """
    Average True Range sur une liste de bougies [{o,h,l,c}, ...].
    Renvoie (atr_courant, atr_moyen) pour l'ajustement volatilité.
    """
    if len(candles) < period + 1:
        return 0.0, 0.0

    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["h"]
        l = candles[i]["l"]
        prev_close = candles[i - 1]["c"]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)

    # ATR courant = moyenne des "period" derniers true ranges
    atr_current = sum(trs[-period:]) / period
    # ATR moyen = moyenne sur tout l'historique fourni (référence)
    atr_average = sum(trs) / len(trs)
    return atr_current, atr_average


def ema(values, period: int):
    """
    Moyenne mobile exponentielle. Renvoie la série complète (même longueur
    que `values`). Sert à lire la tendance : EMA rapide au-dessus de l'EMA
    lente = tendance haussière, et inversement.
    """
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes, period: int = 14):
    """
    RSI (lissage de Wilder). Renvoie la série des valeurs RSI.
    < 30 = survendu, > 70 = suracheté. On l'utilise ici comme FILTRE
    (éviter d'acheter en suracheté), pas comme signal d'achat magique.

    La série renvoyée est alignée sur les `period` premières bougies
    consommées pour l'amorçage : rsi(closes)[-1] correspond à la dernière
    bougie de `closes`.
    """
    if len(closes) < period + 1:
        return []

    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    out = []
    for i in range(period, len(gains) + 1):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - 100.0 / (1.0 + rs))
    return out
