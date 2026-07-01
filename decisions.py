"""
Journal de décision — la pièce la plus précieuse (leçon du benchmark).

Enregistre CHAQUE décision quand un trade est « sur la table » : auto-validé,
en attente, ou refusé — AVEC le motif (y compris les inactions). Relisable,
résumable, exportable en markdown. Anti-spam : on ne réenregistre pas la même
décision (même instrument/type/motif) pour une session à moins de 120 s.

Best-effort : record() n'échoue jamais bruyamment (ne doit pas casser le
pipeline superviseur).
"""
import json
import threading
import time as _time
from pathlib import Path

_LOCK = threading.Lock()
_DATA = Path(__file__).parent / "data"
FILE = _DATA / "decisions.json"
MAX = 500
DEDUP_SEC = 120

KINDS = ("auto", "pending", "rejected")

_MEM = []          # plus récent d'abord
_LAST = {}         # session_id -> (instrument, decision, reason, ts)


def _load():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(d, list):
            _MEM[:] = d[:MAX]
    except Exception:
        pass


def _save():
    try:
        _DATA.mkdir(exist_ok=True)
        FILE.write_text(json.dumps(_MEM[:MAX], ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def record(session_id, instrument, side, confidence, risk, decision, reason, ts=None):
    try:
        ts = _time.time() if ts is None else float(ts)
        key = (instrument, decision, reason)
        with _LOCK:
            last = _LAST.get(session_id)
            if last and last[0:3] == key and (ts - last[3]) < DEDUP_SEC:
                return None            # anti-spam : décision identique trop récente
            _LAST[session_id] = (instrument, decision, reason, ts)
            entry = {"ts": ts, "session": session_id, "instrument": instrument,
                     "side": side, "confidence": round(float(confidence or 0), 3),
                     "risk": round(float(risk or 0), 2),
                     "decision": decision if decision in KINDS else "rejected",
                     "reason": str(reason)[:200]}
            _MEM.insert(0, entry)
            del _MEM[MAX:]
            _save()
            return entry
    except Exception:
        return None


def recent(n=50, decision=None):
    with _LOCK:
        rows = list(_MEM)
    if decision:
        rows = [r for r in rows if r.get("decision") == decision]
    return rows[:n]


def _today_utc(now=None):
    return _time.strftime("%Y-%m-%d", _time.gmtime(now))


def summary_today(now=None):
    day = _today_utc(now)
    with _LOCK:
        rows = [r for r in _MEM if _time.strftime("%Y-%m-%d", _time.gmtime(r.get("ts", 0))) == day]
    counts = {"auto": 0, "pending": 0, "rejected": 0, "total": len(rows)}
    reasons = {}
    for r in rows:
        counts[r.get("decision", "rejected")] = counts.get(r.get("decision", "rejected"), 0) + 1
        rea = r.get("reason", "")
        reasons[rea] = reasons.get(rea, 0) + 1
    top = sorted(reasons.items(), key=lambda kv: -kv[1])[:5]
    counts["top_reasons"] = top
    return counts


def export_md(n=200):
    rows = recent(n)
    if not rows:
        return "# Journal de décision\n\n_Aucune décision enregistrée._\n"
    out = ["# Journal de décision (dernières %d)" % len(rows), "",
           "| Heure (UTC) | Instrument | Sens | Conf | Risque | Décision | Motif |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        t = _time.strftime("%m-%d %H:%M", _time.gmtime(r.get("ts", 0)))
        out.append("| %s | %s | %s | %d%% | %s | %s | %s |" % (
            t, r.get("instrument", "?"), r.get("side", ""),
            round(r.get("confidence", 0) * 100), r.get("risk", 0),
            r.get("decision", ""), r.get("reason", "").replace("|", "/")))
    return "\n".join(out) + "\n"


_load()
