"""
DÉMO hors-ligne du bot sous tutelle (aucun réseau).

Montre :
  - l'allocation de capital par session (solde commun / disponible),
  - des propositions générées par session,
  - validation manuelle, auto, rejet, et expiration (inaction),
  - le retour du capital ± résultat au solde commun à la clôture.

Lancer :  python supervisor_demo.py
"""

from datetime import datetime, timedelta, timezone

from session import SessionManager, Tutelle
from supervisor import Supervisor
from risk_manager import Profile
from alerts import AlertSink
from journal import JournalStore
from data_demo import make_m15

NOW = datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc)


def main():
    candles = make_m15()
    mgr = SessionManager(starting_balance=10000.0)
    sink = AlertSink()
    store = JournalStore(db_path="data/demo_supervisor.db")
    store.conn.execute("DELETE FROM trades")
    sup = Supervisor(mgr, journal_store=store, alert_sink=sink)

    print(f"Solde commun : {mgr.balance} CAD · disponible : {mgr.available} CAD\n")

    # 1) Ouvrir 3 sessions (sur 5 possibles), avec budgets et tutelles variés.
    s1 = mgr.open_session(2000, profile=Profile.RESERVE, tutelle=Tutelle.MANUEL, duration_min=120)
    s2 = mgr.open_session(1500, profile=Profile.DOUX, tutelle=Tutelle.AUTO,
                          duration_min=90, risk_level="doux")
    s3 = mgr.open_session(3000, profile=Profile.DOUX, tutelle=Tutelle.MANUEL, duration_min=240)
    print(f"3 sessions ouvertes. Réservé : {mgr.reserved} · disponible : {mgr.available}")
    for s in mgr.active:
        print(f"  · {s.id} | budget {s.allocated} | {s.tutelle.value} | "
              f"expire {s.duration_min} min")

    # 2) Générer une proposition par session.
    print("\nPropositions générées :")
    pends = []
    for s in (s1, s2, s3):
        q2a = 1.36
        p = sup.propose(s, "EUR_USD", candles, news_items=[],
                        quote_to_account=q2a, base_to_account=1.47, now=NOW)
        if p:
            pends.append(p)
            print(f"  · session {s.id} ({s.tutelle.value}) -> {p.units} u, "
                  f"risque {p.risk}, conf {p.confidence}, statut {p.status}")
        else:
            print(f"  · session {s.id} -> aucune proposition")

    # 3) Décisions humaines sur les sessions MANUEL.
    manual = [p for p in pends if p.status == "pending"]
    if manual:
        sup.approve(manual[0].id, now=NOW)                 # on approuve la 1re
        print(f"\nApprouvé manuellement : {manual[0].id} ({manual[0].pair})")
    if len(manual) > 1:
        # la 2e : on ne répond pas -> expiration après 45 s
        later = NOW + timedelta(seconds=46)
        sup.sweep(now=later)
        print(f"Laissé expirer : {manual[1].id} -> statut {manual[1].status}")

    # 4) Simuler le résultat des trades approuvés (pour voir la comptabilité).
    approved = [p for p in pends if p.status == "approved"]
    for p in approved:
        # résultat fictif : +1,9R (gain) sur la session correspondante
        gain = round(p.risk * 1.9, 2)
        mgr.record_trade_pnl(p.session_id, gain)
        print(f"Trade {p.pair} (session {p.session_id}) clôturé : +{gain} CAD")

    # 5) Clôturer une session -> rend le budget + résultat au solde commun.
    mgr.close_session(s1.id)
    print(f"\nSession {s1.id} clôturée. Résultat : {s1.realized_pnl:+.2f} CAD")
    print(f"Solde commun : {mgr.balance} CAD · disponible : {mgr.available} CAD")

    # 6) Boîte de réception in-app (ce que le dashboard afficherait).
    print(f"\nMessagerie in-app : {len(sink.inbox)} message(s)")
    for a in sink.inbox:
        print(f"  [{a.kind}] {a.title}")
    store.close()


if __name__ == "__main__":
    main()
