# Checklist de préparation à la production

Cette checklist est basée sur le code Python de `src/`, les tests Python et le pipeline Azure DevOps. Elle ne déduit aucun contrôle ou privilège MySQL à partir de scripts de configuration de base de données.

Légende : `[x]` vérifiable et appliqué, `[~]` partiel ou à confirmer hors du code Python, `[ ]` absent ou à mettre en place.

## Sécurité

### Secrets hors du code

- [~] **Configuration par variables d’environnement** — [config.py](src/config.py) lit `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` et `CHAT_DEPLOYMENT` avec `os.environ`. [agent.py](src/agent.py) lit `MYSQL_USER` et `MYSQL_PASSWORD` depuis l’environnement et charge dotenv via `load_dotenv()`. Cela permet d’injecter les secrets au lieu de les coder dans le chemin d’exécution Python.

### Requêtes paramétrées et SQL libre

- [x] Les outils SQL de [agent.py](src/agent.py) définissent des requêtes fixes et transmettent les valeurs par paramètres `%s`. Exemple : `WHERE prix <= %s ... LIMIT %s`, puis `executer_requete(sql, (prix_max, LIMITE_RESULTATS_SQL))`.
- [x] Les outils exposés à Semantic Kernel acceptent des arguments métiers (`fournisseur`, `prix_max`, `nom_produit`) ; aucun ne demande au modèle de fournir du texte SQL.
- [~] `executer_requete(sql, parametres=())` exécute directement l’argument `sql` qu’il reçoit et n’impose pas, en Python, que celui-ci soit une requête `SELECT`. Les appels actuels visibles dans `SQLPlugin` passent des requêtes fixes, mais le helper lui-même ne garantit pas le caractère lecture seule.
- [~] Les tests d’outils de [test_agent_rag.py](tests/test_agent_rag.py) vérifient les paramètres et les `LIMIT`. Le test `test_executer_requete_refuse_toute_ecriture_avant_connexion` attend un garde-fou qui n’est pas implémenté dans le `executer_requete` Python observé ; il ne prouve donc pas actuellement un refus des requêtes d’écriture.

### Protection contre l’injection de prompt

- [~] Le `SYSTEM_PROMPT` de [agent.py](src/agent.py) indique de traiter les messages utilisateur et les extraits des fiches comme des données non fiables. C’est une consigne au modèle, pas une frontière de sécurité.
- [x] [securite.py](src/securite.py) est branché à [app.py](src/app.py) : `verifier_entree` refuse les messages vides, ceux de plus de 500 caractères et les phrases correspondant aux motifs connus. L’API refuse ces messages avant d’appeler l’agent.
- [~] La détection repose sur une liste de chaînes dans `PHRASES_SUSPECTES`. Elle ne couvre pas nécessairement les reformulations, obfuscations ou langues différentes ; ne pas la considérer comme une protection complète contre les jailbreaks.
- [~] `app.py` limite l’historique à `HISTORIQUE_MAX_TOURS = 10`, ce qui borne le contexte, mais ne neutralise pas le contenu malveillant d’un message conservé.
- [ ] Les extraits retournés par [rag.py](src/rag.py) ne sont pas filtrés pour détecter des instructions malveillantes. Le prompt demande de les traiter comme non fiables, mais aucune validation indépendante de leur contenu n’est visible.
- [x] [test_securite.py](tests/test_securite.py) vérifie le refus d’une injection reconnue avant appel de l’agent et la validation des messages vides/trop longs.

### Limitation du débit et filtrage des réponses

- [x] `LimiteurDeDebit` dans [securite.py](src/securite.py) est appelé depuis [app.py](src/app.py) avant l’invocation de l’agent. La configuration actuelle est de trois requêtes par fenêtre de 60 secondes ; le dépassement renvoie HTTP 429.
- [~] Le limiteur conserve son état en mémoire dans un seul processus Python. Il n’est ni distribué entre plusieurs workers ni persistant, et n’identifie pas les utilisateurs individuellement.
- [x] `filtrer_sortie` masque les adresses e-mail et les numéros de téléphone dans la réponse avant de la stocker dans l’historique et de la renvoyer.
- [x] [test_securite.py](tests/test_securite.py) vérifie la fenêtre glissante, le refus HTTP 429 et le masquage des informations personnelles.

### Gestion des erreurs destinées à l’utilisateur

- [~] [app.py](src/app.py) renvoie des messages génériques pour les refus de contenu et de quota explicitement reconnus, ainsi que pour les erreurs de conversation.
- [~] Les erreurs `BadRequestError` non liées au filtre de contenu et les erreurs non prises en charge peuvent remonter au gestionnaire HTTP par défaut. L’interface [index.html](src/static/index.html) affiche le détail HTTP disponible ou un code d’erreur ; vérifier qu’aucun composant ne place des informations sensibles dans ce détail.
- Recommandation : ajouter un gestionnaire d’exception global qui journalise les détails côté serveur et renvoie systématiquement un message public générique.

### Dépendances

- [~] Le pipeline [azure-pipelines.yml](azure-pipelines.yml) installe `requirements.txt`, exécute les tests et Ruff, mais ne lance pas `pip-audit`.

## Optimisation et performance

### Index RAG calculé une seule fois

- [x] Au démarrage du serveur, le `lifespan` de [app.py](src/app.py) appelle `creer_agent()` une fois. Cette fonction dans [agent.py](src/agent.py) charge le modèle local et indexe les PDF avec `await recherche_fiches.charger()`. Les fragments et leurs embeddings sont conservés dans l’instance RAG réutilisée.
- [~] L’index n’est conservé qu’en mémoire dans le processus. Un redémarrage ou un worker supplémentaire recharge les PDF et recalcule les embeddings. L’embedding de la question est calculé lors de chaque recherche.

### Résultats SQL et historique bornés

- [x] `LIMITE_RESULTATS_SQL = 100` dans [agent.py](src/agent.py) est appliqué aux quatre requêtes d’outils par `LIMIT %s`, avec le plafond transmis comme paramètre.
- [x] `HISTORIQUE_MAX_TOURS = 10` dans [app.py](src/app.py) limite le contexte passé à l’agent et l’historique conservé à dix tours, en gardant le message système.
- Preuves de tests : assertions des paramètres `LIMIT` dans [test_agent_rag.py](tests/test_agent_rag.py) ; tests de troncature et de reconstruction du thread dans [test_historique.py](tests/test_historique.py).

### Modèle et paramètres de génération

- [~] [config.py](src/config.py) lit le nom du déploiement de conversation via `CHAT_DEPLOYMENT`, mais le code ne vérifie pas qu’il s’agit d’un modèle économique ou compact.
- [ ] Aucun réglage explicite de `max_tokens` ou de température n’est visible dans la configuration Python de la complétion. Définir ces paramètres selon la qualité attendue et le budget.

### Ressources partagées

- [x] [app.py](src/app.py) conserve une seule instance de l’agent dans `app.state.agent` pour la durée de vie du processus ; l’agent réutilise le Kernel et l’index RAG construits au démarrage.
- [ ] `executer_requete` dans [agent.py](src/agent.py) ouvre une connexion avec `mysql.connector.connect(**CONFIG_BD)` pour chaque requête puis la ferme. Aucun pool de connexions n’est configuré dans le code Python.

### Observabilité des index et cache pip

- [~] Le code Python consulté ne déclare ni ne vérifie les index MySQL réellement présents. Leur existence et leur adéquation aux requêtes ne peuvent pas être attestées à partir du client seul.
- [x] Le stage `Test` d’[azure-pipelines.yml](azure-pipelines.yml) utilise `Cache@2` et `PIP_CACHE_DIR` pour le cache pip.

## Résultat des tests

- Les tests Python correspondants sont [test_agent_rag.py](tests/test_agent_rag.py), [test_historique.py](tests/test_historique.py) et [test_securite.py](tests/test_securite.py).
- Les tests utilisent des mocks et un encodeur factice pour éviter une base MySQL réelle et un appel Azure.
- Le statut courant doit être vérifié en exécutant `python -m pytest tests -q`. Les six cas paramétrés de refus d’écriture dans `test_agent_rag.py` ne passent pas avec le `executer_requete` Python examiné tant que le garde-fou lecture seule n’est pas ajouté.

## Actions recommandées avant production

1. Garantir la gestion des secrets dans l’environnement de déploiement ; vérifier `.env` et l’historique Git hors de cette revue Python.
2. Appliquer des droits MySQL minimaux et les confirmer directement sur le serveur.
3. Ajouter un garde-fou lecture seule effectif à `executer_requete` et faire réussir les tests de refus d’écriture.
4. Renforcer la protection contre les injections de prompt, notamment pour le contenu RAG non fiable, et compléter les tests adversariaux.
5. Ajouter l’audit des dépendances et la mise à jour automatique des paquets.
6. Centraliser la gestion des erreurs, puis évaluer un pool MySQL, une persistance de l’index RAG et les paramètres de génération du modèle.
