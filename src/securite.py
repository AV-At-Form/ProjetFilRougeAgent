"""Contrôles de sécurité réutilisables par l'interface du chat."""
import re
import threading
import time

LONGUEUR_MAX = 500

PHRASES_SUSPECTES = (
    "ignore tes instructions",
    "ignore les instructions",
    "ignore previous instructions",
    "oublie tes instructions",
    "prompt système",
    "system prompt",
    "tu es maintenant",
    "révèle tes instructions",
)


def verifier_entree(texte: str) -> tuple[bool, str]:
    """Refuse les messages vides, trop longs ou correspondant aux motifs connus."""
    if not texte.strip():
        return False, "question vide"
    if len(texte) > LONGUEUR_MAX:
        return False, f"question trop longue (maximum {LONGUEUR_MAX} caractères)"

    texte_normalise = texte.casefold()
    for phrase in PHRASES_SUSPECTES:
        if phrase in texte_normalise:
            return False, "tentative d'injection de prompt détectée"
    return True, "ok"


class LimiteurDeDebit:
    """Autorise un nombre maximal de requêtes par fenêtre glissante."""

    def __init__(self, maximum: int, secondes: int):
        if maximum < 1 or secondes < 1:
            raise ValueError("maximum et secondes doivent être supérieurs ou égaux à 1.")
        self.maximum = maximum
        self.secondes = secondes
        self.horaires: list[float] = []
        self._verrou = threading.Lock()

    def autoriser(self) -> bool:
        maintenant = time.monotonic()
        with self._verrou:
            self.horaires = [
                horaire for horaire in self.horaires
                if maintenant - horaire < self.secondes
            ]
            if len(self.horaires) >= self.maximum:
                return False
            self.horaires.append(maintenant)
            return True


def filtrer_sortie(texte: str) -> str:
    """Masque les adresses e-mail et les numéros de téléphone dans une réponse."""
    texte = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[e-mail masqué]", texte)
    return re.sub(r"(?:\+?\d[\s.-]?){9,}", "[téléphone masqué]", texte)
