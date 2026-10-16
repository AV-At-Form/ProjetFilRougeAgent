# config.py
# Crée le "Kernel" de Semantic Kernel, connecté à notre déploiement Azure OpenAI.
# Tous les scripts du projet l'importent : from config import creer_kernel
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion

load_dotenv()


def creer_kernel():
    
    client = AsyncOpenAI(
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/") + "/openai/v1/",
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )

    # Le "service" = le LLM que le Kernel va utiliser
    service = OpenAIChatCompletion(
        ai_model_id=os.environ["CHAT_DEPLOYMENT"],  # le NOM DU DÉPLOIEMENT
        async_client=client,
    )

    # Le Kernel = le chef d'orchestre : il connaît le LLM et les plugins
    kernel = Kernel()
    kernel.add_service(service)
    return kernel
