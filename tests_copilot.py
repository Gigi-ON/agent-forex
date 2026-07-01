"""Tests Lot 4 — copilote Grok (OpenRouter), hors chemin d'exécution.
python3 tests_copilot.py"""
import copilot
import config


class Resp:
    def __init__(self, p): self._p = p
    def json(self): return self._p


class FakeSess:
    def __init__(self): self.calls = []
    def post(self, url, json=None, headers=None, **k):
        self.calls.append((url, json, headers))
        return Resp({"choices": [{"message": {"content": "Analyse… À valider : baisser adx_min à 18."}}]})


def t_no_key():
    old = config.OPENROUTER_API_KEY; config.OPENROUTER_API_KEY = ""
    try:
        r = copilot.ask([{"role": "user", "content": "x"}])
        assert "error" in r and "OPENROUTER_API_KEY" in r["error"]
    finally:
        config.OPENROUTER_API_KEY = old
    print("OK garde clé manquante")


def t_ask():
    old = config.OPENROUTER_API_KEY; config.OPENROUTER_API_KEY = "sk-test"
    fs = FakeSess()
    try:
        r = copilot.ask([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                        model="x-ai/grok-2-1212", session=fs)
        assert r.get("answer", "").startswith("Analyse")
        url, payload, headers = fs.calls[-1]
        assert "openrouter.ai" in url and payload["model"] == "x-ai/grok-2-1212"
        assert payload["messages"][0]["role"] == "system" and payload["temperature"] == 0.3
        assert headers["Authorization"] == "Bearer sk-test"
    finally:
        config.OPENROUTER_API_KEY = old
    print("OK ask (payload + auth + parsing réponse)")


def t_analyze():
    old = config.OPENROUTER_API_KEY; config.OPENROUTER_API_KEY = "sk-test"
    fs = FakeSess()
    try:
        r = copilot.analyze({"win_rate": 0.5, "trades": 10}, {"bands": []},
                            {"PHASE1": {"adx_min": 20}}, question="Pourquoi ça perd ?", session=fs)
        _, payload, _ = fs.calls[-1]
        u = payload["messages"][1]["content"]
        assert "adx_min" in u and "Pourquoi ça perd ?" in u
        assert "À valider" in copilot.SYSTEM and "n'exécute" in copilot.SYSTEM
        assert r.get("answer")
    finally:
        config.OPENROUTER_API_KEY = old
    print("OK analyze (contexte + question dans le prompt, system strict)")


def t_reasoning_passthrough():
    """reasoning n'est ajoute au payload que s'il est fourni (Ingenieur uniquement)."""
    old = config.OPENROUTER_API_KEY; config.OPENROUTER_API_KEY = "sk-test"
    try:
        fs = FakeSess()
        copilot.ask([{"role": "user", "content": "u"}], session=fs)
        assert "reasoning" not in fs.calls[-1][1]                      # defaut : pas de reasoning
        fs2 = FakeSess()
        copilot.ask([{"role": "user", "content": "u"}], session=fs2, reasoning={"enabled": True})
        assert fs2.calls[-1][1].get("reasoning") == {"enabled": True}  # fourni : present
    finally:
        config.OPENROUTER_API_KEY = old
    print("OK reasoning (absent par defaut, present si demande)")


if __name__ == "__main__":
    t_no_key(); t_ask(); t_analyze(); t_reasoning_passthrough()
    print("\n=== Lot 4 (copilote Grok) : tous les tests passent ===")
