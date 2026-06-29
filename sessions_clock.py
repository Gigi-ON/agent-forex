"""
Horloge des sessions forex & meilleures paires « maintenant ».

Logique 100% déterministe (aucun LLM, aucun appel réseau) : à partir de l'heure
UTC courante, on calcule quelles places sont ouvertes, les chevauchements, un
score de liquidité par paire, un verdict global et le classement des paires.

Référence (Investopedia, « Forex Market Trading Hours », heures EST) :
  - New York 8h–17h | Londres 3h–12h | Tokyo 19h–4h | Sydney 17h–2h (EST)
  - Chevauchement US/Londres (8h–12h EST) = le plus liquide (~58% des trades)
  - Chevauchement Sydney/Tokyo (2h–4h EST) -> EUR/JPY ; Tokyo seul -> USD/JPY
  - 4 majors : EUR/USD, USD/JPY, GBP/USD, USD/CHF

Principe du score : une devise est la plus active quand sa place domestique est
ouverte. score(paire) = (place devise1 ouverte) + (place devise2 ouverte).
  2 = les deux marchés ouverts -> optimal | 1 = un seul -> correct | 0 -> éviter.
Les fenêtres sont ancrées sur le fuseau de chaque ville -> heure d'été gérée
automatiquement, pas de fenêtre UTC figée qui se décale 2× par an.
"""
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
except Exception:                       # py < 3.9 (improbable ici)
    ZoneInfo = None

# Place de marché : (fuseau, heure d'ouverture locale, heure de fermeture locale)
SESSIONS = {
    "Sydney":  ("Australia/Sydney", 8, 17),
    "Tokyo":   ("Asia/Tokyo",       9, 18),
    "Londres": ("Europe/London",    8, 17),
    "New York": ("America/New_York", 8, 17),
}

# Rattachement devise -> place domestique
MARKET_OF = {
    "EUR": "Londres", "GBP": "Londres", "CHF": "Londres",
    "SEK": "Londres", "NOK": "Londres", "ZAR": "Londres",
    "USD": "New York", "CAD": "New York", "MXN": "New York",
    "JPY": "Tokyo", "SGD": "Tokyo", "HKD": "Tokyo", "CNH": "Tokyo",
    "AUD": "Sydney", "NZD": "Sydney",
}

# Chevauchements nommés (du plus liquide au moins) + paire vedette (article)
OVERLAPS = [
    (("Londres", "New York"), "Londres–New York", "EUR/USD, GBP/USD", "Le créneau le plus liquide (~58% des échanges)."),
    (("Sydney", "Tokyo"),     "Sydney–Tokyo",     "EUR/JPY, AUD/JPY", "Volatilité modérée, paires JPY actives."),
    (("Londres", "Tokyo"),    "Londres–Tokyo",    "EUR/JPY",          "Bref chevauchement, mouvements limités."),
]

# Priorité de liquidité pour départager à score égal (majors en tête).
# Sert AUSSI d'univers recommandable : on n'affiche jamais d'exotique à spread
# large (EUR/NOK, USD/MXN, …) dans « meilleures paires maintenant ».
LIQ_RANK = ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF", "AUD/USD", "USD/CAD",
            "NZD/USD", "EUR/JPY", "EUR/GBP", "GBP/JPY", "EUR/CHF", "AUD/JPY",
            "GBP/CHF", "GBP/JPY", "CAD/JPY", "NZD/JPY", "EUR/AUD", "EUR/CAD",
            "GBP/AUD", "AUD/NZD"]
LIQUID = set(LIQ_RANK)


def _now_utc(now=None):
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _is_open(session, now_utc):
    """Place ouverte = jour de semaine local (lun–ven) et heure dans la fenêtre."""
    tzname, oh, ch = SESSIONS[session]
    if ZoneInfo is None:
        return False
    loc = now_utc.astimezone(ZoneInfo(tzname))
    if loc.weekday() >= 5:              # samedi/dimanche locaux
        return False
    return oh <= loc.hour < ch


def _forex_closed_weekend(now_utc):
    """Forex fermé du vendredi 22:00 UTC au dimanche 22:00 UTC (aligné moteur)."""
    wd, h = now_utc.weekday(), now_utc.hour       # lun=0 … dim=6
    if wd == 5:                                   # samedi
        return True
    if wd == 4 and h >= 22:                       # vendredi soir
        return True
    if wd == 6 and h < 22:                        # dimanche avant réouverture
        return True
    return False


def _split(pair):
    p = pair.replace("_", "/")
    if "/" not in p:
        return None, None
    a, b = p.split("/", 1)
    return a.upper(), b.upper()


def open_sessions(now_utc):
    return [s for s in SESSIONS if _is_open(s, now_utc)]


def active_overlaps(open_set):
    s = set(open_set)
    return [o for o in OVERLAPS if set(o[0]).issubset(s)]


def score_pair(pair, open_set):
    a, b = _split(pair)
    if not a or not b:
        return 0
    ma, mb = MARKET_OF.get(a), MARKET_OF.get(b)
    return (1 if ma in open_set else 0) + (1 if mb in open_set else 0)


def rank_pairs(pairs, open_set, top=5):
    scored = []
    for p in pairs:
        disp = p.replace("_", "/").upper()
        if disp not in LIQUID:          # pas d'exotique à spread large
            continue
        sc = score_pair(p, open_set)
        if sc <= 0:
            continue
        scored.append((sc, disp, p))
    # tri : score desc, puis liquidité (rang faible d'abord)
    scored.sort(key=lambda t: (-t[0], LIQ_RANK.index(t[1]) if t[1] in LIQ_RANK else 999))
    out = []
    for sc, disp, raw in scored[:top]:
        out.append({"pair": disp,
                    "oanda": raw.replace("/", "_").upper(),
                    "score": sc,
                    "tier": "optimal" if sc >= 2 else "correct"})
    return out


def verdict(open_set, overlaps, weekend):
    if weekend:
        return "Fermé", "Marché forex fermé (week-end)."
    names = set(open_set)
    if {"Londres", "New York"}.issubset(names):
        return "Idéal", "Chevauchement Londres–New York : liquidité maximale."
    if overlaps:
        return "Actif", overlaps[0][1] + " : " + overlaps[0][3]
    if "Londres" in names or "New York" in names:
        return "Actif", "Une place majeure ouverte — fourchettes correctes."
    if names:
        return "Calme", "Session asiatique seule — fourchettes plus serrées."
    return "Calme", "Transition entre sessions — faible volume."


def snapshot(pairs=None, now=None):
    """Photo complète prête à servir au front."""
    if pairs is None:
        try:
            import config
            pairs = list(config.FOREX_PRIORITY)
        except Exception:
            pairs = ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF", "AUD/USD",
                     "USD/CAD", "NZD/USD", "EUR/JPY"]
    nu = _now_utc(now)
    open_set = open_sessions(nu)
    weekend = _forex_closed_weekend(nu)
    overlaps = active_overlaps(open_set)
    verd, why = verdict(open_set, overlaps, weekend)
    top = [] if weekend else rank_pairs(pairs, open_set)
    featured = overlaps[0][2] if overlaps else (top[0]["pair"] if top else None)
    return {
        "utc": nu.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verd,
        "reason": why,
        "weekend": weekend,
        "sessions": [{"name": s,
                      "open": (not weekend) and _is_open(s, nu)} for s in SESSIONS],
        "overlaps": [{"name": o[1], "pairs": o[2], "note": o[3]} for o in overlaps] if not weekend else [],
        "featured": featured,
        "top_pairs": top,
        "crypto_note": "Crypto 24/7 — volume plus élevé aux heures EU/US.",
    }


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), indent=2, ensure_ascii=False))
