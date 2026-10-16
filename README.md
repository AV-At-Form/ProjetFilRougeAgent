# Assistant IA pour le service Achats

Ce projet fournit un assistant conversationnel en français pour consulter le catalogue de produits et les fiches fournisseurs.

## Fonctionnalités

- **Recherche SQL en lecture seule** : consultation des produits par fournisseur ou par prix, vérification du stock et statistiques par fournisseur. Les commandes d’écriture sont refusées.
- **Recherche RAG dans les fiches fournisseurs** : lecture des PDF du répertoire `fiches_fournisseurs`, découpage du texte et recherche sémantique. Les réponses peuvent citer le nom de la fiche et la page.
- **Embeddings locaux** : le modèle multilingue Sentence Transformers calcule les embeddings sur la machine. Aucun service Azure n’est utilisé pour cette étape.
- **Interface web** : fil de discussion avec historique par conversation, rendu Markdown, indication des outils employés et bouton **Nouvelle conversation**.

L’historique et les conversations sont conservés en mémoire par le serveur et sont perdus à son arrêt.

## Prérequis

- Windows et Python (3.11 ou plus récent recommandé).
- MySQL ou MariaDB.
- Un endpoint Azure OpenAI configuré avec un déploiement de modèle de conversation. Azure est utilisé pour générer les réponses de l’assistant, mais pas pour calculer les embeddings.

## Installation

Ouvrez PowerShell à la racine du projet et créez l’environnement virtuel :

```powershell
python -m venv .venv
```

Activez-le :

```powershell
.\.venv\Scripts\Activate.ps1
```

Si PowerShell bloque l’activation des scripts, autorisez-les uniquement pour la session en cours puis réessayez :

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Mettez `pip` à niveau et installez les dépendances :

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Le téléchargement de `sentence-transformers` et de son modèle d’embeddings peut prendre du temps et de l’espace disque.

## Configuration

Créez un fichier `.env` à la racine du projet, à côté de `requirements.txt`, puis renseignez les paramètres nécessaires :

```dotenv
AZURE_OPENAI_ENDPOINT=https://<votre-ressource>.openai.azure.com
AZURE_OPENAI_API_KEY=<votre-cle>
CHAT_DEPLOYMENT=<nom-du-deploiement-de-chat>

MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=<utilisateur-mysql>
MYSQL_PASSWORD=<mot-de-passe-mysql>
MYSQL_DATABASE=catalogue
```

`MYSQL_DATABASE` doit correspondre à la base créée par les scripts fournis ; sa valeur par défaut dans l’agent est `boutique`, donc configurez-la explicitement à `catalogue` pour cette installation.

Pour une installation personnalisée, le modèle local et le dossier de fiches sont configurables :

```dotenv
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
FICHES_FOURNISSEURS_DIR=fiches_fournisseurs
```

Ces deux variables sont facultatives ; les valeurs ci-dessus sont celles utilisées par défaut.

### Préparer la base de données

Avec un compte administrateur MySQL, exécutez depuis la racine du projet :

1. `sql/00_mysql_base_et_utilisateur.sql` pour créer la base `catalogue` et un compte applicatif en lecture seule.
2. `sql/01_schema.sql` pour créer la table `produits`.
3. `sql/02_donnees.sql` pour insérer les données d’exemple.

Vérifiez que les identifiants de l’utilisateur applicatif dans `.env` correspondent à votre configuration MySQL. Ne stockez pas de secrets dans le code source.

## Lancer l’application

Depuis la racine du projet, avec l’environnement virtuel activé :

```powershell
python -m uvicorn app:app --app-dir src --host 127.0.0.1 --port 8000
```

Ouvrez ensuite [http://127.0.0.1:8000](http://127.0.0.1:8000) dans votre navigateur. Le modèle d’embeddings local et les PDF sont chargés au démarrage de l’application. Le premier chargement du modèle peut nécessiter un accès Internet si celui-ci n’est pas déjà en cache.

Pour arrêter le serveur, utilisez `Ctrl+C` dans le terminal.

## Lancer les tests

Les tests unitaires utilisent des mocks et un encodeur factice : ils ne contactent ni Azure OpenAI ni une base MySQL réelle.

```powershell
python -m pytest tests
```

## Pipeline Azure DevOps

Le fichier `azure-pipelines.yml` définit trois stages, exécutés dans l’ordre :

1. **Dev** : vérifie la syntaxe des modules Python de l’application.
2. **Test** : installe les dépendances, exécute les tests pytest et publie leur rapport JUnit dans Azure DevOps.
3. **Lint** : vérifie les erreurs de style et d’analyse statique avec Ruff.

Le pipeline se déclenche automatiquement à chaque push sur une branche. Les stages `Test` et `Lint` ne démarrent que si le stage précédent réussit.

## Structure du projet

```text
.
├── fiches_fournisseurs/   # PDF sources de la recherche RAG
├── sql/                   # Création et alimentation de la base MySQL
├── azure-pipelines.yml    # Pipeline CI Azure DevOps
├── src/
│   ├── agent.py           # Construction de l’agent et outils SQL
│   ├── app.py             # API FastAPI et interface web
│   ├── config.py          # Configuration du service de conversation Azure
│   ├── rag.py             # Découpage PDF, embeddings locaux et recherche sémantique
│   └── static/
│       └── index.html     # Interface utilisateur
├── tests/                 # Tests unitaires SQL et RAG
└── requirements.txt
```
