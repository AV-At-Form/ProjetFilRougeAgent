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
from pydantic import BaseModel, Field
from semantic_kernel.agents.chat_completion.chat_completion_agent import ChatHistoryAgentThread
from semantic_kernel.contents.function_call_content import FunctionCallContent
from semantic_kernel.contents.function_result_content import FunctionResultContent

from agent import SYSTEM_PROMPT, creer_agent


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
    if not contenu:
        raise HTTPException(status_code=422, detail="Le message ne peut pas être vide.")

    async with conversation["lock"]:
        debut_historique = len(conversation["thread"]) if conversation["thread"] else 0
        reponse = await app.state.agent.get_response(
            messages=contenu,
            thread=conversation["thread"],
        )
        conversation["thread"] = reponse.thread
        contenu_reponse = reponse.content.content or ""
        outils = await outils_utilises(reponse.thread, debut_historique)
        message_utilisateur: Message = {"role": "user", "content": contenu}
        message_assistant: Message = {"role": "assistant", "content": contenu_reponse}
        conversation["history"].extend([message_utilisateur, message_assistant])

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
