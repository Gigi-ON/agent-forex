"""
Moteur de gestion du risque — LE module le plus important du projet.

Philosophie :
  - On ne décide JAMAIS d'une taille de position « au feeling ».
  - On part du risque qu'on accepte de perdre (un petit % du capital),
    et de la distance jusqu'au stop-loss. La taille en découle.
  - Les profils (réservé / doux / agressif) ne changent QUE le % risqué
    par trade. Ils ne « prédisent » rien.
  - La volatilité élevée RÉDUIT la taille (prudence), elle ne l'augmente pas.
  - Des plafonds durs (HARD_LIMITS) priment toujours sur tout le reste.

Formule de base du sizing forex :
  Pour une position de N unités de la devise de base (ex : EUR sur EUR_USD),
  un mouvement de prix Δ (exprimé dans la devise de cotation, ex : USD)
  produit un P&L de  N * Δ  dans la devise de cotation.

  Donc si je veux risquer un montant R (en devise de cotation) avec un
  stop situé à une distance d du prix d'entrée :

      N = R / d

  Il reste à convertir R, exprimé au départ dans la devise du COMPTE,
  vers la devise de cotation de la paire.
"""

from dataclasses import dataclass
from enum import Enum

from config import HARD_LIMITS
try:
    from config import PHASE2 as _P2
except Exception:
    _P2 = {}


class Profile(str, Enum):
    """Profils de risque. Chaque valeur = % du capital risqué PAR TRADE."""
    RESERVE = "reserve"     # prudent
    DOUX = "doux"           # intermédiaire
    AGRESSIF = "agressif"   # plus de risque, mais toujours plafonné


# % de capital risqué par trade selon le profil.
PROFILE_RISK_PCT = {
    Profile.RESERVE: 0.5,
    Profile.DOUX: 1.0,
    Profile.AGRESSIF: 1.5,
}


@dataclass
class TradeProposal:
    """Une proposition de trade à évaluer."""
    instrument: str          # ex : "EUR_USD"
    side: str                # "buy" ou "sell"
    entry_price: float       # prix d'entrée
    stop_loss: float         # prix du stop-loss (OBLIGATOIRE)
    take_profit: float       # prix de l'objectif


@dataclass
class SizingResult:
    """Résultat du calcul : combien d'unités, et si le trade est accepté."""
    accepted: bool
    units: int                       # signé : positif = achat, négatif = vente
    risk_amount_account_ccy: float   # montant réellement risqué
    effective_leverage: float
    reward_risk_ratio: float
    reasons: list                    # raisons d'un refus, le cas échéant


class RiskManager:
    """
    Garde-fou central. On lui passe une proposition de trade et il répond :
    taille de position autorisée, ou refus motivé.
    """

    def __init__(self, profile: Profile = Profile.RESERVE):
        self.profile = profile
        # Suivi de la perte du jour pour le coupe-circuit journalier.
        self._daily_pnl = 0.0
        self._open_positions = 0

    # -- état de la journée --------------------------------------------------
    def register_pnl(self, pnl_account_ccy: float):
        """À appeler quand un trade est clôturé, pour suivre la perte du jour."""
        self._daily_pnl += pnl_account_ccy

    def reset_day(self):
        self._daily_pnl = 0.0

    def set_open_positions(self, n: int):
        self._open_positions = n

    def daily_loss_breached(self, equity: float) -> bool:
        """True si la perte du jour dépasse le plafond -> on coupe tout."""
        max_loss = equity * HARD_LIMITS["max_daily_loss_pct"] / 100.0
        return self._daily_pnl <= -max_loss

    # -- ajustement volatilité ----------------------------------------------
    @staticmethod
    def volatility_factor(current_atr: float, average_atr: float) -> float:
        """
        Renvoie un facteur dans [0.4, 1.0] qui RÉDUIT la taille quand la
        volatilité (ATR courant) dépasse sa moyenne. Jamais > 1 :
        on ne sur-expose jamais sur un pic de volatilité.

        C'est ça, l'« aide de l'IA » prudente : moins d'exposition quand
        le marché est agité, pas plus.
        """
        if average_atr <= 0:
            return 1.0
        ratio = current_atr / average_atr
        if ratio <= 1.0:
            return 1.0           # volatilité normale ou basse -> pleine taille
        # volatilité élevée -> on réduit, plancher à 0.4 (40 % de la taille)
        return max(0.4, 1.0 / ratio)

    # -- calcul principal ----------------------------------------------------
    def size_position(
        self,
        proposal: TradeProposal,
        equity_account_ccy: float,
        quote_to_account_rate: float,
        base_to_account_rate: float,
        current_atr: float = 0.0,
        average_atr: float = 0.0,
        external_caution: float = 1.0,
        whole_units: bool = True,
        portfolio_open_risk: float = 0.0,
        portfolio_equity: float = 0.0,
        streak_scale: float = 1.0,
        risk_base: float = 0.0,
    ) -> SizingResult:
        """
        equity_account_ccy     : capital du compte, dans la devise du compte
        quote_to_account_rate  : 1 unité de devise de COTATION = combien en
                                  devise du compte ? (ex EUR_USD, compte CAD :
                                  combien vaut 1 USD en CAD)
        base_to_account_rate   : 1 unité de devise de BASE = combien en devise
                                  du compte ? (sert au calcul du levier)
        current_atr/average_atr: pour l'ajustement volatilité (optionnel)
        """
        reasons = []
        # Base de dimensionnement : capital de référence FIXE si fourni
        # (sizing uniforme), sinon l'équité de la session (ancien comportement).
        base = risk_base if (risk_base and risk_base > 0) else equity_account_ccy

        # 1) Le stop-loss est obligatoire et doit être du bon côté.
        d = abs(proposal.entry_price - proposal.stop_loss)
        if d <= 0:
            return SizingResult(False, 0, 0, 0, 0,
                                ["Stop-loss absent ou égal au prix d'entrée."])

        # 2) Ratio reward/risk : refus si l'objectif est trop proche.
        reward = abs(proposal.take_profit - proposal.entry_price)
        rr = reward / d if d > 0 else 0
        if rr < HARD_LIMITS["min_reward_risk_ratio"]:
            reasons.append(
                f"Ratio gain/risque {rr:.2f} < minimum "
                f"{HARD_LIMITS['min_reward_risk_ratio']}."
            )

        # 3) Plafond du nombre de positions ouvertes.
        if self._open_positions >= HARD_LIMITS["max_open_positions"]:
            reasons.append("Nombre maximum de positions ouvertes atteint.")

        # 4) Coupe-circuit journalier.
        if self.daily_loss_breached(equity_account_ccy):
            reasons.append("Perte journalière maximale atteinte : trading suspendu.")

        # 5) % risqué, plafonné par la limite dure.
        risk_pct = min(
            PROFILE_RISK_PCT[self.profile],
            HARD_LIMITS["max_risk_per_trade_pct"],
        )

        # 6) Ajustement volatilité (réduit la taille si marché agité)
        #    ET prudence externe issue de la couche news/macro.
        #    external_caution est borné dans [0, 1] : il ne peut que RÉDUIRE.
        vol_factor = self.volatility_factor(current_atr, average_atr)
        ext = max(0.0, min(1.0, external_caution))
        risk_amount_account = (
            base * (risk_pct / 100.0) * vol_factor * ext
        )

        # 6bis) PHASE 2 — DE-RISKING : réduit la taille après des pertes en série.
        risk_amount_account *= max(0.0, min(1.0, streak_scale))

        # 6ter) PHASE 2 — HEAT GLOBAL : la somme des risques ouverts ne doit pas
        # dépasser un % du solde total. On réduit le trade pour rentrer dans le
        # budget de risque restant ; s'il n'en reste plus, on refuse.
        if portfolio_equity > 0:
            import strategy as _S
            heat_cap = portfolio_equity * _S.P2().get("max_portfolio_heat_pct", 6.0) / 100.0
            remaining = heat_cap - max(0.0, portfolio_open_risk)
            if remaining <= 0:
                return SizingResult(False, 0, 0, 0, rr,
                                    ["Heat global au plafond : risque cumulé maximal atteint."])
            if risk_amount_account > remaining:
                risk_amount_account = remaining

        # 7) Conversion du risque vers la devise de cotation.
        #    R_quote = R_account / (valeur d'1 unité de cotation en devise compte)
        if quote_to_account_rate <= 0:
            return SizingResult(False, 0, 0, 0, rr,
                                ["Taux de conversion cotation invalide."])
        risk_amount_quote = risk_amount_account / quote_to_account_rate

        # 8) Taille brute en unités de la devise de base.
        #    Forex : unités entières (convention OANDA). Crypto : fractionnaire.
        raw_units = risk_amount_quote / d
        units = int(raw_units) if whole_units else round(raw_units, 8)
        if units <= 0:
            reasons.append("Taille calculée nulle (capital trop faible vs stop).")

        # 9) Levier effectif = notionnel / capital.
        notional_account = units * base_to_account_rate
        effective_leverage = (
            notional_account / base if base > 0 else 0
        )
        # Si le levier dépasse le plafond, on réduit les unités pour rentrer dedans.
        if effective_leverage > HARD_LIMITS["max_effective_leverage"]:
            max_notional = base * HARD_LIMITS["max_effective_leverage"]
            _u = max_notional / base_to_account_rate
            units = int(_u) if whole_units else round(_u, 8)
            notional_account = units * base_to_account_rate
            effective_leverage = notional_account / base
            # le risque réel baisse en conséquence
            risk_amount_quote = units * d
            risk_amount_account = risk_amount_quote * quote_to_account_rate

        # signe : vente -> unités négatives (convention OANDA)
        signed_units = units if proposal.side == "buy" else -units

        accepted = (len(reasons) == 0) and units > 0
        return SizingResult(
            accepted=accepted,
            units=signed_units,
            risk_amount_account_ccy=round(risk_amount_account, 2),
            effective_leverage=round(effective_leverage, 2),
            reward_risk_ratio=round(rr, 2),
            reasons=reasons,
        )
