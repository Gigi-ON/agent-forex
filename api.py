"""
API de lecture seule — fait le pont entre le bot et l'interface.

Expose UNIQUEMENT des GET, aucune écriture, aucun ordre :
  GET /api/status   → mode, environnement, état des verrous
  GET /api/account  → solde / NAV / devise du compte OANDA réel
  GET /api/signals  → signal courant par instrument (sur données réelles)
  GET /api/health   → vérif simple

Lancer en local :   ./venv/bin/uvicorn api:app --host 127.0.0.1 --port 8001
Derrière Nginx, exposé sous https://votre-domaine/api/ (voir le guide).

Note de sécurité : ces routes ne renvoient que des données de compte de
démonstration (practice). Avant d'exposer un compte réel, ajouter la
vérification du jeton Supabase (JWT) côté serveur — voir la section dédiée.
"""
import time
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

import config

app = FastAPI(title="Agent Forex API", version="1.0")

# CORS : par défaut on suppose un service same-origin (proxifié par Nginx).
# Pour autoriser un autre origine en test, définir FRONTEND_ORIGIN dans l'env.
_origin = os.environ.get("FRONTEND_ORIGIN", "")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_origin] if _origin else [],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# --- petit cache mémoire pour ne pas marteler OANDA -------------------------
_CACHE = {}

def _memo(key, ttl, producer):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = producer()
    _CACHE[key] = (now, val)
    return val


def _data():
    """Instancie OandaData paresseusement (évite l'erreur si pas de token)."""
    from oanda_data import OandaData
    return OandaData()


def _signal_to_dict(sig, price: Optional[dict]):
    p = getattr(sig, "proposal", None)
    return {
        "instrument": sig.instrument,
        "has_signal": p is not None,
        "side": getattr(p, "side", None),
        "entry": getattr(p, "entry_price", None),
        "stop": getattr(p, "stop_loss", None),
        "take_profit": getattr(p, "take_profit", None),
        "confidence": round(float(sig.confidence), 2),
        "notes": list(sig.notes or []),
        "price": price,
    }


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    """État non sensible : mode, environnement, verrous. Toujours disponible."""
    return {
        "environment": config.ENVIRONMENT,
        "live_trading": bool(config.LIVE_TRADING),
        "account_currency": config.ACCOUNT_CURRENCY,
        "instruments": list(config.INSTRUMENTS),
        # verrou effectif : il faut LIVE_TRADING **et** un compte réel
        "real_execution_possible": bool(config.LIVE_TRADING and config.ENVIRONMENT == "live"),
    }


@app.get("/api/account")
def account():
    """Solde / NAV réels du compte OANDA. 503 si identifiants absents/invalides."""
    def produce():
        od = _data()
        return od.get_account_summary()
    try:
        return _memo("account", 10, produce)
    except Exception as e:
        return {"error": "compte indisponible", "detail": str(e)}


@app.get("/api/signals")
def signals():
    """Signal courant par instrument, calculé sur les bougies réelles en cache."""
    def produce():
        from signals import SignalEngine
        od = _data()
        eng = SignalEngine()
        out = []
        for inst in config.INSTRUMENTS:
            try:
                candles = od.get_history(inst, "M15")
                sig = eng.evaluate(inst, candles)
                try:
                    price = od.get_latest(inst)
                except Exception:
                    price = None
                out.append(_signal_to_dict(sig, price))
            except Exception as e:
                out.append({"instrument": inst, "error": str(e)})
        return {"signals": out}
    try:
        return _memo("signals", 30, produce)
    except Exception as e:
        return {"error": "signaux indisponibles", "detail": str(e)}


# ===========================================================================
# Endpoints analytiques en LECTURE SEULE (Tier 1)
# Journal / apprentissage / maîtrise lisent le journal SQLite réel
# (vides tant qu'aucun trade clôturé). Backtest tourne sur les cours en cache.
# ===========================================================================

def _clean(obj):
    """Rend une structure JSON-safe (remplace inf/nan par None)."""
    import math
    if isinstance(obj, float):
        return None if (math.isinf(obj) or math.isnan(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def _closed_trades():
    from journal import JournalStore
    js = JournalStore()
    try:
        return js.closed_trades()
    finally:
        js.close()


@app.get("/api/journal")
def journal():
    """Post-mortem du journal réel. Vide (trades=0) tant qu'aucun trade clôturé."""
    from dataclasses import asdict
    from journal import analyze
    try:
        trades = _closed_trades()
        pm = analyze(trades)
        d = asdict(pm)
        d["win_rate"] = pm.win_rate
        recent = [{
            "pair": t.pair, "side": t.side, "r": t.r_multiple,
            "pnl": round(t.pnl, 2), "exit_reason": t.exit_reason,
            "outcome": t.outcome, "entry_time": t.entry_time,
        } for t in trades[-20:]]
        return _clean({"post_mortem": d, "recent": recent})
    except Exception as e:
        return {"error": "journal indisponible", "detail": str(e)}


@app.get("/api/learning")
def learning():
    """Calibration par tranche de fiabilité (apprentissage)."""
    from learning import calibrate
    try:
        cal = calibrate(_closed_trades())
        return _clean({
            "bands": [{"label": l, "n": n, "expectancy_R": e} for (l, n, e) in cal.bands],
            "recommended_min_confidence": cal.recommended_min_confidence,
            "enough_data": cal.enough_data, "note": cal.note,
        })
    except Exception as e:
        return {"error": "calibration indisponible", "detail": str(e)}


@app.get("/api/mastery")
def mastery():
    """Verdict de la campagne de maîtrise 30 j (go/no-go) sur le journal réel."""
    from dataclasses import asdict
    from market_mastery import evaluate
    try:
        trades = _closed_trades()
        base = 5000.0
        eq = [base]
        for t in trades:
            base += t.pnl
            eq.append(round(base, 2))
        v = evaluate(trades, eq)
        return _clean({"verdict": asdict(v)})
    except Exception as e:
        return {"error": "maîtrise indisponible", "detail": str(e)}


@app.get("/api/backtest")
def backtest_ep(pair: str = "EUR_USD", gran: str = "M15"):
    """Backtest + test de robustesse sur les cours en cache (données riches immédiates)."""
    from backtest import Backtester, robustness_report
    try:
        od = _data()
        candles = od.get_history(pair, gran)
        n = len(candles) if candles else 0
        if n < 60:
            return {"error": "historique insuffisant",
                    "detail": f"{n} bougies en cache pour {pair} {gran}. "
                              "Lancez la collecte d'historique (research_timeframe.py ou les routines)."}
        bt = Backtester()
        r = bt.run(pair, candles)
        return _clean({
            "pair": pair, "granularity": gran, "candles": n,
            "result": {
                "start_equity": r.start_equity, "end_equity": r.end_equity,
                "return_pct": r.return_pct, "win_rate": r.win_rate,
                "profit_factor": r.profit_factor, "trades": r.trades,
                "wins": r.wins, "losses": r.losses,
                "max_drawdown_pct": r.max_drawdown_pct, "equity_curve": r.equity_curve,
            },
            "robustness": robustness_report(pair, candles),
        })
    except Exception as e:
        return {"error": "backtest indisponible", "detail": str(e)}


# ===========================================================================
# Service PAPER-TRADING (Tier 2) — état persistant, exécutions simulées
# Signaux réels -> propositions supervisées (intervalle d'acceptation §10) ->
# positions papier suivies contre les prix réels -> trades journalisés.
# AUCUN ordre OANDA. Les écritures exigent une session Supabase valide.
# ===========================================================================
import json as _json
import threading as _threading
import time as _time
from pathlib import Path as _Path
from fastapi import Body, Header, HTTPException, Depends

from paper_engine import PaperEngine
from journal import JournalStore
from risk_manager import Profile

_PAPER_STATE = _Path(__file__).parent / "data" / "paper_state.json"
_paper_lock = _threading.Lock()
_paper = PaperEngine(starting_balance=5000.0, journal_store=JournalStore())
try:
    _paper.load_state(_json.loads(_PAPER_STATE.read_text()))
except Exception:
    pass


def _save_paper():
    try:
        _PAPER_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PAPER_STATE.with_suffix(".tmp")
        tmp.write_text(_json.dumps(_paper.to_state()))
        os.replace(str(tmp), str(_PAPER_STATE))   # écriture atomique
    except Exception:
        pass


_rate_cache = {}   # pair -> (ts, (q2a, b2a))


def _rates_cached(od, pair, ttl=60):
    hit = _rate_cache.get(pair)
    if hit and (_time.time() - hit[0]) < ttl:
        return hit[1]
    r = od.conversion_rates(pair)
    _rate_cache[pair] = (_time.time(), r)
    return r


def _candle_stale(candles, max_age_min=45):
    """True si la dernière bougie M15 est trop vieille (données périmées)."""
    if not candles:
        return True
    from datetime import datetime, timezone
    try:
        t = candles[-1]["time"].replace("Z", "").split(".")[0]
        dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0 > max_age_min
    except Exception:
        return False


def _gather_market():
    od = _data()
    market = {}
    for pair in config.INSTRUMENTS:
        try:
            candles = od.get_history(pair, "M15")
            px = od.get_latest(pair)             # prix live -> frais par construction
            price = (px["bid"] + px["ask"]) / 2.0
            q2a, b2a = _rates_cached(od, pair)   # taux mis en cache (B9)
            market[pair] = {"candles": candles, "price": price, "news": [],
                            "q2a": q2a, "b2a": b2a, "stale": _candle_stale(candles)}
        except Exception:
            continue
    return market


def _tick_loop():
    while True:
        try:
            m = _gather_market()
            if m:
                with _paper_lock:
                    _paper.tick(m)
                    _save_paper()
        except Exception:
            pass
        _time.sleep(15)


@app.on_event("startup")
def _start_paper():
    t = _threading.Thread(target=_tick_loop, daemon=True)
    t.start()


# -- auth : exige une session Supabase valide pour les écritures ------------
_SB_URL = os.environ.get("SUPABASE_URL", "https://qdhnnsipwnogecrptxfk.supabase.co")
_SB_ANON = os.environ.get("SUPABASE_ANON_KEY", "")
_REQUIRE_AUTH = os.environ.get("PAPER_REQUIRE_AUTH", "true").lower() == "true"


def require_user(authorization: str = Header(default="")):
    if not _REQUIRE_AUTH:
        return "dev"
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentification requise.")
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{_SB_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": _SB_ANON})
        with urllib.request.urlopen(req, timeout=5) as r:
            return _json.load(r).get("id")
    except Exception:
        raise HTTPException(status_code=401, detail="Session invalide.")


# -- statut du marché forex -------------------------------------------------
def _forex_next_open(now):
    from datetime import timedelta
    wd = now.weekday()
    cand = (now + timedelta(days=(6 - wd) % 7)).replace(hour=21, minute=0, second=0, microsecond=0)
    if cand <= now:
        cand = cand + timedelta(days=7)
    return cand


def _forex_closes_at(now):
    from datetime import timedelta
    wd = now.weekday()
    cand = (now + timedelta(days=(4 - wd) % 7)).replace(hour=21, minute=0, second=0, microsecond=0)
    if cand <= now:
        cand = cand + timedelta(days=7)
    return cand


@app.get("/api/market")
def market_status():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    is_open = _paper._forex_open(now)
    return {
        "forex_open": is_open,
        "next_open": None if is_open else _forex_next_open(now).isoformat(),
        "closes_at": _forex_closes_at(now).isoformat() if is_open else None,
        "server_utc": now.isoformat(),
    }


@app.get("/api/crypto")
def crypto_prices():
    """Cours crypto via l'API publique Kraken (lecture seule, aucune clé requise)."""
    from kraken_data import KrakenData
    try:
        return _clean({"prices": KrakenData().latest_quotes(config.CRYPTO_INSTRUMENTS)})
    except Exception as e:
        return {"error": "cours crypto indisponibles", "detail": str(e)}


# -- endpoints LECTURE -----------------------------------------------------
@app.get("/api/paper")
def paper_state():
    """État courant du paper-trading : solde, sessions, propositions, positions."""
    with _paper_lock:
        return _clean(_paper.snapshot())


# -- endpoints ÉCRITURE (auth requise) -------------------------------------
@app.post("/api/paper/session")
def paper_open_session(body: dict = Body(...), user=Depends(require_user)):
    try:
        budget = float(body.get("budget", 0))
        asset = body.get("asset", "forex")
        if asset == "forex":
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            if not _paper._forex_open(now):
                return {"error": "Le marché forex est fermé.",
                        "next_open": _forex_next_open(now).isoformat()}
        with _paper_lock:
            s = _paper.open_session(
                budget=budget,
                accept_min=body.get("accept_min"), accept_max=body.get("accept_max"),
                profile=Profile(body.get("profile", "reserve")),
                risk_level=body.get("risk_level", "reserve"),
                duration_min=int(body.get("duration_min", 240)))
            _save_paper()
        return {"ok": True, "session_id": s.id}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/paper/decide")
def paper_decide(body: dict = Body(...), user=Depends(require_user)):
    with _paper_lock:
        p = _paper.decide(body.get("pending_id"), body.get("action"))
        _save_paper()
    return {"ok": p is not None}


@app.post("/api/paper/session/close")
def paper_close_session(body: dict = Body(...), user=Depends(require_user)):
    with _paper_lock:
        _paper.close_session(body.get("session_id"))
        _save_paper()
    return {"ok": True}


@app.post("/api/paper/pause")
def paper_pause(user=Depends(require_user)):
    with _paper_lock:
        _paper.pause(); _save_paper()
    return {"ok": True, "running": _paper.running}


@app.post("/api/paper/resume")
def paper_resume(user=Depends(require_user)):
    with _paper_lock:
        _paper.resume(); _save_paper()
    return {"ok": True, "running": _paper.running}


@app.post("/api/paper/kill")
def paper_kill(user=Depends(require_user)):
    """Arrêt d'urgence : ferme toutes les positions papier et stoppe le moteur."""
    with _paper_lock:
        _paper.kill(_gather_market()); _save_paper()
    return {"ok": True, "running": _paper.running}
