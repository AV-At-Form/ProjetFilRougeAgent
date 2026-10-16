"""Interface web et API de conversation de l'agent d'achat."""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

import bleach
import markdown
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from openai import BadRequestError, RateLimitError
from pydantic import BaseModel, Field
from semantic_kernel.agents.chat_completion.chat_completion_agent import ChatHistoryAgentThread
from semantic_kernel.contents.chat_history import ChatHistory
from semantic_kernel.contents.chat_message_content import ChatMessageContent
from semantic_kernel.contents.function_call_content import FunctionCallContent
from semantic_kernel.contents.function_result_content import FunctionResultContent
from semantic_kernel.contents.utils.author_role import AuthorRole

from agent import SYSTEM_PROMPT, creer_agent
from securite import LimiteurDeDebit, filtrer_sortie, verifier_entree


class Message(TypedDict):
    role: str
    content: str


class ToolUse(TypedDict):
    name: str


class Conversation(TypedDict):
    history: list[Message]
    thread: ChatHistoryAgentThread | None
    lock: asyncio.Lock


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)


HISTORIQUE_MAX_TOURS = 10
limiteur = LimiteurDeDebit(maximum=3, secondes=60)
FICHIER_INTERFACE = Path(__file__).resolve().parent / "static" / "index.html"
BALISES_MARKDOWN_AUTORISEES = set(bleach.sanitizer.ALLOWED_TAGS).union(
    {
        "p", "pre", "code", "blockquote", "h1", "h2", "h3", "h4",
        "hr", "br", "ul", "ol", "li", "table", "thead", "tbody",
        "tr", "th", "td", "strong", "em", "del",
    }
)


def rendre_markdown_sain(contenu: str) -> str:
    """Convertit le Markdown en HTML en supprimant les balises et attributs dangereux."""
    rendu = markdown.markdown(
        contenu,
        extensions=["fenced_code", "tables", "nl2br"],
    )
    return bleach.clean(
        rendu,
        tags=BALISES_MARKDOWN_AUTORISEES,
        attributes={"a": ["href", "title"], "code": ["class"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.agent = await creer_agent()
    application.state.conversations = {}
    yield


app = FastAPI(title="Assistant Achats", lifespan=lifespan)


def tronquer_historique(historique: list[Message], max_tours: int = HISTORIQUE_MAX_TOURS) -> list[Message]:
    """Garde les messages système et les derniers échanges utilisateur/assistant."""
    if max_tours < 1:
        raise ValueError("max_tours doit être supérieur ou égal à 1.")

    messages_systeme = [message for message in historique if message["role"] == "system"]
    messages_conversation = [message for message in historique if message["role"] != "system"]
    indices_utilisateur = [
        index
        for index, message in enumerate(messages_conversation)
        if message["role"] == "user"
    ]
    if len(indices_utilisateur) <= max_tours:
        return messages_systeme + messages_conversation

    debut = indices_utilisateur[-max_tours]
    return messages_systeme + messages_conversation[debut:]


async def outils_utilises(
    thread: ChatHistoryAgentThread,
    debut_historique: int,
) -> list[ToolUse]:
    """Récupère les noms des fonctions appelées pendant le dernier échange."""
    nouveaux_messages = [
        message async for message in thread.get_messages()
    ][debut_historique:]
    outils: list[ToolUse] = []
    noms_deja_ajoutes: set[str] = set()

    for message in nouveaux_messages:
        for item in message.items:
            if not isinstance(item, (FunctionCallContent, FunctionResultContent)):
                continue
            nom = (
                f"{item.plugin_name}.{item.function_name}"
                if item.plugin_name and item.function_name
                else item.name or item.function_name
            )
            if nom and nom not in noms_deja_ajoutes:
                outils.append({"name": nom})
                noms_deja_ajoutes.add(nom)

    return outils


@app.get("/", response_class=HTMLResponse)
async def interface_web() -> HTMLResponse:
    return HTMLResponse(FICHIER_INTERFACE.read_text(encoding="utf-8"))


@app.post("/api/conversations")
async def nouvelle_conversation() -> dict[str, object]:
    conversation_id = str(uuid4())
    historique: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]
    conversations: dict[str, Conversation] = app.state.conversations
    conversations[conversation_id] = {
        "history": historique,
        "thread": None,
        "lock": asyncio.Lock(),
    }
    return {"id": conversation_id, "history": historique}


@app.get("/api/conversations/{conversation_id}")
async def lire_conversation(conversation_id: str) -> dict[str, object]:
    conversation = app.state.conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return {"id": conversation_id, "history": conversation["history"]}


@app.post("/api/conversations/{conversation_id}/messages")
async def envoyer_message(conversation_id: str, demande: MessageRequest) -> dict[str, object]:
    conversation = app.state.conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    contenu = demande.content.strip()
    entree_valide, raison = verifier_entree(contenu)
    if not entree_valide:
        raise HTTPException(status_code=400, detail=raison)
    if not limiteur.autoriser():
        raise HTTPException(
            status_code=429,
            detail="Trop de requêtes. Veuillez réessayer dans une minute.",
        )

    async with conversation["lock"]:
        historique = tronquer_historique(conversation["history"])
        chat_history = ChatHistory()
        for message in historique:
            if message["role"] == "user":
                chat_history.add_message(
                    ChatMessageContent(role=AuthorRole.USER, content=message["content"])
                )
            elif message["role"] == "assistant":
                chat_history.add_message(
                    ChatMessageContent(role=AuthorRole.ASSISTANT, content=message["content"])
                )

        thread = ChatHistoryAgentThread(chat_history=chat_history)
        debut_historique = len(thread)
        try:
            reponse = await app.state.agent.get_response(
                messages=contenu,
                thread=thread,
            )
        except BadRequestError as erreur:
            if "content_filter" in str(erreur).casefold():
                raise HTTPException(
                    status_code=400,
                    detail="Le contenu du message a été refusé par le filtre de sécurité.",
                ) from erreur
            raise
        except RateLimitError as erreur:
            raise HTTPException(
                status_code=429,
                detail="Le service IA est temporairement limité. Veuillez réessayer plus tard.",
            ) from erreur
        conversation["thread"] = reponse.thread
        contenu_reponse = filtrer_sortie(reponse.content.content or "")
        outils = await outils_utilises(reponse.thread, debut_historique)
        message_utilisateur: Message = {"role": "user", "content": contenu}
        message_assistant: Message = {"role": "assistant", "content": contenu_reponse}
        conversation["history"] = tronquer_historique(
            historique + [message_utilisateur, message_assistant]
        )

    return {
        "user": message_utilisateur,
        "assistant": message_assistant,
        "assistant_html": rendre_markdown_sain(contenu_reponse),
        "tools_used": outils,
    }


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=False,
    )
