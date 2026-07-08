"""
Test carry restreint a l'univers EXECUTABLE Kraken-US (9 majors) = juge de deployabilite reelle.
Reutilise capacity_study (funding 12h + OI + cout) mais restreint aux perps tradables aux US,
avec parametres adaptes a un petit panier (top 2-3). Lecture seule.
"""
import numpy as np
import pandas as pd

import capacity_study as CS

US9 = ["BTC", "ETH", "SOL", "XRP", "ADA", "LINK", "DOGE", "LTC", "AVAX"]

# petit univers -> selection plus large en fraction, minimum de noms bas
CS.Q = 0.4
CS.MIN_NAMES = 3


def main():
    fund, oi = CS.load()
    cols = [c for c in fund.columns if c in US9]
    if len(cols) < 3:
        raise SystemExit("Pas assez de majors Kraken-US dans data/capacity (trouve: %s)" % cols)
    fund = fund[cols]
    oi = oi[[c for c in oi.columns if c in cols]]
    ih = (pd.Series(fund.index).diff().dt.total_seconds().median()) / 3600.0
    ppy = 8760.0 / max(1.0, ih)
    print("== UNIVERS KRAKEN-US (9 majors) ==")
    print("Perps presents: %s" % ", ".join(cols))
    print("Periodes: %d | intervalle ~%.0fh | %s -> %s"
          % (fund.shape[0], ih, fund.index.min(), fund.index.max()), flush=True)
    cost = CS.load_cost()
    wk = max(2, int(round(168 / ih))); mo = max(4, int(round(720 / ih)))
    for lab, hold in [("hebdo", wk), ("mensuel", mo)]:
        print("\n-- Rebalance %s --" % lab)
        print("  cap/nom |  capacite tenable $ | net %/an |  pire annee")
        for cf in [0.001, 0.005, 0.01, 0.02]:
            s = CS.study(fund, oi, cost, cf, hold, wk, ppy)
            if not s:
                print("   %4.1f%% | (aucune periode eligible)" % (cf * 100)); continue
            print("   %4.1f%% | %18s | %+7.1f%% | %+7.1f%% (%s)"
                  % (cf * 100, "{:,.0f}".format(s["cap_usd"]), s["net_ann_pct"], s["worst_year"],
                     " ".join("%d:%+.0f" % (y, v) for y, v in sorted(s["by_year"].items()))))
    print("\n>> VERDICT DEPLOYABILITE US : capacite $ tenable + net %/an sur les 9 majors tradables aux US.")
    print("   Si net positif ET pire annee positive a une capacite serieuse -> edge deployable depuis ton VPS via Kraken.")
    print("   Si ca s'effondre (dispersion trop faible sur 9 majors) -> edge reel mais pas capturable ici pour l'instant.")


if __name__ == "__main__":
    main()
