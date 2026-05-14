# Veille Actualités — SD2026

Application web de collecte, consultation et visualisation d'articles d'actualité, développée dans le cadre du cours **Structuration des documents** (Master 1 MIAGE, Université de Lorraine, 2025-2026).

**Groupe :** ALI Abdullah · HUANG Alex · IRAZIGAMA Victor-Nervy  
**Enseignant :** Hendry FERREIRA CHAME

---

## Fonctionnalités

**Mode Administration**
- Abonnement à un sitemap XML d'actualité (URL + intervalle de mise à jour en minutes)
- Suppression d'un abonnement existant
- Mise à jour manuelle ou automatique (APScheduler) des sources actives

**Mode Consultation**
- Navigation parmi les articles organisés par source (grille parallèle)
- Filtrage multicritère : source, mot-clé, thème, plage de dates, nombre d'articles par source
- Ouverture de l'article original dans un nouvel onglet avec enregistrement automatique de la consultation
- Historique personnel des consultations avec horodatage (page Profil)
- Catégories personnalisées : regrouper des articles dans des listes nommées

**Visualisation**
- Génération d'un nuage de mots SVG paramétrable (n mots, période temporelle)
- Affichage en ligne et téléchargement du fichier SVG

---

## Technologies

| Composant | Version |
|-----------|---------|
| Python | 3.11+ |
| Flask | >=3.0,<4.0 |
| PyMongo | >=4.6,<5.0 |
| APScheduler | >=3.10,<4.0 |
| requests | >=2.31,<3.0 |
| MongoDB | 6.0+ (local ou distant) |

---

## Prérequis

- **Python 3.11 ou plus récent**
- **MongoDB** accessible en local (`localhost:27017`) ou via URI distante
- `pip` pour l'installation des dépendances

---

## Installation

```bash
# 1. Cloner le dépôt
git clone <url-du-depot>
cd StructureDesDocuments-Rendu

# 2. Créer et activer l'environnement virtuel
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer l'environnement
copy .env.example .env   # Windows
# cp .env.example .env   # Linux / macOS
# Puis éditer .env selon vos besoins (voir section Variables ci-dessous)
```

---

## Lancer l'application

```bash
python src/main.py
```

L'application démarre par défaut sur [http://127.0.0.1:5000](http://127.0.0.1:5000).  
Les index MongoDB sont créés automatiquement au premier démarrage.

> **Important :** MongoDB doit être lancé avant de démarrer l'application.  
> Sur Windows : `mongod --dbpath C:\data\db`

---

## Lancer les tests

```bash
python -m unittest discover -s tests
```

---

## Variables d'environnement

Copier `.env.example` en `.env` et adapter les valeurs :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MONGODB_URI` | `mongodb://localhost:27017` | URI de connexion MongoDB |
| `MONGODB_DB_NAME` | `SD2026_projet` | Nom de la base de données |
| `TEAM_MEMBER_NAMES` | *(vide)* | Prénoms des membres séparés par des virgules — génère le préfixe `G_AIH_` des collections (ex. : `Abdul,Irazigama,Huang`) |
| `MONGODB_COLLECTION_PREFIX` | *(vide)* | Préfixe explicite si vous ne voulez pas le calcul automatique |
| `FLASK_SECRET_KEY` | `dev-structure-donnees-secret` | Clé secrète Flask (changer en production) |
| `FLASK_DEBUG` | `true` | Mode debug Flask |
| `FLASK_HOST` | `127.0.0.1` | Adresse d'écoute |
| `FLASK_PORT` | `5000` | Port d'écoute |
| `REQUEST_TIMEOUT_SECONDS` | `10` | Timeout lecture des sitemaps |
| `FETCH_ARTICLE_IMAGES` | `true` | Active la récupération des images d'articles |
| `SCHEDULER_ENABLED` | `true` | Active la mise à jour automatique des abonnements |
| `SCHEDULER_TIMEZONE` | `UTC` | Fuseau horaire du scheduler |

---

## Structure du projet

```
StructureDesDocuments-Rendu/
├── src/
│   ├── app.py               # Routes Flask, logique applicative, scheduler
│   ├── BdMongo.py           # Connexion MongoDB, création des index, collections
│   ├── config.py            # Lecture de la configuration via variables d'environnement
│   ├── main.py              # Point d'entrée — lance l'application Flask
│   ├── sitemap_reader.py    # Lecture et parsing des sitemaps XML
│   ├── sitemap_to_mongo.py  # Import des articles depuis un sitemap vers MongoDB
│   ├── migrate_dates.py     # Script utilitaire de migration des dates
│   └── templates/
│       ├── home.html        # Page d'accueil
│       ├── articles.html    # Consultation et recherche d'articles
│       ├── subscriptions.html # Gestion des abonnements sitemaps
│       ├── wordcloud.html   # Génération du nuage de mots SVG
│       ├── profile.html     # Profil utilisateur et historique des consultations
│       └── auth.html        # Connexion / Inscription
├── tests/
│   └── test_app.py          # Tests fonctionnels des routes Flask
├── .env.example             # Modèle de configuration
├── requirements.txt         # Dépendances Python
└── README.md
```

---

## Collections MongoDB

Le préfixe des collections (`G_AIH_` par défaut) est calculé automatiquement depuis `TEAM_MEMBER_NAMES` dans le fichier `.env`.

| Collection | Rôle |
|------------|------|
| `G_AIH_subscriptions` | Abonnements aux sitemaps XML |
| `G_AIH_articles` | Articles collectés depuis les sitemaps |
| `G_AIH_consultations` | Historique des consultations avec horodatage |
| `G_AIH_users` | Comptes utilisateurs (authentification) |
| `G_AIH_categories` | Catégories personnalisées créées par les utilisateurs |

---

## Comptes utilisateurs

L'application intègre un système d'authentification (inscription / connexion). Certaines fonctionnalités (catégories personnelles, historique nominatif) requièrent d'être connecté. La consultation des articles et la génération du nuage de mots sont accessibles sans compte.
