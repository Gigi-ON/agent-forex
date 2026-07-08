"""
Fetcher Hyperliquid (Phase 1 du test carry realiste) — LECTURE SEULE, donnees publiques on-chain.
Recupere, pour l'univers liquide : funding history (horaire), OI ($), et profondeur L2 (spread + depth)
-> data/hyperliquid/. Sert a mesurer le "chiffre qui paie" (net apres coûts REELS + capacite).
Test d'acces integre : si l'IP du VPS est bloquee, le dit clairement (fallback Coinglass).
API : POST https://api.hyperliquid.xyz/info  (shapes verifiees via le SDK officiel).
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

URL = "https://api.hyperliquid.xyz/info"
OUT = "data/hyperliquid"
TOP_N = int(os.environ.get("HL_TOP", "50"))          # univers = top N par volume
FROM_MS = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
SLEEP = float(os.environ.get("HL_SLEEP", "0.18"))
DEPTH_PCT = 0.005                                     # profondeur mesuree a +/-0.5% du mid


def _post(body, tries=4):
    data = json.dumps(body).encode()
    for k in range(tries):
        try:
            req = urllib.request.Request(URL, data=data,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "agent-forex-hl/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and k < tries - 1:
                time.sleep(2.0 * (k + 1)); continue
            raise
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))


def access_test():
    """Retourne l'univers des perps, ou leve si l'API est injoignable/bloquee."""
    m = _post({"type": "meta"})
    uni = (m or {}).get("universe", [])
    return [u["name"] for u in uni]


def contexts():
    """metaAndAssetCtxs -> {name: {funding, oi_usd, vol_usd, mark}} (snapshot courant)."""
    res = _post({"type": "metaAndAssetCtxs"})
    uni = res[0]["universe"]; ctx = res[1]
    out = {}
    for u, c in zip(uni, ctx):
        try:
            mark = float(c.get("markPx") or c.get("oraclePx") or 0)
            oi = float(c.get("openInterest") or 0) * mark
            out[u["name"]] = {"funding": float(c.get("funding") or 0), "oi_usd": oi,
                              "vol_usd": float(c.get("dayNtlVlm") or 0), "mark": mark}
        except Exception:
            continue
    return out


def funding_history(coin, since_ms=None):
    """Pagine fundingHistory (horaire) depuis since_ms (sinon FROM_MS) -> [(ts_ms, rate)]. Incremental."""
    out = []; start = int(since_ms) if since_ms else FROM_MS; now = int(time.time() * 1000)
    while start < now:
        d = _post({"type": "fundingHistory", "coin": coin, "startTime": start})
        if not d:
            break
        for r in d:
            out.append((int(r["time"]), float(r["fundingRate"])))
        last = int(d[-1]["time"])
        if last <= start or len(d) < 2:
            break
        start = last + 1
        time.sleep(SLEEP)
    return out


def depth(coin):
    """l2Book -> spread bp + profondeur ($ dans +/-DEPTH_PCT) des deux cotes."""
    d = _post({"type": "l2Book", "coin": coin})
    lv = (d or {}).get("levels") or []
    if len(lv) < 2 or not lv[0] or not lv[1]:
        return None
    bids, asks = lv[0], lv[1]
    bb = float(bids[0]["px"]); ba = float(asks[0]["px"]); mid = (bb + ba) / 2
    if mid <= 0:
        return None
    spread_bp = (ba - bb) / mid * 1e4
    dep = 0.0
    for side in (bids, asks):
        for l in side:
            px = float(l["px"])
            if abs(px - mid) / mid <= DEPTH_PCT:
                dep += px * float(l["sz"])
    return {"spread_bp": round(spread_bp, 2), "depth_usd": round(dep)}


def main():
    import pandas as pd
    os.makedirs(os.path.join(OUT, "funding"), exist_ok=True)
    print("=== Test d'acces Hyperliquid depuis ce serveur ===", flush=True)
    try:
        uni = access_test()
    except Exception as e:
        raise SystemExit("ACCES REFUSE / API injoignable (%s). Si IP bloquee -> fallback Coinglass." % str(e)[:120])
    print("ACCES OK — %d perps dans l'univers." % len(uni), flush=True)

    ctx = contexts()
    top = sorted(ctx.items(), key=lambda kv: -kv[1]["vol_usd"])[:TOP_N]
    coins = [c for c, _ in top]
    print("Univers retenu : top %d par volume 24h. Ex.: %s" % (len(coins), ", ".join(coins[:12])), flush=True)

    depths = {}; catalog = []; t0 = time.time(); shape_shown = False
    for i, coin in enumerate(coins, 1):
        try:
            path = os.path.join(OUT, "funding", "%s.parquet" % coin)
            since = None; old = None
            if os.path.exists(path):
                try:
                    old = pd.read_parquet(path)
                    since = int(old["ts_ms"].max()) + 1
                except Exception:
                    old = None
            rows = funding_history(coin, since_ms=since)
            if not shape_shown and rows:
                print("  (verif shape funding %s : ex. %s)" % (coin, rows[:2]), flush=True); shape_shown = True
            if rows or old is not None:
                df = pd.DataFrame(rows, columns=["ts_ms", "rate"])
                if old is not None:
                    df = pd.concat([old[["ts_ms", "rate"]], df], ignore_index=True)
                df = df.drop_duplicates("ts_ms").sort_values("ts_ms")
                df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
                df.to_parquet(path)
                catalog.append({"coin": coin, "rows": int(len(df)),
                                "first": str(df["ts"].iloc[0]), "last": str(df["ts"].iloc[-1])})
            dp = depth(coin)
            if dp:
                depths[coin] = dp
        except Exception as e:
            print("  ! %s: %s" % (coin, str(e)[:70]), flush=True)
        if i % 10 == 0 or i == len(coins):
            print("  %d/%d | %.0fs" % (i, len(coins), time.time() - t0), flush=True)
        time.sleep(SLEEP)

    json.dump({c: ctx[c] for c in coins if c in ctx}, open(os.path.join(OUT, "_ctx.json"), "w"), indent=2)
    json.dump(depths, open(os.path.join(OUT, "_depth.json"), "w"), indent=2)
    span = " | %s -> %s" % (min(c["first"] for c in catalog), max(c["last"] for c in catalog)) if catalog else ""
    json.dump({"source": "hyperliquid", "kind": "funding+oi+depth", "interval": "1h", "raw_kept": True,
               "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "coins": len(catalog), "datasets": catalog},
              open(os.path.join(OUT, "_manifest.json"), "w"), indent=2)
    print("TERMINE : %d funding + OI + profondeur dans %s%s" % (len(catalog), OUT, span))


if __name__ == "__main__":
    main()
