"""
Couche actualité / macro.

RÈGLE D'OR (identique à signals.py, mais encore plus stricte) :
cette couche ne produit JAMAIS de signal d'achat/vente. L'actualité et le
sentiment ne prédisent pas la direction de façon fiable. Cette couche sert
uniquement à MODULER LA PRUDENCE :

  - "blackout" : interdire un NOUVEAU trade sur une devise autour d'un
    événement à fort impact (ex : décision BCE, emploi US, taux BoC).
  - "caution_factor" dans [0.4, 1.0] : réduire la taille quand l'incertitude
    est élevée (densité d'événements, divergence des sources).

Elle ne peut que RÉDUIRE l'exposition, jamais l'augmenter.

Modèle de confiance :
  - Les sources OFFICIELLES (banques centrales, FMI, Banque mondiale, FRED,
    bourses, agences de notation) font foi.
  - Les médias sérieux suivent.
  - Le social a un poids FAIBLE par défaut. On peut suivre son taux de
    confirmation par les sources officielles pour ajuster ce poids — borné,
    jamais au-dessus d'un média sérieux.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum


class Trust(IntEnum):
    """Niveau de confiance d'une source. Plus haut = plus fiable."""
    SOCIAL_TREND = 1      # Reddit/Bluesky : chatter non vérifié
    VERIFIED_SOCIAL = 2   # comptes vérifiés d'orgs/médias officiels
    MAJOR_MEDIA = 4       # Reuters, Bloomberg, AP, FT, Les Affaires...
    OFFICIAL = 5          # banques centrales, FMI, Banque mondiale, FRED,
                          # bourses, agences de notation (via news)


# Poids [0..1] appliqué à l'influence d'une source sur la prudence.
TRUST_WEIGHT = {
    Trust.SOCIAL_TREND: 0.15,
    Trust.VERIFIED_SOCIAL: 0.40,
    Trust.MAJOR_MEDIA: 0.80,
    Trust.OFFICIAL: 1.00,
}


@dataclass
class NewsItem:
    source: str
    trust: Trust
    title: str
    published: datetime                 # timezone-aware (UTC)
    region: str = "global"              # north_america / south_america / africa / europe / global
    currencies: list = field(default_factory=list)   # ex : ["EUR", "USD"]
    impact: str = "low"                 # low / medium / high
    is_event: bool = False              # True = événement programmé (calendrier)
    event_time: datetime | None = None  # heure prévue de l'événement
    url: str = ""


# ---------------------------------------------------------------------------
# Registre des SOURCES suggérées par région (données seulement).
# Les flux RSS et API officiels sont gratuits ; le social est limité.
# Couverture : Amérique du Nord, Amérique du Sud, Afrique + global/Europe.
# Note pour EUR/USD et EUR/CAD : les pilotes DIRECTS sont la BCE, la Fed et
# la Banque du Canada. L'Amérique du Sud et l'Afrique comptent surtout comme
# CONTEXTE de risque global (matières premières, risk-on/risk-off), pas comme
# moteurs directs des paires tradées.
# ---------------------------------------------------------------------------
SOURCE_REGISTRY = {
    "official": [
        # nom, région, devises clés. Accès : API/RSS gratuits.
        ("BCE / ECB", "europe", ["EUR"]),
        ("US Federal Reserve / FRED", "north_america", ["USD"]),
        ("Banque du Canada", "north_america", ["CAD"]),
        ("FMI / IMF", "global", []),
        ("Banque mondiale / World Bank", "global", []),
        ("Banco Central do Brasil", "south_america", ["BRL"]),
        ("South African Reserve Bank", "africa", ["ZAR"]),
        ("African Development Bank", "africa", []),
    ],
    "major_media": [
        ("Reuters", "global", []),
        ("Bloomberg", "global", []),
        ("Associated Press", "global", []),
        ("Les Affaires", "north_america", ["CAD"]),
        ("MarketPulse (OANDA)", "global", ["EUR", "USD", "CAD"]),
    ],
    "feasible_social": [
        ("Bluesky (public API, gratuit)", "global", []),
    ],
    "restricted_social": [
        # Conservés pour mémoire, mais coûteux ou fermés en 2026 :
        ("X / Twitter — paiement à l'usage, coûteux", "global", []),
        ("Reddit — gratuit non-commercial, bruité", "global", []),
        ("LinkedIn / Facebook — pas d'API de lecture tierce", "global", []),
    ],
}


class TrustScorer:
    """
    Confronte les éléments sociaux aux sources officielles/médias.
    Un élément social est "corroboré" si une source de confiance >= MAJOR_MEDIA
    parle des mêmes devises dans une fenêtre temporelle proche.

    Renvoie un score [0..1] = part des rumeurs sociales confirmées.
    C'est une heuristique grossière : à utiliser pour pondérer, pas pour
    valider une information au sens journalistique.
    """

    def __init__(self, window_hours: float = 6.0):
        self.window = timedelta(hours=window_hours)

    def corroboration_rate(self, items: list) -> float:
        social = [i for i in items if i.trust <= Trust.VERIFIED_SOCIAL]
        trusted = [i for i in items if i.trust >= Trust.MAJOR_MEDIA]
        if not social:
            return 1.0
        confirmed = 0
        for s in social:
            for t in trusted:
                same_ccy = bool(set(s.currencies) & set(t.currencies)) \
                    or not s.currencies
                close_time = abs((s.published - t.published)) <= self.window
                if same_ccy and close_time:
                    confirmed += 1
                    break
        return round(confirmed / len(social), 2)


@dataclass
class CautionDecision:
    blackout: bool                  # True = pas de NOUVEAU trade maintenant
    caution_factor: float           # [0.4, 1.0] multiplicateur de taille
    reasons: list


class RiskModulator:
    """
    Transforme l'état macro/news en consignes de prudence pour UN instrument.
    Ne renvoie jamais de direction.
    """

    def __init__(
        self,
        pre_event_minutes: int = 60,    # fenêtre AVANT un événement fort
        post_event_minutes: int = 30,   # fenêtre APRÈS
        floor: float = 0.4,             # plancher du facteur de prudence
    ):
        self.pre = timedelta(minutes=pre_event_minutes)
        self.post = timedelta(minutes=post_event_minutes)
        self.floor = floor

    @staticmethod
    def _currencies_of(instrument: str) -> set:
        # "EUR_USD" -> {"EUR", "USD"}
        return set(instrument.split("_"))

    def assess(self, items: list, instrument: str, now: datetime) -> CautionDecision:
        ccy = self._currencies_of(instrument)
        reasons = []
        caution = 1.0
        blackout = False

        for it in items:
            # Ne considérer que ce qui touche les devises de la paire.
            if it.currencies and not (set(it.currencies) & ccy):
                continue

            weight = TRUST_WEIGHT[it.trust]

            # 1) Événement programmé à fort impact -> fenêtre de blackout.
            if it.is_event and it.impact == "high" and it.event_time:
                start = it.event_time - self.pre
                end = it.event_time + self.post
                if start <= now <= end:
                    blackout = True
                    reasons.append(
                        f"Blackout : {it.title} ({'/'.join(it.currencies)}) "
                        f"à {it.event_time:%H:%M} UTC."
                    )
                # même hors fenêtre, un gros événement imminent réduit la taille
                elif now < start and (start - now) <= timedelta(hours=8):
                    caution -= 0.2 * weight
                    reasons.append(
                        f"Événement fort à venir : {it.title} -> prudence accrue."
                    )

            # 2) Actualité à fort impact récente (non programmée) -> prudence.
            elif it.impact == "high" and not it.is_event:
                age = now - it.published
                if age <= timedelta(hours=6):
                    caution -= 0.15 * weight
                    reasons.append(
                        f"News à fort impact récente ({it.source}) -> prudence."
                    )

        caution = max(self.floor, round(caution, 2))
        if not reasons:
            reasons.append("Aucun signal macro contraignant pour cette paire.")
        return CautionDecision(blackout=blackout, caution_factor=caution,
                               reasons=reasons)


# ---------------------------------------------------------------------------
# Adaptateurs de sources (structure prête, réseau requis -> imports paresseux).
# Dans le sandbox hors-ligne ils ne sont pas appelés ; ils montrent où brancher
# les vraies sources. Chacun renvoie une liste de NewsItem.
# ---------------------------------------------------------------------------
class RSSSource:
    """Flux RSS d'un média sérieux ou d'une org officielle (gratuit, légal)."""

    def __init__(self, name, url, trust: Trust, region="global", currencies=None):
        self.name, self.url = name, url
        self.trust, self.region = trust, region
        self.currencies = currencies or []

    def fetch(self):
        import feedparser  # import paresseux : pas requis hors-ligne
        feed = feedparser.parse(self.url)
        items = []
        for e in feed.entries:
            published = datetime.now(timezone.utc)  # à affiner via e.published_parsed
            items.append(NewsItem(
                source=self.name, trust=self.trust, title=e.get("title", ""),
                published=published, region=self.region,
                currencies=self.currencies, url=e.get("link", ""),
            ))
        return items


class EconomicCalendarSource:
    """
    Calendrier économique via API (ex : Finnhub gratuit, Trading Economics).
    Clé requise -> à mettre en variable d'environnement, jamais en dur.
    """

    def __init__(self, api_key_env="FINNHUB_KEY"):
        import os
        self.api_key = os.environ.get(api_key_env, "")

    def fetch(self):
        # Brancher ici l'appel réel ; renvoyer des NewsItem is_event=True
        # avec impact "high"/"medium"/"low", currencies et event_time.
        raise NotImplementedError(
            "Brancher une API de calendrier (Finnhub/Trading Economics) "
            "et définir la clé en variable d'environnement."
        )


class BlueskySource:
    """Recherche publique Bluesky (public.api.bsky.app), gratuite."""

    BASE = "https://public.api.bsky.app"

    def __init__(self, query, region="global", currencies=None):
        self.query = query
        self.region, self.currencies = region, currencies or []

    def fetch(self):
        import requests  # import paresseux
        url = f"{self.BASE}/xrpc/app.bsky.feed.searchPosts"
        r = requests.get(url, params={"q": self.query, "limit": 25}, timeout=10)
        r.raise_for_status()
        items = []
        for post in r.json().get("posts", []):
            items.append(NewsItem(
                source="Bluesky", trust=Trust.SOCIAL_TREND,
                title=post.get("record", {}).get("text", "")[:200],
                published=datetime.now(timezone.utc),
                region=self.region, currencies=self.currencies,
            ))
        return items


class NewsAggregator:
    """Collecte et fusionne les éléments de plusieurs sources."""

    def __init__(self, sources):
        self.sources = sources

    def collect(self):
        items = []
        for s in self.sources:
            try:
                items.extend(s.fetch())
            except Exception as e:
                # une source défaillante ne doit pas faire tomber le reste
                print(f"[news] source {getattr(s, 'name', s)} indisponible : {e}")
        # dédoublonnage grossier par (source, titre)
        seen, deduped = set(), []
        for it in items:
            key = (it.source, it.title)
            if key not in seen:
                seen.add(key)
                deduped.append(it)
        deduped.sort(key=lambda i: i.published, reverse=True)
        return deduped


# Flux RSS de MarketPulse (analyse de marché OANDA : FX, banques centrales, NFP).
MARKETPULSE_FEED = "https://www.marketpulse.com/feed/"


def default_rss_sources():
    """Sources RSS prêtes à l'emploi (gratuites, légales). À enrichir au besoin."""
    return [
        RSSSource("MarketPulse (OANDA)", MARKETPULSE_FEED, Trust.MAJOR_MEDIA,
                  region="global", currencies=["EUR", "USD", "CAD"]),
    ]
