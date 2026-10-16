import asyncio

import pytest

import agent
from rag import Fragment, RechercheFichesFournisseurs, decouper_texte


def test_produits_par_fournisseur_utilise_un_parametre_sql(monkeypatch):
    appels = []

    def executer(sql, parametres=()):
        appels.append((sql, parametres))
        return '[{"libelle": "Clavier"}]'

    monkeypatch.setattr(agent, "executer_requete", executer)

    resultat = agent.SQLPlugin().produits_par_fournisseur("  NovaTech  ")

    assert resultat == '[{"libelle": "Clavier"}]'
    assert len(appels) == 1
    sql, parametres = appels[0]
    assert "WHERE fournisseur = %s" in sql
    assert "LIMIT %s" in sql
    assert parametres == ("NovaTech", agent.LIMITE_RESULTATS_SQL)
    assert "NovaTech" not in sql


def test_produits_par_prix_transmet_le_prix_et_refuse_un_prix_negatif(monkeypatch):
    appels = []

    def executer(sql, parametres=()):
        appels.append((sql, parametres))
        return "résultats"

    monkeypatch.setattr(agent, "executer_requete", executer)
    plugin = agent.SQLPlugin()

    assert plugin.produits_par_prix(125.5) == "résultats"
    assert appels[0][1] == (125.5, agent.LIMITE_RESULTATS_SQL)
    assert "prix <= %s" in appels[0][0]
    assert "LIMIT %s" in appels[0][0]

    assert plugin.produits_par_prix(-1) == "Le prix maximum doit être supérieur ou égal à 0."
    assert len(appels) == 1


def test_stock_par_produit_encode_le_terme_dans_un_parametre(monkeypatch):
    appels = []

    def executer(sql, parametres=()):
        appels.append((sql, parametres))
        return "résultats"

    monkeypatch.setattr(agent, "executer_requete", executer)

    assert agent.SQLPlugin().stock_par_produit("  souris  ") == "résultats"
    assert "libelle LIKE %s" in appels[0][0]
    assert "LIMIT %s" in appels[0][0]
    assert appels[0][1] == ("%souris%", agent.LIMITE_RESULTATS_SQL)


def test_stock_par_produit_vide_ne_consulte_pas_la_base(monkeypatch):
    def executer(*args, **kwargs):
        pytest.fail("La base ne doit pas être consultée pour un nom vide.")

    monkeypatch.setattr(agent, "executer_requete", executer)

    assert agent.SQLPlugin().stock_par_produit("   ") == "Veuillez préciser le nom du produit."


def test_statistiques_execute_une_requete_agregee_en_lecture_seule(monkeypatch):
    appels = []

    def executer(sql, parametres=()):
        appels.append((sql, parametres))
        return "statistiques"

    monkeypatch.setattr(agent, "executer_requete", executer)

    assert agent.SQLPlugin().statistiques() == "statistiques"
    sql, parametres = appels[0]
    assert sql.lstrip().startswith("SELECT")
    assert "COUNT(*)" in sql
    assert "AVG(prix)" in sql
    assert "LIMIT %s" in sql
    assert parametres == (agent.LIMITE_RESULTATS_SQL,)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO produits (libelle) VALUES ('intrus')",
        "UPDATE produits SET stock = 0",
        "DELETE FROM produits",
        "DROP TABLE produits",
        "SELECT libelle FROM produits; DELETE FROM produits",
        "SELECT libelle FROM produits INTO OUTFILE 'export.csv'",
    ],
)
def test_executer_requete_refuse_toute_ecriture_avant_connexion(monkeypatch, sql):
    def connecter(**kwargs):
        pytest.fail("Une requête refusée ne doit pas ouvrir de connexion MySQL.")

    monkeypatch.setattr(agent.mysql.connector, "connect", connecter)

    with pytest.raises(ValueError, match="SELECT en lecture seule"):
        agent.executer_requete(sql)


def test_decouper_texte_conserve_un_chevauchement():
    texte = "a" * 2500

    fragments = decouper_texte(texte)

    assert [len(fragment) for fragment in fragments] == [1000, 1000, 800]
    assert fragments[0][-150:] == fragments[1][:150]
    assert fragments[1][-150:] == fragments[2][:150]
    assert "".join(
        fragment if index == 0 else fragment[150:]
        for index, fragment in enumerate(fragments)
    ) == texte


def test_decouper_texte_ignore_le_texte_vide():
    assert decouper_texte(" \n\t ") == []


class EncodeurFactice:
    def encode(self, texte, **kwargs):
        if isinstance(texte, str):
            textes = [texte]
            scalaire = True
        else:
            textes = texte
            scalaire = False

        vecteurs = [
            [1.0, 0.0] if "clavier" in element.lower() else [0.0, 1.0]
            for element in textes
        ]
        return vecteurs[0] if scalaire else vecteurs


def test_recherche_rag_retourne_le_passage_le_plus_proche_et_sa_source():
    recherche = RechercheFichesFournisseurs(
        modele_embeddings=EncodeurFactice(),
        dossier="fiches_fournisseurs",
    )
    recherche.fragments = [
        Fragment(
            source="FRN-001_NovaTech.pdf, page 2",
            texte="Clavier mécanique avec touches silencieuses.",
            embedding=[1.0, 0.0],
        ),
        Fragment(
            source="FRN-002_Papyrus.pdf, page 1",
            texte="Papier recyclé pour imprimante.",
            embedding=[0.0, 1.0],
        ),
    ]

    resultat = asyncio.run(recherche.rechercher_fiches("clavier"))

    assert "FRN-001_NovaTech.pdf, page 2" in resultat
    assert "Clavier mécanique" in resultat
    assert resultat.index("FRN-001_NovaTech.pdf") < resultat.index("FRN-002_Papyrus.pdf")
