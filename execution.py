"""
Adaptateurs d'exécution — route chaque session vers son lieu d'exécution.

  - Pratique      -> InternalExecutor : le moteur simule les fills en interne.
  - Apprentissage -> exécution sur compte PAPER du courtier :
        forex  : OandaExecutor("practice")  (api-fxpractice, argent démo)
        crypto : Alpaca paper               (incrément 2, à venir)
  - Réel          -> mêmes adaptateurs, comptes LIVE, VERROUILLÉS :
        OandaExecutor("live") n'envoie que si config.LIVE_TRADING == True.

Sécurité : envoyer sur un compte PRACTICE (démo) est sans risque et toujours
autorisé. Envoyer sur un compte LIVE (argent réel) exige le verrou LIVE_TRADING.
Le client réseau est injectable (tests sans dépendance OANDA).
"""
from dataclasses import dataclass
import time as _time
import threading as _threading
import uuid as _uuid


@dataclass
class ExecResult:
    ok: bool = False
    trade_id: str = None
    fill_price: float = None
    pnl: float = None               # PnL réalisé rapporté par le courtier (clôtures)
    blocked: bool = False           # refusé par un garde (ex. live verrouillé)
    error: str = None
    raw: dict = None



# === Durcissement réseau partagé : retry/backoff + circuit-breaker ==========
# But : un courtier lent ou en panne ne doit JAMAIS geler le moteur (tick loop).
# - timeout court, retry borné respectant Retry-After sur 429/5xx,
# - circuit-breaker : après N appels échoués d'affilée, on saute le fournisseur
#   pendant COOLDOWN s (CircuitOpen levé instantanément, sans toucher le réseau),
# - throttle adaptatif si X-RateLimit-Remaining devient bas.
NET_TIMEOUT = 6.0          # s : court, pour ne pas bloquer le tick sous le lock
NET_RETRIES = 3            # tentatives sur 429/5xx
BREAKER_FAILS = 3          # appels échoués consécutifs avant ouverture
BREAKER_COOLDOWN = 60.0    # s de pause quand le disjoncteur est ouvert
RL_LOW = 20                # seuil X-RateLimit-Remaining -> throttle

_BREAKER = {}
_BREAKER_LOCK = _threading.Lock()


class CircuitOpen(Exception):
    """Disjoncteur ouvert : appel sauté sans taper le réseau."""


def breaker_open(provider, now=None):
    now = _time.time() if now is None else now
    with _BREAKER_LOCK:
        b = _BREAKER.get(provider)
        return bool(b and b.get("open_until", 0.0) > now)


def breaker_record(provider, ok, now=None):
    now = _time.time() if now is None else now
    with _BREAKER_LOCK:
        b = _BREAKER.setdefault(provider, {"open_until": 0.0, "fails": 0})
        if ok:
            b["fails"] = 0
            b["open_until"] = 0.0
        else:
            b["fails"] += 1
            if b["fails"] >= BREAKER_FAILS:
                b["open_until"] = now + BREAKER_COOLDOWN
        return dict(b)


def breaker_reset(provider=None):
    with _BREAKER_LOCK:
        if provider is None:
            _BREAKER.clear()
        else:
            _BREAKER.pop(provider, None)


def _maybe_throttle(r, sleep):
    try:
        rem = (getattr(r, "headers", {}) or {}).get("X-RateLimit-Remaining")
        if rem is not None and int(rem) < RL_LOW:
            sleep(0.5)
    except Exception:
        pass


def with_retry(provider, do, sleep=None, retries=NET_RETRIES, now=None):
    """Exécute do() -> réponse (objet .status_code/.headers) avec retry/backoff
    sur 429/5xx (respecte Retry-After) et circuit-breaker. do() peut lever
    (erreur réseau). Compte 1 échec par APPEL (pas par tentative)."""
    sleep = _time.sleep if sleep is None else sleep
    nowf = (lambda: _time.time()) if now is None else now
    if breaker_open(provider, nowf()):
        raise CircuitOpen("disjoncteur %s ouvert" % provider)
    last = None
    for attempt in range(retries):
        try:
            r = do()
        except Exception as e:
            last = e
            if attempt == retries - 1:
                breaker_record(provider, False, nowf())
                raise
            sleep(min(2 ** attempt * 0.4, 4.0))
            continue
        sc = getattr(r, "status_code", 200)
        if sc == 429 or sc >= 500:
            if attempt == retries - 1:
                breaker_record(provider, False, nowf())
                return r
            wait = 0.0
            try:
                wait = float((getattr(r, "headers", {}) or {}).get("Retry-After", 0) or 0)
            except Exception:
                wait = 0.0
            sleep(wait or min(2 ** attempt * 0.4, 4.0))
            continue
        breaker_record(provider, True, nowf())
        _maybe_throttle(r, sleep)
        return r
    breaker_record(provider, False, nowf())
    raise last or Exception("échec réseau %s" % provider)


class InternalExecutor:
    """Pratique : aucune exécution externe, le moteur gère tout en interne."""
    name = "internal"
    venue = "interne"

    def place(self, instrument, units, stop, take_profit):
        return ExecResult(ok=True)          # le moteur simule le fill

    def nav(self):
        return None

    def open_trades(self):
        return []

    def open_map(self):
        return {}

    def modify_stop(self, ref, stop, instrument=None):
        return ExecResult(ok=True)

    def partial_close(self, ref, units, side=None, instrument=None):
        return ExecResult(ok=True)

    def trade_pnl(self, ref):
        return None

    def close(self, ref, instrument=None):
        return ExecResult(ok=True)


class OandaExecutor:
    """Forex via OANDA. account='practice' (démo) ou 'live' (réel, gated)."""

    def __init__(self, account="practice", client=None, live_trading=None, env=None):
        import config
        acc = config.ACCOUNTS.get(account, {})
        self.account_name = account
        self.env = env or acc.get("env") or ("live" if account == "live" else "practice")
        self._live_trading = config.LIVE_TRADING if live_trading is None else live_trading
        self._client = client               # injectable pour les tests
        self.name = "oanda:" + account
        self.venue = "OANDA " + self.env

    def can_send(self):
        """Practice (démo) : toujours OK. Live (réel) : exige LIVE_TRADING."""
        if self.env == "practice":
            return True
        return bool(self._live_trading)

    def _cli(self):
        if self._client is None:
            from oanda_client import OandaClient
            self._client = OandaClient(account=self.account_name)
        return self._client

    def place(self, instrument, units, stop, take_profit):
        if not units:
            return ExecResult(error="units=0")
        if not self.can_send():
            return ExecResult(blocked=True,
                              error="LIVE_TRADING désactivé : ordre réel bloqué.")
        try:
            r = self._cli().place_market_order(instrument, units, stop, take_profit)
        except Exception as e:
            return ExecResult(error=str(e))
        if not isinstance(r, dict):
            return ExecResult(error="réponse inattendue")
        if r.get("blocked"):
            return ExecResult(blocked=True, error=r.get("message"), raw=r)
        resp = r.get("response") or {}
        fill = resp.get("orderFillTransaction") or {}
        tid = None
        opened = fill.get("tradeOpened") or {}
        if opened:
            tid = opened.get("tradeID")
        price = fill.get("price")
        try:
            price = float(price) if price is not None else None
        except Exception:
            price = None
        return ExecResult(ok=True, trade_id=tid, fill_price=price, raw=r)

    def nav(self):
        try:
            return float(self._cli().get_equity())
        except Exception:
            return None

    def account(self):
        """Compte OANDA : NAV/solde/devise/nb trades ouverts."""
        return self._cli().account_summary()

    def open_trades(self):
        try:
            return self._cli().get_open_trades()
        except Exception:
            return []

    def open_map(self):
        """{trade_id: {unrealized, price}} des trades ouverts OANDA."""
        out = {}
        for t in self.open_trades():
            tid = str(t.get("id"))
            try:
                out[tid] = {"unrealized": float(t.get("unrealizedPL", 0) or 0),
                            "price": float(t.get("price", 0) or 0)}
            except Exception:
                continue
        return out

    @staticmethod
    def _pl(resp):
        try:
            f = (resp or {}).get("orderFillTransaction") or {}
            return float(f.get("pl")) if f.get("pl") is not None else None
        except Exception:
            return None

    def close(self, ref, instrument=None):
        try:
            d = self._cli().close_trade(ref)
            return ExecResult(ok=True, pnl=self._pl(d), raw=d)
        except Exception as e:
            return ExecResult(error=str(e))

    def modify_stop(self, ref, stop, instrument=None):
        try:
            self._cli().modify_stop(ref, stop)
            return ExecResult(ok=True)
        except Exception as e:
            return ExecResult(error=str(e))

    def partial_close(self, ref, units, side=None, instrument=None):
        try:
            d = self._cli().partial_close(ref, int(abs(units)))
            return ExecResult(ok=True, pnl=self._pl(d), raw=d)
        except Exception as e:
            return ExecResult(error=str(e))

    def trade_pnl(self, ref):
        try:
            return self._cli().trade_realized_pl(ref)
        except Exception:
            return None


class AlpacaExecutor:
    """Crypto via Alpaca. account='paper' (démo) ou 'live' (réel, gated).
    Alpaca crypto n'a PAS de SL/TP attachés -> le moteur gère la sortie et appelle
    close() pour aplatir la position chez Alpaca."""

    def __init__(self, account="paper", session=None, key=None, secret=None,
                 base=None, live_trading=None):
        import config
        self.account_name = account
        self.env = "live" if account == "live" else "paper"
        self.base = base or ("https://api.alpaca.markets" if self.env == "live"
                             else "https://paper-api.alpaca.markets")
        self._key = key if key is not None else config.ALPACA_PAPER_KEY
        self._secret = secret if secret is not None else config.ALPACA_PAPER_SECRET
        self._live_trading = config.LIVE_TRADING if live_trading is None else live_trading
        self._session = session            # injectable (objet requests-like) pour tests
        self.name = "alpaca:" + account
        self.venue = "Alpaca " + self.env

    def can_send(self):
        if self.env == "paper":
            return True
        return bool(self._live_trading)

    def _req(self, method, path, **kw):
        url = self.base + path
        sess = self._session
        if sess is not None:
            do = lambda: getattr(sess, method)(url, **kw)          # client injecté (tests)
        else:
            import requests
            headers = {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret}
            do = lambda: requests.request(method, url, headers=headers, timeout=NET_TIMEOUT, **kw)
        return with_retry("alpaca", do)

    @staticmethod
    def _json(r):
        return r.json() if hasattr(r, "json") else r

    def place(self, instrument, units, stop, take_profit):
        if not units:
            return ExecResult(error="units=0")
        if not self.can_send():
            return ExecResult(blocked=True, error="LIVE_TRADING désactivé : ordre réel bloqué.")
        side = "buy" if units > 0 else "sell"
        try:
            r = self._req("post", "/v2/orders", json={
                "symbol": instrument, "qty": str(abs(units)),
                "side": side, "type": "market", "time_in_force": "gtc",
                "client_order_id": "af-" + _uuid.uuid4().hex[:24]})  # idempotence sur retry
            d = self._json(r)
        except Exception as e:
            return ExecResult(error=str(e))
        if isinstance(d, dict) and d.get("id"):
            return ExecResult(ok=True, trade_id=d["id"], raw=d)
        msg = d.get("message") if isinstance(d, dict) else "réponse inattendue"
        return ExecResult(error=msg, raw=d if isinstance(d, dict) else None)

    def nav(self):
        try:
            d = self._json(self._req("get", "/v2/account"))
            return float(d.get("equity") or d.get("cash"))
        except Exception:
            return None

    def account(self):
        """Compte Alpaca : equity/cash/buying_power + quota (en-têtes rate-limit)."""
        r = self._req("get", "/v2/account")
        d = self._json(r)
        rl = rr = None
        try:
            h = getattr(r, "headers", {}) or {}
            rl = int(h["X-RateLimit-Limit"]) if h.get("X-RateLimit-Limit") else None
            rr = int(h["X-RateLimit-Remaining"]) if h.get("X-RateLimit-Remaining") else None
        except Exception:
            pass
        return {"equity": float(d.get("equity") or 0), "cash": float(d.get("cash") or 0),
                "buying_power": float(d.get("buying_power") or 0),
                "currency": d.get("currency", "USD"), "status": d.get("status"),
                "rate_limit": rl, "rate_remaining": rr}

    def open_trades(self):
        try:
            d = self._json(self._req("get", "/v2/positions"))
            return d if isinstance(d, list) else []
        except Exception:
            return []

    def open_map(self):
        """{symbole NORMALISÉ (sans slash) : {unrealized, price}} des positions Alpaca.
        Alpaca renvoie 'BTCUSD' (sans slash) alors qu'on envoie 'BTC/USD' -> on
        normalise pour pouvoir réconcilier."""
        out = {}
        for p in self.open_trades():
            sym = p.get("symbol")
            if not sym:
                continue
            try:
                out[str(sym).replace("/", "")] = {
                    "unrealized": float(p.get("unrealized_pl", 0) or 0),
                    "price": float(p.get("current_price", 0) or 0)}
            except Exception:
                continue
        return out

    def close(self, ref, instrument=None):
        sym = instrument or ref
        try:
            self._req("delete", "/v2/positions/" + str(sym).replace("/", "%2F"))
            return ExecResult(ok=True)
        except Exception as e:
            return ExecResult(error=str(e))

    def modify_stop(self, ref, stop, instrument=None):
        # Alpaca crypto n'a pas d'ordre stop attaché à la position : le SL/TP
        # reste géré par le moteur (qui appellera close/partial_close). No-op ici.
        return ExecResult(ok=True)

    def partial_close(self, ref, units, side=None, instrument=None):
        # Réduction partielle = ordre opposé au marché pour la quantité voulue.
        sym = instrument or ref
        opp = "sell" if side == "buy" else "buy"
        try:
            r = self._req("post", "/v2/orders", json={
                "symbol": sym, "qty": str(abs(units)),
                "side": opp, "type": "market", "time_in_force": "gtc",
                "client_order_id": "af-" + _uuid.uuid4().hex[:24]})  # idempotence sur retry
            d = self._json(r)
            return ExecResult(ok=bool(isinstance(d, dict) and d.get("id")), raw=d if isinstance(d, dict) else None)
        except Exception as e:
            return ExecResult(error=str(e))

    def trade_pnl(self, ref):
        return None


_CACHE = {}


def executor_for(mode, asset, cache=None):
    """Choisit l'adaptateur selon le mode de session et l'actif.
    asset : 'crypto' (paire avec '/') ou 'forex'."""
    cache = _CACHE if cache is None else cache
    mode = (mode or "pratique").lower()
    is_forex = (asset != "crypto")

    def _get(key, factory):
        if key not in cache:
            cache[key] = factory()
        return cache[key]

    if mode == "apprentissage":
        if is_forex:
            return _get("oanda:practice", lambda: OandaExecutor("practice"))
        return _get("alpaca:paper", lambda: AlpacaExecutor("paper"))
    if mode in ("reel", "réel", "real"):
        if is_forex:
            return _get("oanda:live", lambda: OandaExecutor("live"))
        return _get("alpaca:live", lambda: AlpacaExecutor("live"))
    return _get("internal", InternalExecutor)


def asset_of(instrument):
    return "crypto" if (instrument and "/" in instrument) else "forex"
