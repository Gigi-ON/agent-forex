"""
Couche de récap / notification.

Compose un résumé lisible (posture du jour + post-mortem) et l'envoie vers
un ou plusieurs canaux : console, fichier, ou webhook (Slack/Discord/Telegram
via une simple URL). Déterministe, sans dépendance lourde.

C'est l'équivalent du "daily recap" de la vidéo, mais alimenté par nos
chiffres réels (journal, espérance en R), pas par une narration du LLM.
"""

from datetime import datetime, timezone


def compose_daily(posture: dict, postmortem_summary: str,
                  intents: list = None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"📊 Récap quotidien — {now}",
        "",
        "Posture du marché :",
        f"  · Sessions actives : {', '.join(posture.get('sessions_actives', []))}",
        f"  · Régime macro     : {posture.get('regime_macro', 'n/d')}",
        f"  · Prudence         : {posture.get('prudence', 1.0)}",
        f"  · Paires bloquées  : {', '.join(posture.get('paires_bloquees')) or 'aucune'}",
        f"  · Nouvelles entrées: {posture.get('nouvelles_entrees', 'oui')}",
    ]
    if intents:
        lines += ["", "Décisions du scan :"]
        for it in intents:
            if it.get("action") == "ordre":
                lines.append(
                    f"  · {it['pair']} {it['sens']} {it['unites']} u — "
                    f"risque {it['risque']} | conf {it['confiance']}")
            else:
                lines.append(f"  · {it['pair']} : {it['action']} ({it.get('raison','')})")
    lines += ["", "Post-mortem du journal :", postmortem_summary,
              "", "Mode practice — aucun ordre réel envoyé."]
    return "\n".join(lines)


def send(text: str, to_file: str = None, webhook_url: str = None):
    """Envoie le récap. Toujours affiché ; fichier et webhook optionnels."""
    print(text)
    if to_file:
        with open(to_file, "a", encoding="utf-8") as f:
            f.write(text + "\n\n" + "=" * 60 + "\n\n")
    if webhook_url:
        try:
            import requests
            requests.post(webhook_url, json={"text": text}, timeout=10)
        except Exception as e:
            print(f"[recap] envoi webhook échoué : {e}")
