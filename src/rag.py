"""Recherche sémantique dans les fiches PDF des fournisseurs."""
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
from semantic_kernel.functions import kernel_function

load_dotenv()

RACINE_PROJET = Path(__file__).resolve().parent.parent
DOSSIER_DEFAUT = RACINE_PROJET / "fiches_fournisseurs"
TAILLE_FRAGMENT = 1000
CHEVAUCHEMENT = 150
TAILLE_LOT_EMBEDDING = 32
MODELE_EMBEDDING_DEFAUT = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@dataclass
class Fragment:
    source: str
    texte: str
    embedding: list[float]


def creer_modele_embeddings() -> SentenceTransformer:
    """Charge localement un modèle multilingue, sans appel à un service Azure."""
    nom_modele = os.getenv("EMBEDDING_MODEL", MODELE_EMBEDDING_DEFAUT)
    print(f"[RAG] Chargement du modèle local d'embeddings : {nom_modele}", flush=True)
    return SentenceTransformer(nom_modele)


def decouper_texte(texte: str) -> list[str]:
    """Découpe le texte en fragments avec chevauchement pour préserver le contexte."""
    fragments = []
    debut = 0

    while debut < len(texte):
        fin = min(debut + TAILLE_FRAGMENT, len(texte))
        if fin < len(texte):
            limite_mot = texte.rfind(" ", debut + TAILLE_FRAGMENT * 3 // 4, fin)
            if limite_mot > debut:
                fin = limite_mot

        fragment = texte[debut:fin].strip()
        if fragment:
            fragments.append(fragment)
        if fin >= len(texte):
            break
        debut = max(debut + 1, fin - CHEVAUCHEMENT)

    return fragments


class RechercheFichesFournisseurs:
    """Charge les fiches PDF et expose leur recherche comme outil Semantic Kernel."""

    def __init__(self, modele_embeddings: SentenceTransformer, dossier: str | Path | None = None):
        chemin = Path(
            dossier
            if dossier is not None
            else os.getenv("FICHES_FOURNISSEURS_DIR", str(DOSSIER_DEFAUT))
        ).expanduser()
        self.dossier = chemin if chemin.is_absolute() else RACINE_PROJET / chemin
        self.modele_embeddings = modele_embeddings
        self.fragments: list[Fragment] = []

    async def charger(self) -> int:
        """Extrait, découpe et vectorise toutes les pages PDF du répertoire."""
        if not self.dossier.is_dir():
            raise FileNotFoundError(f"Le répertoire des fiches PDF n'existe pas : {self.dossier}")

        fragments_a_vectoriser: list[tuple[str, str]] = []
        fichiers = sorted(
            fichier for fichier in self.dossier.rglob("*")
            if fichier.is_file() and fichier.suffix.casefold() == ".pdf"
        )
        for fichier in fichiers:
            lecteur = PdfReader(fichier)
            for numero_page, page in enumerate(lecteur.pages, start=1):
                texte = page.extract_text() or ""
                source = f"{fichier.name}, page {numero_page}"
                fragments_a_vectoriser.extend(
                    (source, fragment) for fragment in decouper_texte(texte)
                )

        if not fragments_a_vectoriser:
            raise ValueError(f"Aucun texte exploitable trouvé dans les PDF de {self.dossier}")

        vecteurs = await asyncio.to_thread(
            self.modele_embeddings.encode,
            [texte for _, texte in fragments_a_vectoriser],
            batch_size=TAILLE_LOT_EMBEDDING,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        self.fragments = [
            Fragment(source, texte, [float(valeur) for valeur in vecteur])
            for (source, texte), vecteur in zip(fragments_a_vectoriser, vecteurs, strict=True)
        ]
        print(
            f"[RAG] {len(self.fragments)} fragments chargés depuis "
            f"{len(fichiers)} PDF ({self.dossier}).",
            flush=True,
        )
        return len(self.fragments)

    @staticmethod
    def _similarite_cosinus(vecteur_a: list[float], vecteur_b: list[float]) -> float:
        produit = sum(a * b for a, b in zip(vecteur_a, vecteur_b, strict=True))
        norme_a = sum(valeur * valeur for valeur in vecteur_a) ** 0.5
        norme_b = sum(valeur * valeur for valeur in vecteur_b) ** 0.5
        if not norme_a or not norme_b:
            return 0.0
        return produit / (norme_a * norme_b)

    @kernel_function(
        description=(
            "Recherche dans les fiches PDF les informations sémantiquement pertinentes sur "
            "les fournisseurs, leurs produits, caractéristiques, services et conditions. "
            "Les résultats indiquent le nom de la fiche et la page à citer."
        )
    )
    async def rechercher_fiches(
        self,
        question: Annotated[str, "Question à rechercher dans les fiches fournisseurs"],
    ) -> str:
        """Retourne les passages PDF les plus proches de la question avec leurs sources."""
        question = question.strip()
        if not question:
            return "Veuillez préciser les informations à rechercher dans les fiches."
        if not self.fragments:
            raise RuntimeError("Les fiches PDF n'ont pas été chargées avant la recherche.")

        vecteur_question = await asyncio.to_thread(
            self.modele_embeddings.encode,
            question,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        embedding_question = [float(valeur) for valeur in vecteur_question]
        resultats = sorted(
            (
                (self._similarite_cosinus(embedding_question, fragment.embedding), fragment)
                for fragment in self.fragments
            ),
            key=lambda resultat: resultat[0],
            reverse=True,
        )[:3]

        print(
            "[OUTIL] rechercher_fiches appelé "
            f"(question={question!r}, sources={[fragment.source for _, fragment in resultats]!r})",
            flush=True,
        )
        return "\n\n".join(
            f"[Source : {fragment.source} | pertinence : {score:.2f}]\n{fragment.texte}"
            for score, fragment in resultats
        )
