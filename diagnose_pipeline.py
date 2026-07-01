"""
Diagnostic PIPELINE — simule le chemin réel du superviseur (propose) sur des
bougies réelles, et compte où chaque barre meurt : pas de signal / rejet sizing /
rejet spread / rejet corrélation / EN ATTENTE (manuel = hors bande ou garde) /
AUTO-VALIDÉ. Compare deux bandes (0.60 vs 0.70) pour isoler l'effet du seuil.

Usage (VPS) :  venv/bin/python3 diagnose_pipeline.py EUR_USD
"""
import sys
from collections import Counter
from datetime import datetime, timezone

from signals import SignalEngine
from supervisor import Supervisor
from news import RiskModulator
from risk_manager import Profile


class _Sess:
    def __init__(self, inst, amin, amax):
        self.id = "DIAG"; self.profile = Profile.DOUX
        self.accept_min = amin; self.accept_max = amax
        self.risk_level = "doux"; self.instrument = inst; self.mode = "apprentissage"
    @property
    def equity(self):
        return 500.0


def _why_none(note):
    n = (note or "").lower()
    if "spread" in n:
        return "rejet_spread"
    if "exposition" in n or "corrél" in n or "correl" in n:
        return "rejet_correlation"
    if "heat" in n:
        return "rejet_heat"
    if "ratio" in n or "taille" in n or "nulle" in n or "stop" in n:
        return "rejet_sizing"
    return "rejet_autre"


def _auto_reason(sess, p, now):
    """POURQUOI cette proposition ne s'auto-valide pas (cause exacte)."""
    from session import AUTO_RISK_CAP
    lo, hi = sess.accept_min, sess.accept_max
    if not (lo <= p.confidence <= hi):
        return "hors_bande (conf %.2f)" % p.confidence
    if p.caution < 1.0:
        return "caution_news (%.2f)" % p.caution
    cap = AUTO_RISK_CAP.get(sess.risk_level, 0.5)
    if sess.equity and (p.risk / sess.equity * 100) > cap:
        return "risk_cap"
    try:
        from config import PHASE1 as _P1
    except Exception:
        _P1 = {}
    if _P1.get("session_guard", True) and "/" not in p.pair:
        import sessions_clock as sc
        if sc.score_pair(p.pair, sc.open_sessions(now)) <= 0:
            return "GARDE_SESSION (marché fermé)"
    return "AUTO_VALIDE"


def simulate(candles, instrument, amin, now=None):
    sup = Supervisor(manager=None, journal_store=None, alert_sink=None,
                     engine=SignalEngine(), modulator=RiskModulator())
    now = now or datetime.now(timezone.utc)
    eng = sup.engine
    need = max(eng.ema_slow, 2 * eng.adx_period) + eng.rsi_period + 2
    cnt = Counter(); n = len(candles)
    pf = {"open_risk": 0.0, "equity": 5000.0, "ccy_exposure": {}}
    for end in range(need, n + 1):
        sess = _Sess(instrument, amin, 0.90)
        sup.pending.clear()
        p = sup.propose(sess, instrument, candles[:end], [], 1.0, 1.0,
                        now=now, spread=0.0, portfolio=pf, risk_scale=1.0)
        look = sup.last_look.get("DIAG", {})
        if p is None:
            cnt["no_signal" if not look.get("has_signal") else _why_none(look.get("note"))] += 1
        else:
            cnt[_auto_reason(sess, p, now)] += 1
    return cnt, max(0, n - need + 1)


def _fetch(instrument):
    if "/" in instrument:
        from kraken_data import KrakenData
        return KrakenData().get_history(instrument, interval=15)
    from oanda_client import OandaClient
    return OandaClient(account="practice").get_candles(instrument, granularity="M15", count=500)


if __name__ == "__main__":
    inst = sys.argv[1] if len(sys.argv) > 1 else "EUR_USD"
    try:
        candles = _fetch(inst)
    except Exception as e:
        print("bougies indisponibles:", e); sys.exit(1)
    print("\n=== PIPELINE %s (%d bougies) ===" % (inst, len(candles)))
    for amin in (0.70, 0.60):
        cnt, ev = simulate(candles, inst, amin)
        auto = cnt.get("AUTO_VALIDE", 0)
        print("\n-- bande min %.0f%% --  (%d barres évaluées)" % (amin * 100, ev))
        for k, v in cnt.most_common():
            print("   %-20s %5d  (%5.1f%%)" % (k, v, 100.0 * v / ev if ev else 0))
        print("   >> AUTO-VALIDÉS = %d  (%.1f%%)" % (auto, 100.0 * auto / ev if ev else 0))
