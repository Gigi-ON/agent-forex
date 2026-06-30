"""
Copilote IA (OpenRouter / Grok) — ANALYSTE, jamais trader.

LIGNE ROUGE : ce module n'exécute AUCUN ordre, ne modifie AUCUN réglage. Il lit
le journal / la calibration / les réglages courants et PROPOSE des ajustements
que l'humain validera. Il est strictement HORS du chemin de décision/exécution.
Aucun outil n'est exposé au LLM : sa sortie est du texte consultatif, jamais
exécuté. Le client réseau est injectable (tests sans appel réel).
"""
import json

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = (
    "Tu es un analyste de stratégie de trading pour une plateforme de PAPER-TRADING "
    "déterministe (forex + crypto). RÈGLES STRICTES ET NON NÉGOCIABLES : tu n'exécutes "
    "JAMAIS d'ordre, tu ne modifies RIEN toi-même. Tu analyses le journal et tu PROPOSES "
    "des ajustements de paramètres (PHASE1 = qualité de signal ; PHASE2 = survie/portefeuille) "
    "que l'humain validera et appliquera lui-même. Priorise l'ESPÉRANCE (R-multiple) et la "
    "SURVIE plutôt que le taux de réussite brut. Sois concret, concis, chiffré. Réponds en "
    "français et termine TOUJOURS par une section « À valider » listant tes propositions."
)


def ask(messages, model=None, session=None, timeout=45):
    import config
    key = getattr(config, "OPENROUTER_API_KEY", "")
    if not key:
        return {"error": "OPENROUTER_API_KEY manquante : ajoute-la au .env du VPS."}
    model = model or getattr(config, "OPENROUTER_MODEL", "x-ai/grok-2-1212")
    payload = {"model": model, "messages": messages, "temperature": 0.3}
    headers = {"Authorization": "Bearer " + key,
               "HTTP-Referer": "https://agent-forex.unidevlabs.com",
               "X-Title": "agent-forex"}
    try:
        if session is not None:
            r = session.post(ENDPOINT, json=payload, headers=headers)
        else:
            import requests
            r = requests.post(ENDPOINT, json=payload, headers=headers, timeout=timeout)
        d = r.json() if hasattr(r, "json") else r
        if isinstance(d, dict) and d.get("error"):
            return {"error": str(d["error"])[:200]}
        txt = d["choices"][0]["message"]["content"]
        return {"answer": txt, "model": model}
    except Exception as e:
        return {"error": str(e)[:200]}


def build_user_prompt(journal, learning, settings, question=None):
    ctx = {"post_mortem": journal, "apprentissage": learning, "reglages_actuels": settings}
    blob = json.dumps(ctx, ensure_ascii=False)[:6000]
    task = question or ("Analyse ce qui marche et ce qui pèche, puis propose 3 à 5 ajustements "
                        "CONCRETS et chiffrés de PHASE1/PHASE2 avec justification (impact attendu "
                        "sur espérance et drawdown). N'invente pas de données.")
    return "État du bot (données réelles) :\n" + blob + "\n\nTâche : " + task


def analyze(journal, learning, settings, question=None, session=None, model=None):
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": build_user_prompt(journal, learning, settings, question)}]
    return ask(msgs, model=model, session=session)
