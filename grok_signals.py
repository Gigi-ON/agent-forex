"""
Chasseur Grok — source de signal alternative (interface SignalEngine).

Grok « chasse » : il reçoit les indicateurs qu'on calcule (jamais de texte web brut),
et renvoie un JSON STRICT {action, side, confidence, rationale}. Tout ce qui est hors
schéma/allowlist -> wait (aucun trade). Le stop/objectif est calculé par L'APP (ATR),
jamais par Grok. La proposition retraverse ensuite le superviseur normal (bande, garde-
fous, exécution). PAPER uniquement ; jamais d'ordre brut ; aucun outil exposé au LLM.

Coût maîtrisé : 1 appel par bougie/instrument (cache), plafond d'appels/jour.
"""
import json
import re
import threading
from datetime import datetime, timezone

from indicators import atr, ema, rsi, adx, resample
from risk_manager import TradeProposal
from signals import Signal

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = (
    "Tu es un CHASSEUR de trades pour une plateforme de paper-trading. On te donne des "
    "indicateurs techniques chiffrés. Tu dois décider d'entrer ou d'attendre. "
    "RÈGLES STRICTES : tu n'exécutes RIEN, tu ne fixes NI le stop NI la taille. "
    "Tu réponds UNIQUEMENT par un objet JSON valide, sans aucun texte autour, de la forme : "
    '{\"action\":\"trade\"|\"wait\",\"side\":\"buy\"|\"sell\",\"confidence\":0.0-1.0,'
    '\"rationale\":\"<=140 caractères\"}. '
    "Si tu n'as pas de conviction nette, renvoie action=wait. Sois sélectif."
)

_LOCK = threading.Lock()
_CALLS = {"day": None, "n": 0}
_CACHE = {}


def _cap_ok():
    import config
    cap = getattr(config, "GROK_DAILY_CALL_CAP", 500)
    today = datetime.now(timezone.utc).date()
    with _LOCK:
        if _CALLS["day"] != today:
            _CALLS["day"] = today
            _CALLS["n"] = 0
        return _CALLS["n"] < cap


def _cap_inc():
    with _LOCK:
        _CALLS["n"] += 1


def calls_today():
    with _LOCK:
        return _CALLS["n"] if _CALLS["day"] == datetime.now(timezone.utc).date() else 0


def _parse(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class GrokSignalEngine:
    name = "grok"

    def __init__(self, ema_fast=20, ema_slow=50, rsi_period=14, atr_period=14,
                 atr_stop_mult=2.0, rr_target=2.0, htf_factor=4, client=None, model=None):
        self.ema_fast = ema_fast; self.ema_slow = ema_slow
        self.rsi_period = rsi_period; self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult; self.rr_target = rr_target
        self.htf_factor = htf_factor
        self._client = client
        self._model = model

    def _htf(self, candles):
        h = resample(candles, self.htf_factor)
        cl = [c["c"] for c in h]
        if len(cl) < self.ema_slow + 1:
            return None
        return "up" if ema(cl, self.ema_fast)[-1] > ema(cl, self.ema_slow)[-1] else "down"

    def _ask(self, instrument, ctx):
        import config
        key = getattr(config, "OPENROUTER_API_KEY", "")
        if not key:
            return {"error": "OPENROUTER_API_KEY manquante"}
        model = self._model or getattr(config, "OPENROUTER_MODEL_CHASSEUR", None) \
            or getattr(config, "OPENROUTER_MODEL", "x-ai/grok-4.1-fast")
        user = ("Instrument %s. Indicateurs (M15) : %s. Décide : action/side/confidence/rationale en JSON."
                % (instrument, json.dumps(ctx, ensure_ascii=False)))
        payload = {"model": model, "temperature": 0.2,
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": user}]}
        headers = {"Authorization": "Bearer " + key,
                   "HTTP-Referer": "https://agent-forex.unidevlabs.com", "X-Title": "agent-forex"}
        try:
            if self._client is not None:
                r = self._client.post(ENDPOINT, json=payload, headers=headers)
            else:
                import requests
                r = requests.post(ENDPOINT, json=payload, headers=headers, timeout=30)
            d = r.json() if hasattr(r, "json") else r
            if isinstance(d, dict) and d.get("error"):
                err = d["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                return {"error": ("OpenRouter: " + str(msg))[:180]}
            if not (isinstance(d, dict) and d.get("choices")):
                return {"error": ("réponse sans 'choices' (modèle invalide ?) : " + str(d)[:120])}
            return {"text": d["choices"][0]["message"]["content"]}
        except Exception as e:
            return {"error": str(e)[:160]}

    def evaluate(self, instrument, candles):
        closes = [c["c"] for c in candles]
        need = max(self.ema_slow, 2 * self.atr_period) + self.rsi_period + 2
        if len(closes) < need:
            return Signal(instrument, None, 0.0, ["Pas assez de bougies (%d/%d)." % (len(closes), need)])
        cache_key = (instrument, len(candles), round(closes[-1], 8))
        with _LOCK:
            hit = _CACHE.get(cache_key)
        if hit is not None:
            return hit
        if not _cap_ok():
            return Signal(instrument, None, 0.0, ["Plafond Grok/jour atteint — chasseur en pause."])

        ef = ema(closes, self.ema_fast)[-1]; es = ema(closes, self.ema_slow)[-1]
        r = rsi(closes, self.rsi_period)[-1]
        a, _ = atr(candles, self.atr_period); adxv = adx(candles, self.atr_period)
        price = closes[-1]
        if a <= 0:
            return Signal(instrument, None, 0.0, ["ATR nul."])
        ctx = {"price": round(price, 5), "ema_fast": round(ef, 5), "ema_slow": round(es, 5),
               "rsi": round(r, 1), "adx": round(adxv, 1), "atr": round(a, 5),
               "htf_trend": self._htf(candles),
               "ret_5": round((price / closes[-6] - 1) * 100, 2) if len(closes) > 6 else None}

        res = self._ask(instrument, ctx)
        _cap_inc()
        if res.get("error"):
            return Signal(instrument, None, 0.0, ["Grok indisponible : " + res["error"]])
        obj = _parse(res.get("text"))
        notes = ["ADX=%.1f RSI=%.1f HTF=%s" % (adxv, r, ctx["htf_trend"])]
        sig = Signal(instrument, None, 0.0, notes + ["Grok : réponse invalide → wait."])
        if isinstance(obj, dict):
            action = obj.get("action"); side = obj.get("side")
            if action == "trade" and side in ("buy", "sell"):
                try:
                    conf = max(0.0, min(1.0, float(obj.get("confidence", 0))))
                except Exception:
                    conf = 0.0
                d = self.atr_stop_mult * a
                stop = price - d if side == "buy" else price + d
                take = price + d * self.rr_target if side == "buy" else price - d * self.rr_target
                proposal = TradeProposal(instrument, side, round(price, 5), round(stop, 5), round(take, 5))
                rationale = str(obj.get("rationale", ""))[:140]
                sig = Signal(instrument, proposal, round(conf, 2), notes + ["Grok : " + rationale])
            else:
                sig = Signal(instrument, None, 0.0, notes + ["Grok : wait (%s)." % (obj.get("rationale", "")[:80])])
        with _LOCK:
            _CACHE[cache_key] = sig
            if len(_CACHE) > 500:
                _CACHE.clear()
        return sig
