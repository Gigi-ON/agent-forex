"""
Couche d'alertes du bot sous tutelle.

Canaux pour commencer : toast (dashboard), messagerie in-app, email.
  - toast / in-app : l'AlertSink accumule les alertes ; le dashboard les lit
    (toast = éphémère, in-app = persistant dans la messagerie).
  - email : envoi SMTP optionnel (identifiants en variables d'environnement),
    pour être prévenu quand on n'est pas devant le dashboard.

Les alertes liées à une validation portent une échéance (minuteur 45 s).
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Alert:
    kind: str                 # "approval" | "info" | "executed" | "expired" | "rejected"
    title: str
    body: str
    session_id: str = ""
    approval_id: str = ""
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    channels: tuple = ("toast", "inapp", "email")


class AlertSink:
    """Collecte les alertes pour le dashboard et route l'email si configuré."""

    def __init__(self, email_to=None):
        self.toasts = []     # éphémères (UI les fait disparaître)
        self.inbox = []      # messagerie in-app (persistant)
        self.email_to = email_to or os.environ.get("ALERT_EMAIL", "")

    def emit(self, alert: Alert):
        if "toast" in alert.channels:
            self.toasts.append(alert)
        if "inapp" in alert.channels:
            self.inbox.append(alert)
        if "email" in alert.channels and self.email_to:
            self._send_email(alert)
        return alert

    def _send_email(self, alert: Alert):
        """Envoi SMTP réel (hors-ligne : ne fait rien, juste un repli silencieux)."""
        host = os.environ.get("SMTP_HOST")
        if not host:
            return  # pas de SMTP configuré -> on n'envoie pas (dashboard suffit)
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText(alert.body)
            msg["Subject"] = f"[Agent Forex] {alert.title}"
            msg["From"] = os.environ.get("SMTP_FROM", "bot@forex-agent.local")
            msg["To"] = self.email_to
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
                s.starttls()
                user = os.environ.get("SMTP_USER")
                if user:
                    s.login(user, os.environ.get("SMTP_PASS", ""))
                s.send_message(msg)
        except Exception as e:
            print(f"[alerts] envoi email échoué : {e}")

    def unread_inapp(self):
        return [a for a in self.inbox if a.kind == "approval"]
