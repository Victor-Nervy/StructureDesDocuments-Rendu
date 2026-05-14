from pymongo import ASCENDING, DESCENDING, IndexModel, MongoClient
from pymongo.errors import BulkWriteError, PyMongoError

from config import load_settings


SETTINGS = load_settings()

LEGACY_COLLECTION_NAMES = {
    "subscriptions": "subscriptions",
    "articles": "articles",
    "consultations": "consultations",
    "users": "users",
    "categories": "categories",
}


def build_collection_name(logical_name):
    if SETTINGS.mongodb_collection_prefix:
        return f"{SETTINGS.mongodb_collection_prefix}{logical_name}"
    return LEGACY_COLLECTION_NAMES[logical_name]

client = MongoClient(
    SETTINGS.mongodb_uri,
    connect=False,
    serverSelectionTimeoutMS=SETTINGS.mongo_server_selection_timeout_ms,
    tz_aware=True,
)
db = client[SETTINGS.mongodb_db_name]

subscriptions = db[build_collection_name("subscriptions")]
articles = db[build_collection_name("articles")]
consultations = db[build_collection_name("consultations")]
users = db[build_collection_name("users")]
categories = db[build_collection_name("categories")]


def migrate_legacy_collection(logical_name, target_collection):
    legacy_name = LEGACY_COLLECTION_NAMES[logical_name]
    if target_collection.name == legacy_name:
        return

    legacy_collection = db[legacy_name]
    if legacy_collection.estimated_document_count() == 0:
        return

    if target_collection.estimated_document_count() > 0:
        return

    documents = list(legacy_collection.find())
    if not documents:
        return

    try:
        target_collection.insert_many(documents, ordered=False)
    except BulkWriteError:
        pass


def ensure_collection_compatibility():
    migrate_legacy_collection("subscriptions", subscriptions)
    migrate_legacy_collection("articles", articles)
    migrate_legacy_collection("consultations", consultations)
    migrate_legacy_collection("users", users)


def ensure_indexes():
    try:
        ensure_collection_compatibility()
        subscriptions.create_indexes(
            [
                IndexModel([("sitemap_url", ASCENDING)], unique=True, name="subscriptions_sitemap_url_unique"),
                IndexModel([("active", ASCENDING), ("source_name", ASCENDING)], name="subscriptions_active_source_idx"),
            ]
        )
        articles.create_indexes(
            [
                IndexModel([("url", ASCENDING)], unique=True, name="articles_url_unique"),
                IndexModel([("publication_date", DESCENDING)], name="articles_publication_date_idx"),
                IndexModel([("source_name", ASCENDING), ("publication_date", DESCENDING)], name="articles_source_publication_idx"),
            ]
        )
        consultations.create_indexes(
            [
                IndexModel([("article_id", ASCENDING)], name="consultations_article_id_idx"),
                IndexModel([("consulted_at", DESCENDING)], name="consultations_consulted_at_idx"),
                IndexModel([("user_id", ASCENDING)], name="consultations_user_id_idx"),
                IndexModel(
                    [("user_id", ASCENDING), ("consulted_at", DESCENDING)],
                    name="consultations_user_id_consulted_at_idx",
                ),
                IndexModel(
                    [("consulted_at", DESCENDING), ("article_id", ASCENDING)],
                    name="consultations_consulted_at_article_id_idx",
                ),
            ]
        )
        users.create_indexes(
            [
                IndexModel([("email", ASCENDING)], unique=True, name="users_email_unique"),
                IndexModel([("username", ASCENDING)], unique=True, name="users_username_unique"),
            ]
        )
        categories.create_indexes(
            [
                IndexModel([("user_id", ASCENDING), ("name", ASCENDING)], unique=True, name="categories_user_name_unique"),
            ]
        )
    except PyMongoError as exc:
        raise RuntimeError(f"Impossible de creer les index MongoDB: {exc}") from exc


def ping_database():
    client.admin.command("ping")


if __name__ == "__main__":
    try:
        ping_database()
        ensure_indexes()
        print("Connexion MongoDB reussie !")
        print(f"Base de donnees : {db.name}")
        print(f"Collections disponibles : {db.list_collection_names()}")
        print(f"Prefixe de collections : {SETTINGS.mongodb_collection_prefix or 'legacy'}")
    except Exception as exc:
        print(f"Erreur de connexion : {exc}")
