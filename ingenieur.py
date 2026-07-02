"""
Ingénieur — méta-optimiseur de configuration, orchestré par un Claude (OpenRouter).

Rôle : PROPOSER des ajustements PHASE1/PHASE2 pour mieux entrer sur le marché et
éviter le manque de trades. Workflow en 3 ÉTAPES, chacune un verrou humain :
  1. Proposer  -> Claude analyse -> diff JSON validé (allowlist + bornes via strategy).
  2. Réviser   -> backtest AVANT/APRÈS + valider / rejeter / éditer.
  3. Appliquer -> strategy.apply_diff (versionné, rollback dispo).

Sécurité : ne touche QUE PHASE1/PHASE2 (validés par strategy). Jamais de secrets,
.env, ni LIVE_TRADING. Le LLM ne fait que proposer ; rien ne s'applique sans toi.
"""
import json
import re
import threading
import time
import uuid
from pathlib import Path

DATA = Path(__file__).parent / "data"
PROPS = DATA / "ingenieur_proposals.json"
MAX = 20
_LOCK = threading.Lock()

SYSTEM = (
    "Tu es un ingénieur d'optimisation de stratégie de trading paper. On te donne le "
    "journal, un backtest et la configuration courante (PHASE1 = qualité de signal, "
    "PHASE2 = survie/portefeuille). Propose des ajustements pour MIEUX ENTRER sur le "
    "marché et éviter le manque de trades, sans casser la discipline de risque. "
    "Réponds UNIQUEMENT par un JSON valide, sans texte autour : "
    '{\"diff\":{\"PHASE1\":{...},\"PHASE2\":{...}},\"rationale\":\"...\",\"expected_impact\":\"...\"}. '
    "N'utilise QUE des clés EXISTANTES de PHASE1/PHASE2. Pas de secrets, pas de .env. "
    "Sois concret et chiffré ; si rien à changer, renvoie un diff vide."
)


def _read():
    try:
        return json.loads(PROPS.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(lst):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = PROPS.with_suffix(".tmp")
    tmp.write_text(json.dumps(lst[-MAX:], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROPS)


def _parse(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def proposals():
    return list(reversed(_read()))


def get(pid):
    return next((p for p in _read() if p.get("id") == pid), None)


def _save(p):
    with _LOCK:
        lst = _read()
        lst = [x for x in lst if x.get("id") != p["id"]]
        lst.append(p)
        _write(lst)


def propose(context, session=None, model=None):
    import config, strategy, copilot
    model = model or getattr(config, "OPENROUTER_MODEL_INGENIEUR", None)
    user = ("Contexte (données réelles) :\n" + json.dumps(context, ensure_ascii=False)[:6000]
            + "\n\nPropose le diff PHASE1/PHASE2 en JSON.")
    r = copilot.ask([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                    model=model, session=session, reasoning={"enabled": True})
    if not isinstance(r, dict) or r.get("error"):
        return {"error": (r or {}).get("error", "Ingénieur indisponible")}
    obj = _parse(r.get("answer"))
    if not isinstance(obj, dict):
        return {"error": "réponse Ingénieur invalide (JSON attendu)"}
    diff, dropped = strategy.validate(obj.get("diff") or {})
    p = {"id": uuid.uuid4().hex[:8],
         "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "status": "proposee", "diff": diff, "dropped": dropped,
         "rationale": str(obj.get("rationale", ""))[:600],
         "expected_impact": str(obj.get("expected_impact", ""))[:400],
         "review": {"decision": None, "at": None}}
    _save(p)
    return p


def review(pid, decision, edits=None):
    import strategy
    p = get(pid)
    if not p:
        return {"error": "proposition introuvable"}
    if p.get("status") == "appliquee":
        return {"error": "déjà appliquée"}
    if edits is not None:
        d, _ = strategy.validate(edits)
        p["diff"] = d
    p["status"] = "validee" if decision == "valider" else "rejetee"
    p["review"] = {"decision": decision, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save(p)
    return p


def apply(pid):
    import strategy
    p = get(pid)
    if not p:
        return {"error": "proposition introuvable"}
    if p.get("status") != "validee":
        return {"error": "proposition non validée (étape 2 requise)"}
    if not (p.get("diff", {}).get("PHASE1") or p.get("diff", {}).get("PHASE2")):
        return {"error": "diff vide — rien à appliquer"}
    res = strategy.apply_diff(p["diff"], note=(p.get("rationale", "")[:60] or "ingenieur"),
                              source="ingenieur",
                              extra={"rationale": p.get("rationale", ""), "expected_impact": p.get("expected_impact", "")})
    p["status"] = "appliquee"
    p["applied_version"] = res["version"]
    _save(p)
    return {"applied": True, "version": res["version"]}


def backtest_impact(diff, instruments, fetch=None):
    """Backtest AVANT (config courante) vs APRÈS (config + diff PHASE1) par instrument."""
    from signals import SignalEngine
    from backtest_signals import backtest, _fetch
    import strategy
    fetch = fetch or _fetch
    cur = strategy.P1()
    prop = {**cur, **((diff or {}).get("PHASE1") or {})}
    TUN = ("adx_min", "pullback_atr_mult", "swing_lookback", "swing_buffer_atr",
           "stop_min_atr", "stop_max_atr", "vol_min_ratio", "vol_window")

    def eng(p1):
        e = SignalEngine(use_store=False)
        for k in TUN:
            if k in p1:
                setattr(e, k, p1[k])
        return e

    def st(tr):
        n = len(tr)
        if not n:
            return {"n": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "exp": 0.0, "tot": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
        wins = [t["R"] for t in tr if t["R"] > 0]
        losses = [t["R"] for t in tr if t["R"] <= 0]
        tot = sum(t["R"] for t in tr)
        return {"n": n, "wins": len(wins), "losses": len(losses),
                "win_rate": round(100.0 * len(wins) / n),
                "exp": round(tot / n, 3), "tot": round(tot, 1),
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0}

    import config as _cfg
    W = getattr(_cfg, "BACKTEST_WINDOW", 6000)
    out = []
    for inst in instruments:
        try:
            c = fetch(inst)
            if len(c) > W:          # fenetre recente : backtest rapide sur donnee profonde
                c = c[-W:]
        except Exception:
            continue
        out.append({"instrument": inst,
                    "avant": st(backtest(c, inst, engine=eng(cur))),
                    "apres": st(backtest(c, inst, engine=eng(prop)))})
    return {"impact": out, "note": "Backtest = impact PHASE1 (signal). PHASE2 = survie, jugée séparément."}
