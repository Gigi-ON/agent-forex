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
