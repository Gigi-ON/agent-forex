"""Tests Lot 3 — Top 10 crypto (CoinGecko) + Top 10 forex (sessions_clock).
python3 tests_top.py"""
from datetime import datetime, timezone
import coingecko as cg
import sessions_clock as sc
import config


def t_crypto():
    mock = [
        {"symbol": "btc", "name": "Bitcoin", "current_price": 61000, "total_volume": 30e9, "market_cap": 1.2e12, "circulating_supply": 19e6, "price_change_percentage_24h": 2.5},
        {"symbol": "eth", "name": "Ethereum", "current_price": 3400, "total_volume": 15e9, "market_cap": 4.0e11, "circulating_supply": 120e6, "price_change_percentage_24h": -3.0},
        {"symbol": "doge", "name": "Dogecoin", "current_price": 0.12, "total_volume": 1e9, "market_cap": 1.7e10, "circulating_supply": 140e9, "price_change_percentage_24h": 0.2},
        {"symbol": "zzz", "name": "HorsUnivers", "current_price": 1, "total_volume": 99e9, "market_cap": 9e12, "circulating_supply": 1, "price_change_percentage_24h": 50},
    ]
    top = cg.rank_crypto(mock, ["BTC/USD", "ETH/USD", "DOGE/USD"], top=10)
    assert top[0]["symbol"] == "BTC/USD"                 # plus gros volume+cap+momentum
    assert all(r["symbol"] != "zzz" for r in top)        # hors univers exclu
    assert top[1]["state"] == "baissier"                 # ETH -3%
    assert top[0]["state"] == "haussier"                 # BTC +2.5%
    print("OK top-crypto (score volume+cap+momentum, univers, état)")


def t_forex():
    nu = datetime(2026, 1, 7, 14, 0, tzinfo=timezone.utc)   # mercredi, Londres+NY
    open_set = sc.open_sessions(nu)
    top = sc.rank_pairs(list(config.FOREX_PRIORITY), open_set, top=10)
    assert top and top[0]["score"] == 2                  # majors score 2 sur chevauchement
    assert {"EUR/USD", "GBP/USD"} <= {r["pair"] for r in top[:6]}
    # week-end -> aucune paire
    we = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)   # samedi
    assert sc._forex_closed_weekend(we) is True
    print("OK top-forex (marché ouvert + liquidité ; week-end vide)")


if __name__ == "__main__":
    t_crypto(); t_forex()
    print("\n=== Lot 3 (Top 10 crypto + forex) : tous les tests passent ===")
