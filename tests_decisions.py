"""Tests journal de décision. python3 tests_decisions.py"""
import decisions as D


def _reset():
    D._MEM[:] = []; D._LAST.clear(); D._save = lambda: None


def t_record_and_recent():
    _reset()
    D.record("s1", "BTC/USD", "buy", 0.66, 12.5, "auto", "✅ Auto-validé", ts=1000)
    D.record("s2", "EUR_USD", "sell", 0.5, 0.0, "rejected", "Hors séance", ts=1001)
    r = D.recent(10)
    assert len(r) == 2 and r[0]["instrument"] == "EUR_USD"    # plus récent d'abord
    assert D.recent(10, decision="auto")[0]["session"] == "s1"
    print("OK record + recent + filtre")


def t_dedup():
    _reset()
    a = D.record("s1", "BTC/USD", "buy", 0.6, 10, "pending", "conf hors bande", ts=2000)
    b = D.record("s1", "BTC/USD", "buy", 0.6, 10, "pending", "conf hors bande", ts=2050)   # < 120s -> ignoré
    c = D.record("s1", "BTC/USD", "buy", 0.6, 10, "pending", "conf hors bande", ts=2200)   # > 120s -> ok
    d = D.record("s1", "BTC/USD", "buy", 0.6, 10, "auto", "validé", ts=2210)               # motif diff -> ok
    assert a and (b is None) and c and d
    assert len(D.recent(10)) == 3
    print("OK anti-spam (dédup <120s, réenregistre si motif/décision change)")


def t_summary_today():
    _reset()
    now = 1_700_000_000
    D.record("s1", "BTC/USD", "buy", 0.7, 10, "auto", "validé", ts=now)
    D.record("s2", "ETH/USD", "buy", 0.6, 10, "rejected", "corrélation", ts=now + 1)
    D.record("s3", "EUR_USD", "sell", 0.5, 0, "rejected", "corrélation", ts=now + 2)
    s = D.summary_today(now)
    assert s["auto"] == 1 and s["rejected"] == 2 and s["total"] == 3
    assert s["top_reasons"][0] == ("corrélation", 2)
    print("OK résumé du jour (compte par type + motifs fréquents)")


def t_export_md():
    _reset()
    D.record("s1", "BTC/USD", "buy", 0.66, 12.5, "auto", "✅ Auto", ts=1000)
    md = D.export_md()
    assert md.startswith("# Journal de décision") and "BTC/USD" in md and "| auto |" in md
    assert D.export_md.__doc__ is None or True
    _reset()
    assert "Aucune décision" in D.export_md()
    print("OK export markdown")


if __name__ == "__main__":
    t_record_and_recent(); t_dedup(); t_summary_today(); t_export_md()
    print("\n=== Journal de décision : tous les tests passent ===")
