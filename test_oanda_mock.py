"""
Test hors-ligne du module OANDA (aucun réseau).

Valide deux choses critiques avant le branchement réel :
  1. le parsing des bougies au format OANDA (bougies incomplètes ignorées),
  2. la logique de conversion vers le CAD (EUR/USD vs EUR/CAD).

On injecte des données simulées au format exact d'OANDA et on vérifie
les sorties. Lancer :  python test_oanda_mock.py
"""

from oanda_data import OandaData

# Réponse OANDA simulée (format réel : mid o/h/l/c, drapeau complete)
RAW = [
    {"time": "2026-06-24T13:00:00Z", "complete": True,
     "mid": {"o": "1.08500", "h": "1.08620", "l": "1.08470", "c": "1.08580"}},
    {"time": "2026-06-24T13:15:00Z", "complete": True,
     "mid": {"o": "1.08580", "h": "1.08650", "l": "1.08540", "c": "1.08610"}},
    {"time": "2026-06-24T13:30:00Z", "complete": False,   # en cours -> ignorée
     "mid": {"o": "1.08610", "h": "1.08630", "l": "1.08600", "c": "1.08620"}},
]


class FakeOanda(OandaData):
    """Surcharge get_latest pour tester les conversions sans réseau."""
    def get_latest(self, pair):
        prices = {
            "USD_CAD": {"bid": 1.3598, "ask": 1.3602},   # 1 USD ≈ 1.36 CAD
            "EUR_CAD": {"bid": 1.4695, "ask": 1.4705},   # 1 EUR ≈ 1.47 CAD
        }
        return prices[pair]


def test_parse():
    candles = OandaData.parse_candles(RAW)
    assert len(candles) == 2, "la bougie incomplète doit être ignorée"
    assert candles[0]["o"] == 1.0850 and candles[1]["c"] == 1.08610
    assert all(k in candles[0] for k in ("time", "o", "h", "l", "c"))
    print("✓ parsing des bougies : 2/3 retenues (incomplète ignorée), valeurs OK")


def test_conversions():
    od = FakeOanda(account_currency="CAD")
    # EUR/USD, compte CAD : quote=USD->CAD≈1.36 ; base=EUR->CAD≈1.47
    q, b = od.conversion_rates("EUR_USD")
    assert abs(q - 1.36) < 0.01 and abs(b - 1.47) < 0.01
    print(f"✓ EUR/USD -> quote_to_CAD={q:.4f}  base_to_CAD={b:.4f}")
    # EUR/CAD, compte CAD : quote=CAD->1.0 ; base=EUR->CAD≈1.47
    q2, b2 = od.conversion_rates("EUR_CAD")
    assert q2 == 1.0 and abs(b2 - 1.47) < 0.01
    print(f"✓ EUR/CAD -> quote_to_CAD={q2:.4f} (devise du compte)  base_to_CAD={b2:.4f}")


if __name__ == "__main__":
    test_parse()
    test_conversions()
    print("\nTous les tests passent. La logique OANDA est prête pour le réel.")
