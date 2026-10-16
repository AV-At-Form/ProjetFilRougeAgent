import pytest

import app
from app import HISTORIQUE_MAX_TOURS, tronquer_historique


def test_tronquer_historique_conserve_systeme_et_dernieres_conversations():
    historique = [{"role": "system", "content": "instructions"}]
    for numero in range(1, 13):
        historique.extend(
            [
                {"role": "user", "content": f"question {numero}"},
                {"role": "assistant", "content": f"réponse {numero}"},
            ]
        )

    resultat = tronquer_historique(historique, max_tours=3)

    assert resultat[0] == {"role": "system", "content": "instructions"}
    assert resultat[1:] == historique[-6:]
    assert len(resultat) == 1 + 2 * 3


def test_tronquer_historique_ne_depasse_pas_la_limite_par_defaut():
    historique = [{"role": "system", "content": "instructions"}]
    for numero in range(HISTORIQUE_MAX_TOURS + 2):
        historique.extend(
            [
                {"role": "user", "content": f"question {numero}"},
                {"role": "assistant", "content": f"réponse {numero}"},
            ]
        )

    resultat = tronquer_historique(historique)

    assert resultat[0]["role"] == "system"
    assert len(resultat[1:]) == 2 * HISTORIQUE_MAX_TOURS
    assert resultat[1]["content"] == "question 2"


def test_tronquer_historique_refuse_une_limite_invalide():
    with pytest.raises(ValueError, match="supérieur ou égal à 1"):
        tronquer_historique([], max_tours=0)


def test_reponse_agent_reconstruit_le_thread_avec_un_historique_borne():
    class AgentFactice:
        def __init__(self):
            self.messages_recus = []

        async def get_response(self, messages, thread):
            async for message in thread.get_messages():
                self.messages_recus.append((message.role.value, message.content))
            return type(
                "Reponse",
                (),
                {
                    "thread": thread,
                    "content": type("Contenu", (), {"content": "réponse courante"})(),
                },
            )()

    async def verifier():
        agent = AgentFactice()
        app.app.state.agent = agent
        app.app.state.conversations = {}
        conversation = await app.nouvelle_conversation()
        historique = app.app.state.conversations[conversation["id"]]["history"]
        for numero in range(HISTORIQUE_MAX_TOURS + 2):
            historique.extend(
                [
                    {"role": "user", "content": f"question {numero}"},
                    {"role": "assistant", "content": f"réponse {numero}"},
                ]
            )

        await app.envoyer_message(
            conversation["id"],
            app.MessageRequest(content="question actuelle"),
        )

        historique = app.app.state.conversations[conversation["id"]]["history"]
        assert len(agent.messages_recus) == 2 * HISTORIQUE_MAX_TOURS
        assert agent.messages_recus[0] == ("user", "question 2")
        assert agent.messages_recus[-1] == ("assistant", "réponse 11")
        assert len(historique) <= 1 + 2 * HISTORIQUE_MAX_TOURS
        assert historique[0]["role"] == "system"

    import asyncio

    asyncio.run(verifier())
