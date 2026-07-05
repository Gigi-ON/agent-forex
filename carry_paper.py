"""
CARRY PAPER delta-neutre sur l'univers perps KRAKEN-US (tourne 24/7 via cron horaire).
Short perp + long spot sur les perps au funding positif ; accumule le funding, marque le basis,
cout seulement a l'entree/sortie (amorti). P&L paper = signal honnête de "prêt a deployer".

CONTROLE DES PAIRS (anti-erreur) : chaque base doit resoudre vers un perp Kraken Futures
existant ET une paire spot Kraken valide (derivee de AssetPairs, pas devinee). Toute base
non resolue est signalee NOMMEE (jamais droppee en silence).

Donnees Kraken publiques (sans cle). Paper only. Ecrit data/carry_paper/. Aucun ordre reel.
Usage : carry_paper.py         -> un tick 24/7 (cron)
        carry_paper.py check   -> controle des pairs (table de validation)

>> Elargir l'univers : ajouter la base a US_UNIVERSE quand Kraken-US liste un nouveau perp.
"""
import json
import os
import time
import urllib.request

OUT = "data/carry_paper"
STATE = os.path.join(OUT, "state.json")
FUT = "https://futures.kraken.com/derivatives/api/v3/tickers"
ASSETPAIRS = "https://api.kraken.com/0/public/AssetPairs"
SPOT = "https://api.kraken.com/0/public/Ticker"

CAPITAL = 100000.0
COST_BP = 40.0
REBAL_H = 24.0
FUND_PER_H = 1.0

US_UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "ADA", "LINK", "DOGE", "LTC", "AVAX"]


def kbase(b):
    return {"BTC": "XBT", "DOGE": "XDG"}.get(b, b)


def _get(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "carry-paper/1.0"}), timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))


def resolve_symbols():
    """base -> {perp, spot_altname, apkey} DERIVE de Kraken (AssetPairs + Futures tickers).
    Renvoie (resolved, missing) ; missing = bases non resolues, avec la raison."""
    ap = (_get(ASSETPAIRS).get("result", {}) or {})
    perps = {t.get("symbol", "").upper() for t in _get(FUT).get("tickers", [])}
    resolved = {}; missing = []
    for b in US_UNIVERSE:
        kb = kbase(b)
        alt = apkey = None
        for k, v in ap.items():
            if v.get("wsname") == kb + "/USD":            # spot USD canonique
                alt = v.get("altname", k); apkey = k; break
        perp = ("PF_" + kb + "USD").upper()
        has_perp = perp in perps
        if alt and has_perp:
            resolved[b] = {"perp": perp, "spot": alt, "apkey": apkey}
        else:
            why = []
            if not alt: why.append("spot introuvable")
            if not has_perp: why.append("perp %s absent" % perp)
            missing.append("%s (%s)" % (b, ", ".join(why)))
    return resolved, missing


def fetch_market():
    """({base:{funding,perp,spot}}, missing). Utilise la resolution canonique + controle."""
    resolved, missing = resolve_symbols()
    if not resolved:
        return {}, missing
    sres = (_get(SPOT + "?pair=" + ",".join(v["spot"] for v in resolved.values())).get("result", {}) or {})
    fmap = {t.get("symbol", "").upper(): t for t in _get(FUT).get("tickers", [])}
    out = {}
    for b, r in resolved.items():
        t = fmap.get(r["perp"])
        srow = sres.get(r["apkey"]) or sres.get(r["spot"])
        if not t or not srow:
            missing.append("%s (prix indispo)" % b); continue
        try:
            spot = float(srow["c"][0]); mark = float(t.get("markPrice") or t.get("last"))
            fr = float(t["fundingRate"])
        except Exception:
            missing.append("%s (donnee illisible)" % b); continue
        out[b] = {"funding": fr, "perp": mark, "spot": spot}
    return out, missing


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    now = time.time()
    return {"capital": CAPITAL, "start_ts": now, "last_tick": now, "last_rebal": 0.0,
            "realized_pnl": 0.0, "positions": []}


def pos_value(p, mkt):
    m = mkt.get(p["base"])
    leg = 0.0
    if m:
        leg = p["notional"] * (m["spot"] / p["spot_entry"] - 1.0) \
            + p["notional"] * (1.0 - m["perp"] / p["perp_entry"])
    return p["funding_acc"] + leg - p["cost_paid"]


def tick():
    os.makedirs(OUT, exist_ok=True)
    st = load_state()
    mkt, missing = fetch_market()
    now = time.time()
    dh = max(0.0, (now - st["last_tick"]) / 3600.0)

    for p in st["positions"]:
        m = mkt.get(p["base"])
        if m:
            p["funding_acc"] += p["notional"] * m["funding"] * (dh * FUND_PER_H)

    slot = st["capital"] / len(US_UNIVERSE)
    half = slot * (COST_BP / 2.0) / 1e4
    due = (now - st.get("last_rebal", 0.0)) >= REBAL_H * 3600.0 or not st["positions"]
    if due and mkt:
        target = {b for b, m in mkt.items() if m["funding"] > 0}
        keep = []
        for p in st["positions"]:
            if p["base"] in target and p["base"] in mkt:
                keep.append(p)
            else:
                st["realized_pnl"] += pos_value(p, mkt) - half
        st["positions"] = keep
        have = {p["base"] for p in st["positions"]}
        for b in target - have:
            m = mkt[b]
            st["positions"].append({"base": b, "notional": slot, "spot_entry": m["spot"],
                                    "perp_entry": m["perp"], "funding_acc": 0.0, "cost_paid": half})
        st["last_rebal"] = now

    open_val = sum(pos_value(p, mkt) for p in st["positions"])
    equity = st["capital"] + st["realized_pnl"] + open_val
    elapsed_d = (now - st["start_ts"]) / 86400.0
    net_ann = 0.0 if elapsed_d < 2.0 else (equity / st["capital"] - 1.0) / (elapsed_d / 365.25) * 100.0
    st["last_tick"] = now
    st["equity"] = round(equity, 2)
    st["net_ann_pct"] = round(net_ann, 2)
    st["universe_size"] = len(mkt)
    st["pairs_control"] = {"resolues": len(mkt), "attendu": len(US_UNIVERSE), "manquants": missing}
    json.dump(st, open(STATE, "w"), indent=2)
    ctrl = "OK %d/%d" % (len(mkt), len(US_UNIVERSE)) if not missing else "ATTENTION %d/%d manquants: %s" % (
        len(mkt), len(US_UNIVERSE), "; ".join(missing))
    na = "demarrage" if elapsed_d < 2.0 else "%+.1f%%/an" % net_ann
    print("%s | controle pairs: %s | positions %d | equity %.2f (%+.2f) | net %s"
          % (time.strftime("%Y-%m-%d %H:%M"), ctrl, len(st["positions"]),
             equity, equity - st["capital"], na), flush=True)
    return st


def check():
    resolved, missing = resolve_symbols()
    mkt, missing2 = fetch_market()
    print("== CONTROLE DES PAIRS (Kraken-US) ==")
    for b in US_UNIVERSE:
        if b in mkt:
            m = mkt[b]
            print("  OK    %-5s perp=%-11s spot=%.4f  funding=%+.5f%%/h" % (b, resolved[b]["perp"], m["spot"], m["funding"] * 100))
        else:
            r = next((x for x in (missing + missing2) if x.startswith(b + " ")), b + " (non resolu)")
            print("  FAIL  %s" % r)
    print("Resolues: %d/%d" % (len(mkt), len(US_UNIVERSE)))
    print(">> Toute FAIL = pair a corriger AVANT de trader (jamais droppee en silence)." if len(mkt) < len(US_UNIVERSE)
          else ">> Toutes les pairs resolvent proprement.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check()
    else:
        tick()
