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


# ============================ Phase 1 — analyse avancée ============================

def adx(candles, period: int = 14):
    """
    ADX (Average Directional Index, lissage de Wilder). Renvoie la dernière
    valeur (0..100). Mesure la FORCE de tendance, pas son sens :
      - ADX bas (< ~20) = pas de tendance (range) -> éviter le suivi de tendance.
      - ADX élevé = tendance établie -> contexte favorable au trend-following.
    """
    n = len(candles)
    if n < 2 * period + 1:
        return 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = candles[i]["h"] - candles[i - 1]["h"]
        dn = candles[i - 1]["l"] - candles[i]["l"]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    def _wilder(x):
        s = sum(x[:period])
        out = [s]
        for v in x[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    if len(trs) < period:
        return 0.0
    atr_s, pdm_s, mdm_s = _wilder(trs), _wilder(plus_dm), _wilder(minus_dm)
    dxs = []
    for a, p, m in zip(atr_s, pdm_s, mdm_s):
        if a <= 0:
            dxs.append(0.0); continue
        pdi, mdi = 100 * p / a, 100 * m / a
        denom = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / denom if denom else 0.0)
    if not dxs:
        return 0.0
    if len(dxs) < period:
        return dxs[-1]
    val = sum(dxs[:period]) / period
    for v in dxs[period:]:
        val = (val * (period - 1) + v) / period
    return val


def recent_swing_low(candles, lookback: int = 10):
    """Plus bas des `lookback` dernières bougies (support récent)."""
    seg = candles[-lookback:]
    return min((c["l"] for c in seg), default=None)


def recent_swing_high(candles, lookback: int = 10):
    """Plus haut des `lookback` dernières bougies (résistance récente)."""
    seg = candles[-lookback:]
    return max((c["h"] for c in seg), default=None)


def resample(candles, k: int):
    """
    Agrège les bougies par blocs de `k` (ex : M15 -> H1 avec k=4) pour lire un
    horizon supérieur. Aligné sur la FIN : la dernière bougie HTF inclut les
    bougies les plus récentes ; le reliquat le plus ancien est ignoré.
    """
    if k <= 1:
        return list(candles)
    n = len(candles)
    out, start = [], n % k
    for i in range(start, n, k):
        block = candles[i:i + k]
        if len(block) < k:
            continue
        out.append({"o": block[0]["o"],
                    "h": max(b["h"] for b in block),
                    "l": min(b["l"] for b in block),
                    "c": block[-1]["c"]})
    return out
