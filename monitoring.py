"""
Monitoring sans babysitting (leçon du benchmark) :
  - heartbeat : le moteur est-il vivant ? (fichier + endpoint)
  - récap quotidien : un résumé lisible de ce que la plateforme a fait
    (compte, sessions, décisions du jour, actions autopilote/auto-trainer).
Déterministe, alimenté par NOS chiffres réels — pas une narration LLM.
"""
import json
import time as _time
from pathlib import Path

_DATA = Path(__file__).parent / "data"
HEARTBEAT = _DATA / "heartbeat.json"
STALE_SEC = 60


def heartbeat(engine, now=None):
    now = _time.time() if now is None else now
    d = {"ts": now,
         "last_tick": getattr(engine, "last_tick", None),
         "running": bool(getattr(engine, "running", False)),
         "daily_halted": bool(getattr(engine, "daily_halted", False)),
         "active_sessions": len(engine.manager.active) if hasattr(engine, "manager") else 0}
    try:
        import autopilot
        d["autopilot"] = bool(autopilot.status().get("enabled"))
    except Exception:
        d["autopilot"] = None
    try:
        import autotrainer
        d["autotrainer"] = bool(autotrainer.status().get("enabled"))
    except Exception:
        d["autotrainer"] = None
    d["ok"] = True
    return d


def write_heartbeat(engine, now=None):
    try:
        _DATA.mkdir(exist_ok=True)
        HEARTBEAT.write_text(json.dumps(heartbeat(engine, now), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _fmt(v, suffix=""):
    return ("%s%s" % (v, suffix)) if v is not None else "n/d"


def _today_rows(journal, now):
    day = _time.strftime("%Y-%m-%d", _time.gmtime(now))
    return [x for x in (journal or []) if _time.strftime("%Y-%m-%d", _time.gmtime(x.get("ts", 0))) == day]


def recap_md(engine, now=None):
    now = _time.time() if now is None else now
    stamp = _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime(now))
    snap = engine.snapshot() if hasattr(engine, "snapshot") else {}
    out = ["# Récap quotidien — " + stamp, "",
           "## Compte",
           "- Solde : " + _fmt(snap.get("balance")),
           "- Disponible : " + _fmt(snap.get("available")),
           "- PnL du jour : " + _fmt(snap.get("day_pnl")),
           "- Coupe-circuit : " + ("OUI" if snap.get("daily_halted") else "non"),
           "- Moteur : " + ("en marche" if snap.get("running") else "arrêté")]

    sess = snap.get("sessions", []) or []
    out += ["", "## Sessions actives (%d)" % len(sess)]
    for s in sess[:12]:
        out.append("- #%s · %s · %s · PnL %s" % (
            s.get("id"), s.get("instrument", "?"), s.get("mode", ""), s.get("live_pnl")))
    if not sess:
        out.append("- aucune")

    try:
        import decisions
        d = decisions.summary_today(now)
        out += ["", "## Décisions du jour (%d)" % d.get("total", 0),
                "- Auto-validées : %d" % d.get("auto", 0),
                "- En attente : %d" % d.get("pending", 0),
                "- Refusées : %d" % d.get("rejected", 0)]
        if d.get("top_reasons"):
            out += ["", "Motifs les plus fréquents :"]
            out += ["- %s (%d)" % (r or "—", c) for r, c in d["top_reasons"]]
    except Exception:
        pass

    for mod, title in (("autopilot", "Autopilote"), ("autotrainer", "Auto-Trainer")):
        try:
            m = __import__(mod)
            rows = _today_rows(m.status().get("journal", []), now)
            out += ["", "## %s — activité du jour (%d)" % (title, len(rows))]
            for x in rows[:8]:
                t = _time.strftime("%H:%M", _time.gmtime(x.get("ts", 0)))
                out.append("- %s · %s" % (t, x.get("msg", "")))
            if not rows:
                out.append("- aucune action")
        except Exception:
            pass

    out += ["", "_Paper — aucun ordre réel. Le journal est l'outil d'amélioration : "
            "on y lit *pourquoi* ça sous-performe et on itère._"]
    return "\n".join(out)


