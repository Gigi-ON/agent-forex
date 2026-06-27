"""
DÉMO du rapport de session + calibration (hors-ligne).

  backtest -> trades clôturés -> rapport de session + calibration

Lancer :  python learning_demo.py
"""

from backtest import Backtester
from risk_manager import Profile
from session import Session, Tutelle
from learning import report_for, calibrate
from data_demo import make_m15


def main():
    # Trades clôturés (avec fiabilité + R) via un backtest.
    trades = Backtester(profile=Profile.DOUX).run("EUR_USD", make_m15()).trade_log

    # Simuler une session terminée à partir de ces trades.
    pnl = round(sum(t.pnl for t in trades), 2)
    sess = Session(allocated=2000.0, profile=Profile.DOUX, tutelle=Tutelle.MANUEL,
                   duration_min=120, risk_level="doux")
    sess.realized_pnl = pnl
    sess.trades = len(trades)
    sess.close_reason = "période terminée"

    print("=" * 60)
    print("RAPPORT DE SESSION")
    print("=" * 60)
    print(report_for(sess).summary())

    print("\n" + "=" * 60)
    print("CE QUE LE BOT RETIENT (calibration)")
    print("=" * 60)
    # min_samples abaissé pour la démo (peu de trades synthétiques)
    print(calibrate(trades, min_samples_per_band=5).summary())


if __name__ == "__main__":
    main()
