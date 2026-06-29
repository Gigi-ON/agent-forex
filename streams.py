"""
Flux de prix natifs des courtiers — temps réel tick-par-tick (sous la seconde).

 - OANDA pricing stream  : HTTP streaming (forex), lignes JSON PRICE/HEARTBEAT.
 - Kraken WebSocket v2    : crypto, canal "ticker".

Chaque flux tourne dans un thread démon : il se (re)connecte tout seul et se
re-souscrit quand l'ensemble des instruments actifs change (ouverture/clôture de
session). AUCUNE exécution d'ordre — on ne fait que recevoir des prix et appeler
on_price(pair, mid). La clôture SL/TP et le push SSE sont gérés par l'appelant.

Formats d'instruments : forex = "EUR_USD" (underscore), crypto = "BTC/USD" (slash,
identique au format symbol de Kraken v2).
"""
import json
import time


def _log(tag, msg):
    try:
        print("[stream:%s] %s" % (tag, msg), flush=True)
    except Exception:
        pass


# ----------------------------------------------------------------- OANDA (forex)
def oanda_price_stream(get_instruments, get_creds, on_price, poll_resub=5.0):
    """get_instruments() -> liste d'instruments forex actifs ("EUR_USD", ...).
    get_creds() -> (token, account_id, env). on_price(instrument, mid)."""
    import requests
    while True:
        insts = sorted(set(get_instruments() or []))
        if not insts:
            time.sleep(poll_resub)
            continue
        token, account_id, env = get_creds()
        if not token or not account_id:
            time.sleep(10)
            continue
        host = "stream-fxtrade.oanda.com" if env == "live" else "stream-fxpractice.oanda.com"
        url = "https://%s/v3/accounts/%s/pricing/stream" % (host, account_id)
        try:
            with requests.get(url, params={"instruments": ",".join(insts)},
                              headers={"Authorization": "Bearer " + token},
                              stream=True, timeout=(10, 30)) as r:
                r.raise_for_status()
                _log("oanda", "connecte (%d instruments)" % len(insts))
                last_check = time.time()
                for line in r.iter_lines(decode_unicode=True):
                    if line:
                        try:
                            msg = json.loads(line)
                        except Exception:
                            continue
                        if msg.get("type") == "PRICE":
                            inst = msg.get("instrument")
                            bids = msg.get("bids") or []
                            asks = msg.get("asks") or []
                            if inst and bids and asks:
                                try:
                                    mid = (float(bids[0]["price"]) + float(asks[0]["price"])) / 2.0
                                    on_price(inst, mid)
                                except Exception:
                                    pass
                    if time.time() - last_check >= poll_resub:
                        last_check = time.time()
                        if set(get_instruments() or []) != set(insts):
                            _log("oanda", "instruments changes -> reconnexion")
                            break
        except Exception as e:
            _log("oanda", "erreur: %s" % e)
            time.sleep(3)


# --------------------------------------------------------------- Kraken (crypto)
def kraken_price_stream(get_symbols, on_price, poll_resub=5.0):
    """get_symbols() -> liste de symboles crypto actifs ("BTC/USD", ...).
    on_price(symbol, last)."""
    try:
        import websocket  # paquet websocket-client
    except Exception as e:
        _log("kraken", "websocket-client manquant (pip install websocket-client): %s" % e)
        return
    URL = "wss://ws.kraken.com/v2"
    while True:
        syms = sorted(set(get_symbols() or []))
        if not syms:
            time.sleep(poll_resub)
            continue
        ws = None
        try:
            ws = websocket.create_connection(URL, timeout=15)
            ws.send(json.dumps({"method": "subscribe",
                                "params": {"channel": "ticker", "symbol": syms}}))
            ws.settimeout(5)
            _log("kraken", "souscrit (%d symboles)" % len(syms))
            last_check = time.time()
            while True:
                raw = None
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    raw = None
                except Exception:
                    break
                if raw:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        msg = None
                    if isinstance(msg, dict) and msg.get("channel") == "ticker":
                        for d in (msg.get("data") or []):
                            sym = d.get("symbol")
                            px = d.get("last")
                            if sym and px is not None:
                                try:
                                    on_price(sym, float(px))
                                except Exception:
                                    pass
                if time.time() - last_check >= poll_resub:
                    last_check = time.time()
                    if set(get_symbols() or []) != set(syms):
                        _log("kraken", "symboles changes -> reconnexion")
                        break
        except Exception as e:
            _log("kraken", "erreur: %s" % e)
            time.sleep(3)
        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass
