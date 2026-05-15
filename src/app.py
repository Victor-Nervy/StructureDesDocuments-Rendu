import atexit
import logging
import math
import random
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from functools import wraps
from html import escape, unescape
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from bson import ObjectId
from bson.errors import InvalidId
from io import BytesIO

from flask import Flask, Response, abort, redirect, render_template, request, send_file, session
from pymongo.errors import DuplicateKeyError, PyMongoError
from requests import RequestException
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from werkzeug.security import check_password_hash, generate_password_hash

from BdMongo import articles, categories, consultations, ensure_indexes, subscriptions, users
from config import load_settings


SETTINGS = load_settings()
LOGGER = logging.getLogger(__name__)
LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc

app = Flask(__name__)
app.config["SETTINGS"] = SETTINGS
app.config["SECRET_KEY"] = SETTINGS.flask_secret_key
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def normalize_word_key(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


STOPWORDS_FR = {
    "le", "la", "les", "de", "du", "des", "un", "une", "en", "et", "est",
    "au", "aux", "ce", "se", "sa", "son", "ses", "sur", "par", "pour",
    "que", "qui", "dans", "avec", "plus", "pas", "il", "elle", "ils",
    "elles", "on", "nous", "vous", "je", "tu", "l", "d", "a",
    "ou", "si", "ne", "y", "c", "n", "s", "j", "m", "qu",
    "tout", "mais", "sans", "deux", "comme", "faire", "moins", "apres",
    "etre", "face", "ans",
}

WORDCLOUD_EXTRA_STOPWORDS = {
    "apres", "après", "avant", "contre", "retour", "premier", "premiere", "première",
    "premiers", "premieres", "premières", "second", "seconde", "secondes", "moyen",
    "moyenne", "moyennes", "sont", "sera", "seront", "avait", "avoir", "fait",
    "faite", "faits", "faites", "selon", "janvier", "fevrier", "février", "mars",
    "avril", "mai", "juin", "juillet", "aout", "août", "septembre", "octobre",
    "novembre", "decembre", "décembre", "lundi", "mardi", "mercredi", "jeudi",
    "vendredi", "samedi", "dimanche", "direct", "video", "vidéo", "videos",
    "vidéos", "photo", "photos", "comment", "pourquoi", "pourquoi", "quelle",
    "quelles", "quel", "quels", "leur", "leurs", "encore", "entre", "depuis",
    "sous", "peut", "peu", "voir", "ont", "ces", "cet", "cette", "ceux",
    "celles", "bien", "grand", "grande", "grands", "grandes", "nouveau",
    "nouveaux", "nouvelle", "nouvelles", "mois", "dont", "apres", "après",
    "chez", "sans", "tres", "très", "suite", "sera", "serait", "ete", "été",
    "toujours", "quatre", "faut", "lors", "place", "titre", "match", "fin",
    "devant", "temps", "pays", "porte", "remporte", "frappes",
}

WORDCLOUD_STOPWORDS_NORMALIZED = {
    normalize_word_key(word) for word in STOPWORDS_FR | WORDCLOUD_EXTRA_STOPWORDS
}
WORD_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}")

CATEGORY_KEYWORDS = {
    "politique": [
        "politique", "gouvernement", "president", "président", "assemblee", "assemblée",
        "senat", "sénat", "election", "élection", "ministere", "ministère",
    ],
    "economie": [
        "economie", "économie", "bourse", "inflation", "entreprise", "budget",
        "marche", "marché", "emploi", "croissance",
    ],
    "sport": [
        "sport", "football", "rugby", "tennis", "basket", "ligue",
        "tournoi", "match", "olympique",
    ],
    "technologie": [
        "technologie", "tech", "ia", "intelligence artificielle", "cyber",
        "numerique", "numérique", "startup", "robot", "logiciel",
    ],
    "culture": [
        "culture", "cinema", "cinéma", "musique", "livre", "festival",
        "theatre", "théâtre", "serie", "série",
    ],
    "environnement": [
        "climat", "energie", "énergie", "environnement", "pollution",
        "biodiversite", "biodiversité", "carbone", "ecologie", "écologie",
    ],
}

CATEGORY_CHOICES = [
    ("politique", "Politique"),
    ("economie", "Economie"),
    ("sport", "Sport"),
    ("technologie", "Technologie"),
    ("culture", "Culture"),
    ("environnement", "Environnement"),
]

SITEMAP_NAMESPACES = {
    "sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
    "media": "http://search.yahoo.com/mrss/",
}

IMAGE_META_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+property=["\']og:image:url["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image:url["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+name=["\']twitter:image:src["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image:src["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+itemprop=["\']image["\']', re.IGNORECASE),
    re.compile(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']image_src["\']', re.IGNORECASE),
]

IMAGE_JSON_PATTERNS = [
    re.compile(r'"thumbnailUrl"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"contentUrl"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"image"\s*:\s*\[\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"image"\s*:\s*"([^"]+)"', re.IGNORECASE),
]

IMAGE_TAG_PATTERNS = [
    re.compile(r'<img[^>]+data-src=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE),
]

IMAGE_IGNORED_HINTS = ("logo", "icon", "favicon", "sprite", "avatar", "placeholder")
MAX_SITEMAP_INDEX_CHILDREN = 5

scheduler = BackgroundScheduler(
    timezone=SETTINGS.scheduler_timezone,
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
)


def create_http_session():
    retries = SETTINGS.http_max_retries
    retry_strategy = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=SETTINGS.http_backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": SETTINGS.http_user_agent})
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


HTTP_SESSION = create_http_session()


@app.template_filter("format_date")
def format_date(value):
    if value is None or value == "":
        return "Date inconnue"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%d/%m/%Y a %H:%M UTC")
    return str(value)


def is_valid_http_url(url):
    if not url:
        return False

    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def parse_positive_int(raw_value, default=None, minimum=1):
    try:
        parsed_value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default

    if parsed_value < minimum:
        return default

    return parsed_value


def normalize_email(email):
    return str(email or "").strip().lower()


def serialize_user(user):
    if user is None:
        return None

    return {
        "id": str(user["_id"]),
        "username": user.get("username", ""),
        "email": user.get("email", ""),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
    }


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    try:
        oid = ObjectId(user_id)
    except (InvalidId, TypeError):
        session.pop("user_id", None)
        return None

    user = users.find_one({"_id": oid}, {"password_hash": 0})
    if user is None:
        session.pop("user_id", None)
        return None

    return user


def login_user(user):
    session.clear()
    session["user_id"] = str(user["_id"])


def logout_user():
    session.clear()


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if get_current_user() is None:
            next_url = request.path
            if request.query_string:
                next_url = f"{next_url}?{request.query_string.decode()}"
            return redirect(f"/login?next={quote_plus(next_url)}")
        return view_func(*args, **kwargs)

    return wrapped_view


def normalize_category_name(name):
    """Nettoie et normalise le nom de la catégorie fournit par l'utilisateur."""
    return str(name or "").strip()


def validate_category_name(name):
    """Valide le nom de catégorie et renvoie un message d'erreur si nécessaire."""
    normalized = normalize_category_name(name)
    if not normalized:
        return None, "Le nom de categorie est obligatoire."
    if len(normalized) > 80:
        return None, "Le nom de categorie doit contenir au maximum 80 caracteres."
    return normalized, None


def get_user_categories(user_id):
    return list(categories.find({"user_id": user_id}).sort("name", 1))


def parse_object_id(raw_value):
    if not raw_value:
        return None

    cleaned = str(raw_value).strip()
    if cleaned.startswith("ObjectId(") and cleaned.endswith(")"):
        cleaned = cleaned[len("ObjectId("):-1].strip("'\"")

    try:
        return ObjectId(cleaned)
    except (InvalidId, TypeError):
        return None


def get_user_category_by_id(category_id, user_id):
    oid = parse_object_id(category_id)
    if oid is None:
        return None
    return categories.find_one({"_id": oid, "user_id": user_id})


def resolve_article_by_url_or_id(raw_value):
    """Retourne l'article MongoDB correspondant à une URL ou un identifiant passé par l'utilisateur."""
    if not raw_value:
        return None

    cleaned = str(raw_value).strip()
    oid = parse_object_id(cleaned)

    if oid is not None:
        article = articles.find_one({"_id": oid})
        if article is not None:
            return article

    if is_valid_http_url(cleaned):
        return articles.find_one({"url": cleaned})

    return None


@app.context_processor
def inject_current_user():
    return {"current_user": serialize_user(get_current_user())}


def convertir_date(date_str):
    if not date_str:
        return None

    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            converted = datetime.strptime(date_str.strip(), fmt)
            if converted.tzinfo is None:
                converted = converted.replace(tzinfo=timezone.utc)
            return converted.astimezone(timezone.utc)
        except ValueError:
            continue

    return None


def build_publication_date_filter(date_debut, date_fin):
    filtre = {}

    try:
        if date_debut:
            debut_dt = datetime.strptime(date_debut, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            filtre.setdefault("publication_date", {})["$gte"] = debut_dt

        if date_fin:
            fin_dt = datetime.strptime(date_fin, "%Y-%m-%d").replace(
                hour=23,
                minute=59,
                second=59,
                tzinfo=timezone.utc,
            )
            filtre.setdefault("publication_date", {})["$lte"] = fin_dt

        if date_debut and date_fin:
            debut_test = datetime.strptime(date_debut, "%Y-%m-%d")
            fin_test = datetime.strptime(date_fin, "%Y-%m-%d")
            if debut_test > fin_test:
                return {}, "La date de debut doit etre anterieure ou egale a la date de fin."
    except ValueError:
        return {}, "Format de date invalide."

    return filtre, None


def parse_datetime_local(value):
    if not value:
        return None

    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)

    return parsed.astimezone(timezone.utc)


def build_consultation_date_filter(consulted_after, consulted_before):
    filtre = {}

    try:
        if consulted_after:
            filtre.setdefault("consulted_at", {})["$gte"] = parse_datetime_local(consulted_after)

        if consulted_before:
            filtre.setdefault("consulted_at", {})["$lte"] = parse_datetime_local(consulted_before)

        if consulted_after and consulted_before:
            start = parse_datetime_local(consulted_after)
            end = parse_datetime_local(consulted_before)
            if start > end:
                return {}, "La date de debut de consultation doit etre anterieure ou egale a la date de fin."
    except ValueError:
        return {}, "Format de date/heure de consultation invalide."

    return filtre, None


def build_category_title_condition(category):
    if not category:
        return None, None

    normalized = category.strip().lower()
    keywords = CATEGORY_KEYWORDS.get(normalized)
    if not keywords:
        return None, "Categorie de mots-cles inconnue."

    pattern = "|".join(re.escape(keyword) for keyword in keywords)
    return {"title": {"$regex": pattern, "$options": "i"}}, None


def build_articles_query(source, keyword, category, date_debut, date_fin, consulted_after, consulted_before):
    query_parts = []

    publication_filter, erreur = build_publication_date_filter(date_debut, date_fin)
    if erreur:
        return {}, erreur
    if publication_filter:
        query_parts.append(publication_filter)

    consultation_filter, erreur = build_consultation_date_filter(consulted_after, consulted_before)
    if erreur:
        return {}, erreur
    if consultation_filter:
        article_ids = consultations.distinct("article_id", consultation_filter)
        query_parts.append({"_id": {"$in": article_ids}})

    if source:
        query_parts.append({"source_name": source})

    if keyword:
        query_parts.append({"title": {"$regex": re.escape(keyword), "$options": "i"}})

    category_condition, erreur = build_category_title_condition(category)
    if erreur:
        return {}, erreur
    if category_condition:
        query_parts.append(category_condition)

    if not query_parts:
        return {}, None
    if len(query_parts) == 1:
        return query_parts[0], None

    return {"$and": query_parts}, None


def normalize_image_url(article_url, raw_url):
    if not raw_url:
        return None

    candidate = unescape(str(raw_url)).strip()
    if not candidate:
        return None

    if candidate.lower().startswith("data:"):
        return None

    candidate = candidate.split(" ")[0].strip()
    candidate = urljoin(article_url, candidate)

    if not is_valid_http_url(candidate):
        return None

    lowered = candidate.lower()
    if any(hint in lowered for hint in IMAGE_IGNORED_HINTS):
        return None

    return candidate


def extract_sitemap_image_url(url_tag, article_url):
    candidates = []

    for image_tag in url_tag.findall("image:image", SITEMAP_NAMESPACES):
        candidates.append(image_tag.findtext("image:loc", default="", namespaces=SITEMAP_NAMESPACES))

    for media_tag in url_tag.findall("media:content", SITEMAP_NAMESPACES):
        candidates.append(media_tag.get("url", ""))

    for media_tag in url_tag.findall("media:thumbnail", SITEMAP_NAMESPACES):
        candidates.append(media_tag.get("url", ""))

    for candidate in candidates:
        normalized = normalize_image_url(article_url, candidate)
        if normalized:
            return normalized

    return None


def should_refresh_article_image(image_url):
    if not image_url:
        return True

    lowered = image_url.lower()
    if "medias.lequipe.fr/img-photo" in lowered:
        return True
    if "lequipe.fr/_medias/" in lowered and not re.search(r"\.(jpg|jpeg|png|webp|gif)(?:[?#].*)?$", lowered):
        return True

    return False


def iter_candidate_image_urls(article_url, html):
    seen = set()

    for patterns in (IMAGE_META_PATTERNS, IMAGE_JSON_PATTERNS, IMAGE_TAG_PATTERNS):
        for pattern in patterns:
            for match in pattern.finditer(html):
                candidate = normalize_image_url(article_url, match.group(1))
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    yield candidate


def get_xml_local_name(tag):
    return str(tag or "").rsplit("}", 1)[-1]


def sort_sitemap_index_urls(urls):
    return sorted(
        urls,
        key=lambda candidate: (
            "news" not in candidate.lower(),
            "recent" not in candidate.lower(),
            candidate,
        ),
    )


def lire_sitemap(url, _depth=0, _seen=None):
    if not is_valid_http_url(url):
        raise ValueError("L'URL du sitemap doit etre une URL HTTP(S) valide.")

    if _seen is None:
        _seen = set()
    if url in _seen:
        return []
    _seen.add(url)

    try:
        response = HTTP_SESSION.get(url, timeout=SETTINGS.request_timeout_seconds)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except RequestException as exc:
        raise RuntimeError(f"Impossible de lire le sitemap: {exc}") from exc
    except ET.ParseError as exc:
        raise ValueError(f"Le contenu du sitemap est invalide: {exc}") from exc

    if get_xml_local_name(root.tag) == "sitemapindex":
        if _depth >= 2:
            return []

        child_urls = [
            loc.text.strip()
            for loc in root.findall("sitemap:sitemap/sitemap:loc", SITEMAP_NAMESPACES)
            if loc.text and is_valid_http_url(loc.text.strip())
        ]
        news_child_urls = [child_url for child_url in child_urls if "news" in child_url.lower()]
        if news_child_urls:
            child_urls = news_child_urls
        resultats_index = []

        for child_url in sort_sitemap_index_urls(child_urls)[:MAX_SITEMAP_INDEX_CHILDREN]:
            try:
                resultats_index.extend(lire_sitemap(child_url, _depth=_depth + 1, _seen=_seen))
            except (RuntimeError, ValueError) as exc:
                LOGGER.warning("Sous-sitemap ignore (%s): %s", child_url, exc)

        return resultats_index

    resultats = []

    for url_tag in root.findall("sitemap:url", SITEMAP_NAMESPACES):
        loc = url_tag.findtext("sitemap:loc", default="", namespaces=SITEMAP_NAMESPACES).strip()
        title = url_tag.findtext("news:news/news:title", default="", namespaces=SITEMAP_NAMESPACES).strip()
        publication_date = url_tag.findtext(
            "news:news/news:publication_date",
            default="",
            namespaces=SITEMAP_NAMESPACES,
        ).strip()

        if not loc:
            continue

        resultats.append(
            {
                "loc": loc,
                "title": title,
                "publication_date": publication_date,
                "image_url": extract_sitemap_image_url(url_tag, loc),
            }
        )

    return resultats


def recuperer_image_article(url):
    if not SETTINGS.fetch_article_images or not is_valid_http_url(url):
        return None

    try:
        response = HTTP_SESSION.get(url, timeout=SETTINGS.image_request_timeout_seconds)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()

        if content_type and "html" not in content_type:
            return None

        html = response.text
        for image_url in iter_candidate_image_urls(url, html):
            return image_url
    except RequestException as exc:
        LOGGER.warning("Impossible de recuperer l'image pour %s: %s", url, exc)

    return None


def inserer_articles(liste, subscription_id, source_name):
    nb_inseres = 0
    nb_doublons = 0

    for item in liste:
        url_article = item.get("loc", "").strip()
        if not is_valid_http_url(url_article):
            LOGGER.warning("Article ignore car URL invalide: %s", url_article)
            continue

        article_existant = articles.find_one({"url": url_article}, {"_id": 1, "image_url": 1})
        image_url = normalize_image_url(url_article, item.get("image_url"))

        if article_existant is None:
            if should_refresh_article_image(image_url):
                image_url = recuperer_image_article(url_article) or image_url
            document = {
                "subscription_id": subscription_id,
                "source_name": source_name,
                "url": url_article,
                "title": item.get("title", "").strip(),
                "publication_date": convertir_date(item.get("publication_date")),
                "image_url": image_url,
                "fetched_at": datetime.now(timezone.utc),
                "consultations_count": 0,
            }

            try:
                articles.insert_one(document)
                nb_inseres += 1
                continue
            except DuplicateKeyError:
                nb_doublons += 1
                article_existant = articles.find_one({"url": url_article}, {"_id": 1, "image_url": 1})
        else:
            nb_doublons += 1

        if article_existant and should_refresh_article_image(article_existant.get("image_url")):
            image_url = recuperer_image_article(url_article) or image_url
            if image_url:
                articles.update_one(
                    {"_id": article_existant["_id"]},
                    {"$set": {"image_url": image_url}},
                )

    return nb_inseres, nb_doublons


def build_update_resume():
    return {
        "traites": 0,
        "inseres": 0,
        "doublons": 0,
        "erreurs": [],
        "sources": [],
    }


def append_update_success(resume, source_name, inseres, doublons):
    resume["traites"] += 1
    resume["inseres"] += inseres
    resume["doublons"] += doublons
    resume["sources"].append(source_name)


def append_update_error(resume, source_name, exc):
    resume["traites"] += 1
    resume["sources"].append(source_name)
    resume["erreurs"].append(f"{source_name} : {exc}")


def mettre_a_jour_abonnement_document(abonnement):
    liste = lire_sitemap(abonnement["sitemap_url"])
    inseres, doublons = inserer_articles(liste, abonnement["_id"], abonnement["source_name"])

    subscriptions.update_one(
        {"_id": abonnement["_id"]},
        {"$set": {"last_fetch_at": datetime.now(timezone.utc)}},
    )

    LOGGER.info(
        "Mise a jour %s: %s inseres, %s doublons.",
        abonnement["source_name"],
        inseres,
        doublons,
    )
    return inseres, doublons


def mettre_a_jour_un_abonnement(subscription_id_str):
    try:
        oid = ObjectId(subscription_id_str)
        abonnement = subscriptions.find_one({"_id": oid, "active": True})
        if abonnement is None:
            return

        mettre_a_jour_abonnement_document(abonnement)
    except Exception as exc:
        LOGGER.exception("Erreur pendant la mise a jour de l'abonnement %s: %s", subscription_id_str, exc)


def mettre_a_jour_tous_les_abonnements():
    abonnements_actifs = list(subscriptions.find({"active": True}))
    resume = build_update_resume()

    for abonnement in abonnements_actifs:
        try:
            inseres, doublons = mettre_a_jour_abonnement_document(abonnement)
            append_update_success(resume, abonnement["source_name"], inseres, doublons)
        except Exception as exc:
            append_update_error(resume, abonnement["source_name"], exc)

    return resume


def synchroniser_jobs():
    if not SETTINGS.scheduler_enabled:
        return

    abonnements_actifs = list(subscriptions.find({"active": True}))
    ids_attendus = {f"sub_{str(abonnement['_id'])}" for abonnement in abonnements_actifs}

    for job in scheduler.get_jobs():
        if job.id.startswith("sub_") and job.id not in ids_attendus:
            scheduler.remove_job(job.id)
            LOGGER.info("Job supprime: %s", job.id)

    for abonnement in abonnements_actifs:
        job_id = f"sub_{str(abonnement['_id'])}"
        minutes = parse_positive_int(abonnement.get("refresh_interval_minutes"), default=60, minimum=1)
        sub_id_str = str(abonnement["_id"])
        existing_job = scheduler.get_job(job_id)

        if existing_job is None:
            scheduler.add_job(
                func=mettre_a_jour_un_abonnement,
                trigger="interval",
                minutes=minutes,
                id=job_id,
                args=[sub_id_str],
                replace_existing=True,
            )
            LOGGER.info("Job cree: %s toutes les %s minutes.", abonnement["source_name"], minutes)
        else:
            intervalle_actuel = int(existing_job.trigger.interval.total_seconds() // 60)
            if intervalle_actuel != minutes:
                scheduler.reschedule_job(job_id, trigger="interval", minutes=minutes)
                LOGGER.info("Job mis a jour: %s -> %s minutes.", abonnement["source_name"], minutes)


def arreter_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def demarrer_scheduler():
    if not SETTINGS.scheduler_enabled:
        LOGGER.info("Scheduler desactive par configuration.")
        return

    synchroniser_jobs()
    if not scheduler.running:
        scheduler.start()
        LOGGER.info("Scheduler demarre.")
        atexit.register(arreter_scheduler)


def render_subscriptions_page(erreur=None, resume=None):
    try:
        liste = list(subscriptions.find().sort("source_name", 1))
    except PyMongoError:
        liste = []
        erreur = erreur or (
            "Impossible de contacter MongoDB. Verifie que le serveur MongoDB est lance sur "
            "localhost:27017, puis recharge la page."
        )
    return render_template("subscriptions.html", subscriptions=liste, erreur=erreur, resume=resume)


def build_user_history(user_id, limit=40, keyword=None, date_start=None, date_end=None):
    consultation_filter = {"user_id": user_id}

    date_filter = {}
    if date_start:
        date_filter["$gte"] = date_start
    if date_end:
        date_filter["$lte"] = date_end
    if date_filter:
        consultation_filter["consulted_at"] = date_filter

    fetch_limit = limit * 5 if keyword else limit
    consultation_items = list(
        consultations.find(consultation_filter)
        .sort("consulted_at", -1)
        .limit(fetch_limit)
    )

    article_ids = [item.get("article_id") for item in consultation_items if item.get("article_id")]

    article_query = {"_id": {"$in": article_ids}}
    if keyword:
        article_query["title"] = {"$regex": re.escape(keyword), "$options": "i"}

    article_map = {
        article["_id"]: article
        for article in articles.find(
            article_query,
            {"title": 1, "source_name": 1, "publication_date": 1, "image_url": 1},
        )
    }

    history = []
    for item in consultation_items:
        article = article_map.get(item.get("article_id"))
        if article is None:
            continue
        history.append(
            {
                "article_id": article["_id"],
                "title": article.get("title", "Article sans titre"),
                "source_name": article.get("source_name", "Source inconnue"),
                "publication_date": article.get("publication_date"),
                "image_url": article.get("image_url"),
                "consulted_at": item.get("consulted_at"),
            }
        )
        if len(history) >= limit:
            break

    return history


def extract_title_words(title):
    if not title:
        return []

    mots = WORD_PATTERN.findall(title.lower())
    return [mot for mot in mots if normalize_word_key(mot) not in WORDCLOUD_STOPWORDS_NORMALIZED]


def build_wordcloud_frequencies(titres, nb_mots):
    counter = Counter()
    display_words = {}

    for titre in titres:
        for mot in extract_title_words(titre):
            word_key = normalize_word_key(mot)
            counter[word_key] += 1
            display_words.setdefault(word_key, mot)

    items = list(counter.items())
    repeated_count = sum(1 for _, freq in items if freq >= 2)
    minimum_frequency = 1

    # When the cloud is dense enough, drop one-off words to reduce visual noise.
    if len(items) > nb_mots and repeated_count >= max(12, nb_mots // 5):
        minimum_frequency = 2

    frequencies = [
        (display_words[word_key], word_key, freq)
        for word_key, freq in items
        if freq >= minimum_frequency
    ]
    frequencies.sort(key=lambda item: (-item[2], -min(len(item[0]), 12), item[1]))
    return frequencies[:nb_mots]


def build_wordcloud_svg(date_debut, date_fin, nb_mots):
    filtre, erreur = build_publication_date_filter(date_debut, date_fin)
    if erreur:
        return None, erreur

    try:
        documents = list(
            articles.find(filtre, {"title": 1, "publication_date": 1})
            .sort("publication_date", -1)
        )
    except PyMongoError:
        return None, (
            "Impossible de contacter MongoDB. Verifie que le serveur MongoDB est lance sur "
            "localhost:27017, puis recharge la page."
        )

    titres = [article["title"] for article in documents if article.get("title")]

    if not titres:
        return None, None

    word_links = {}
    word_tooltips = {}

    for article in documents:
        title = article.get("title", "")
        if not title:
            continue

        for mot in set(extract_title_words(title)):
            word_key = normalize_word_key(mot)
            if word_key not in word_links:
                word_links[word_key] = f"/articles?keyword={quote_plus(mot)}"
                word_tooltips[word_key] = f"Rechercher : {mot}"

    return generer_svg_interactif(
        titres,
        nb_mots=nb_mots,
        word_links=word_links,
        word_tooltips=word_tooltips,
    ), None


@app.route("/")
def index():
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return render_template(
        "home.html",
        python_version=python_version,
        current_user=serialize_user(get_current_user()),
    )


def render_auth_page(error=None, mode="login"):
    return render_template("auth.html", error=error, mode=mode)


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user() is not None:
        return redirect("/profile")

    if request.method == "GET":
        return render_auth_page()

    email = normalize_email(request.form.get("email"))
    password = request.form.get("password", "")

    if not email or not password:
        return render_auth_page("Email et mot de passe obligatoires.", mode="login")

    user = users.find_one({"email": email})
    password_hash = user.get("password_hash") if user else None
    if user is None or not password_hash or not check_password_hash(password_hash, password):
        return render_auth_page("Identifiants invalides.", mode="login")

    now = datetime.now(timezone.utc)
    users.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": now}})
    user["last_login_at"] = now
    login_user(user)
    next_url = (request.form.get("next") or request.args.get("next") or "").strip()
    if not next_url or not next_url.startswith("/"):
        next_url = "/profile"
    return redirect(next_url)


@app.route("/register", methods=["GET", "POST"])
def register():
    if get_current_user() is not None:
        return redirect("/profile")

    if request.method == "GET":
        return render_auth_page(mode="register")

    username = request.form.get("username", "").strip()
    email = normalize_email(request.form.get("email"))
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not username or not email or not password:
        return render_auth_page("Pseudo, email et mot de passe obligatoires.", mode="register")
    if password != confirm_password:
        return render_auth_page("La confirmation du mot de passe ne correspond pas.", mode="register")
    if len(password) < 6:
        return render_auth_page("Le mot de passe doit contenir au moins 6 caracteres.", mode="register")
    if users.find_one({"email": email}, {"_id": 1}):
        return render_auth_page("Un compte existe deja avec cet email.", mode="register")
    if users.find_one({"username": username}, {"_id": 1}):
        return render_auth_page("Ce pseudo est deja utilise.", mode="register")

    now = datetime.now(timezone.utc)
    user_document = {
        "username": username,
        "email": email,
        "password_hash": generate_password_hash(password),
        "created_at": now,
        "last_login_at": now,
    }

    try:
        result = users.insert_one(user_document)
    except DuplicateKeyError:
        return render_auth_page("Impossible de creer le compte avec ces informations.", mode="register")

    user_document["_id"] = result.inserted_id
    login_user(user_document)
    return redirect("/profile")


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect("/")


@app.route("/profile")
@login_required
def profile():
    user = get_current_user()
    active_tab = request.args.get("tab", "profile").strip().lower()
    if active_tab not in {"profile", "history"}:
        active_tab = "profile"

    hist_keyword = request.args.get("hist_keyword", "").strip()
    hist_after = request.args.get("hist_after", "").strip()
    hist_before = request.args.get("hist_before", "").strip()

    date_start = None
    date_end = None
    try:
        if hist_after:
            date_start = parse_datetime_local(hist_after)
        if hist_before:
            date_end = parse_datetime_local(hist_before)
    except ValueError:
        pass

    history = build_user_history(
        user["_id"],
        limit=40,
        keyword=hist_keyword or None,
        date_start=date_start,
        date_end=date_end,
    )

    history_count = consultations.count_documents({"user_id": user["_id"]})
    consulted_article_ids = consultations.distinct("article_id", {"user_id": user["_id"]})
    sources_count = len(
        [source for source in articles.distinct("source_name", {"_id": {"$in": consulted_article_ids}}) if source]
    )

    last_consult = list(consultations.find({"user_id": user["_id"]}).sort("consulted_at", -1).limit(1))
    stats = {
        "history_count": history_count,
        "sources_count": sources_count,
        "last_consulted_at": last_consult[0].get("consulted_at") if last_consult else None,
    }

    return render_template(
        "profile.html",
        user=serialize_user(user),
        history=history,
        stats=stats,
        active_tab=active_tab,
        hist_keyword=hist_keyword,
        hist_after=hist_after,
        hist_before=hist_before,
    )


@app.route("/articles")
def liste_articles():
    source = request.args.get("source_name", "").strip()
    keyword = request.args.get("keyword", "").strip()
    category = request.args.get("category", "").strip()
    date_debut = request.args.get("date_debut", "").strip()
    date_fin = request.args.get("date_fin", "").strip()
    consulted_after = request.args.get("consulted_after", "").strip()
    consulted_before = request.args.get("consulted_before", "").strip()
    nb_articles = parse_positive_int(request.args.get("nb_articles", "20"), default=20, minimum=1)

    erreur = None
    resultats = []
    sources = []

    try:
        filtre, erreur = build_articles_query(
            source=source,
            keyword=keyword,
            category=category,
            date_debut=date_debut,
            date_fin=date_fin,
            consulted_after=consulted_after,
            consulted_before=consulted_before,
        )

        if not erreur:
            if source:
                # Source spécifique — filtre contient déjà source_name
                resultats = list(
                    articles.find(filtre).sort("publication_date", -1).limit(nb_articles)
                )
            else:
                # Toutes les sources : requête find() séparée par source
                # pour garantir N articles de chaque source indépendamment
                src_names = sorted(
                    [s for s in articles.distinct("source_name") if s],
                    key=str.lower,
                )
                for sname in src_names:
                    src_filter = {**filtre, "source_name": sname}
                    src_arts = list(
                        articles.find(src_filter).sort("publication_date", -1).limit(nb_articles)
                    )
                    resultats.extend(src_arts)

        article_src = set(articles.distinct("source_name"))
        sub_src = set(subscriptions.distinct("source_name", {"active": True}))
        sources = sorted([s for s in (article_src | sub_src) if s])
    except PyMongoError:
        resultats = []
        sources = []
        erreur = (
            "Impossible de contacter MongoDB. Verifie que le serveur MongoDB est lance sur "
            "localhost:27017, puis recharge la page."
        )

    # Groupement par source en Python — plus fiable que Jinja2 groupby
    articles_by_source = {}
    for article in resultats:
        sname = article.get("source_name") or "Source inconnue"
        if sname not in articles_by_source:
            articles_by_source[sname] = []
        articles_by_source[sname].append(article)
    grouped_articles = sorted(articles_by_source.items(), key=lambda x: x[0].lower())

    current_user = get_current_user()
    user_categories = []
    if current_user is not None:
        raw_cats = get_user_categories(current_user["_id"])
        for cat in raw_cats:
            cat_arts = []
            if cat.get("article_ids"):
                try:
                    cat_arts = list(
                        articles.find({"_id": {"$in": cat["article_ids"]}})
                        .sort("publication_date", -1)
                    )
                except PyMongoError:
                    pass
            cat["_articles"] = cat_arts
            user_categories.append(cat)

    return render_template(
        "articles.html",
        articles=resultats,
        grouped_articles=grouped_articles,
        sources=sources,
        categories=CATEGORY_CHOICES,
        source=source,
        keyword=keyword,
        category=category,
        date_debut=date_debut,
        date_fin=date_fin,
        consulted_after=consulted_after,
        consulted_before=consulted_before,
        nb_articles=nb_articles,
        erreur=erreur or request.args.get("error"),
        success=request.args.get("success"),
        user_categories=user_categories,
    )


# --- Gestion des categories personnelles utilisateurs (integrees dans /articles) ---

def _require_login_for_cat():
    """Retourne l'utilisateur courant ou None si non connecté."""
    return get_current_user()


@app.route("/categories")
@app.route("/categories/")
def liste_categories():
    return redirect("/articles#categories")


@app.route("/categories/add", methods=["POST"])
@app.route("/categories/add/", methods=["POST"])
def ajouter_categorie():
    user = _require_login_for_cat()
    if user is None:
        return redirect("/login?next=%2Farticles%23categories")

    name, erreur = validate_category_name(request.form.get("name"))
    if erreur:
        return redirect(f"/articles?error={quote_plus(erreur)}#categories")

    description = str(request.form.get("description", "")).strip()
    now = datetime.now(timezone.utc)

    try:
        categories.insert_one(
            {
                "user_id": user["_id"],
                "name": name,
                "description": description,
                "article_ids": [],
                "created_at": now,
                "updated_at": now,
            }
        )
    except DuplicateKeyError:
        return redirect(f"/articles?error={quote_plus('Une categorie existe deja avec ce nom.')}#categories")

    return redirect(f"/articles?success={quote_plus('Categorie creee avec succes.')}#categories")


@app.route("/categories/<id>")
@app.route("/categories/<id>/")
def voir_categorie(id):
    return redirect(f"/articles?cat={id}#categories")


@app.route("/categories/<id>/update", methods=["POST"])
@app.route("/categories/<id>/update/", methods=["POST"])
def modifier_categorie(id):
    user = _require_login_for_cat()
    if user is None:
        return redirect("/login?next=%2Farticles%23categories")

    category = get_user_category_by_id(id, user["_id"])
    if category is None:
        return redirect(f"/articles?error={quote_plus('Categorie introuvable ou inaccessible.')}#categories")

    name, erreur = validate_category_name(request.form.get("name"))
    if erreur:
        return redirect(f"/articles?cat={id}&error={quote_plus(erreur)}#categories")

    description = str(request.form.get("description", "")).strip()
    update_data = {
        "name": name,
        "description": description,
        "updated_at": datetime.now(timezone.utc),
    }

    try:
        categories.update_one({"_id": category["_id"]}, {"$set": update_data})
    except DuplicateKeyError:
        return redirect(f"/articles?cat={id}&error={quote_plus('Une categorie existe deja avec ce nom.')}#categories")

    return redirect(f"/articles?cat={id}&success={quote_plus('Categorie mise a jour.')}#categories")


@app.route("/categories/<id>/delete", methods=["POST"])
@app.route("/categories/<id>/delete/", methods=["POST"])
def supprimer_categorie(id):
    user = _require_login_for_cat()
    if user is None:
        return redirect("/login?next=%2Farticles%23categories")

    category = get_user_category_by_id(id, user["_id"])
    if category is None:
        return redirect(f"/articles?error={quote_plus('Categorie introuvable ou inaccessible.')}#categories")

    categories.delete_one({"_id": category["_id"]})
    return redirect(f"/articles?success={quote_plus('Categorie supprimee.')}#categories")


@app.route("/categories/<id>/add-article", methods=["POST"])
@app.route("/categories/<id>/add-article/", methods=["POST"])
def ajouter_article_categorie(id):
    user = _require_login_for_cat()
    if user is None:
        return redirect("/login?next=%2Farticles%23categories")

    category = get_user_category_by_id(id, user["_id"])
    if category is None:
        return redirect(f"/articles?error={quote_plus('Categorie introuvable ou inaccessible.')}#categories")

    article_value = str(request.form.get("article_url_or_id", "")).strip()
    article = resolve_article_by_url_or_id(article_value)
    if article is None:
        return redirect(f"/articles?cat={id}&error={quote_plus('Aucun article trouve pour ce lien ou identifiant.')}#categories")

    if article["_id"] in category.get("article_ids", []):
        return redirect(f"/articles?cat={id}&error={quote_plus('Cet article est deja dans la categorie.')}#categories")

    categories.update_one(
        {"_id": category["_id"]},
        {
            "$addToSet": {"article_ids": article["_id"]},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    return redirect(f"/articles?cat={id}&success={quote_plus('Article ajoute a la categorie.')}#categories")


@app.route("/categories/<id>/remove-article", methods=["POST"])
@app.route("/categories/<id>/remove-article/", methods=["POST"])
def supprimer_article_categorie(id):
    user = _require_login_for_cat()
    if user is None:
        return redirect("/login?next=%2Farticles%23categories")

    category = get_user_category_by_id(id, user["_id"])
    if category is None:
        return redirect(f"/articles?error={quote_plus('Categorie introuvable ou inaccessible.')}#categories")

    article_id_raw = request.form.get("article_id", "").strip()
    try:
        article_oid = ObjectId(article_id_raw)
    except (InvalidId, TypeError):
        return redirect(f"/articles?cat={id}&error={quote_plus('Identifiant d article invalide.')}#categories")

    categories.update_one(
        {"_id": category["_id"]},
        {"$pull": {"article_ids": article_oid}, "$set": {"updated_at": datetime.now(timezone.utc)}},
    )
    return redirect(f"/articles?cat={id}&success={quote_plus('Article retire de la categorie.')}#categories")


@app.route("/categories/add-article", methods=["POST"])
@app.route("/categories/add-article/", methods=["POST"])
def ajouter_article_categorie_depuis_articles():
    user = _require_login_for_cat()
    if user is None:
        return redirect("/login?next=%2Farticles%23categories")

    category_id = request.form.get("category_id", "").strip()
    article_id_raw = request.form.get("article_id", "").strip()

    category = get_user_category_by_id(category_id, user["_id"])
    if category is None:
        return redirect(f"/articles?error={quote_plus('Categorie invalide.')}#categories")

    try:
        article_oid = ObjectId(article_id_raw)
    except (InvalidId, TypeError):
        return redirect(f"/articles?error={quote_plus('Identifiant d article invalide.')}#categories")

    article = articles.find_one({"_id": article_oid})
    if article is None:
        return redirect(f"/articles?error={quote_plus('Article inexistant.')}#categories")

    if article_oid in category.get("article_ids", []):
        return redirect(f"/articles?cat={category_id}&error={quote_plus('Cet article est deja dans la categorie.')}#categories")

    categories.update_one(
        {"_id": category["_id"]},
        {
            "$addToSet": {"article_ids": article_oid},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    return redirect(f"/articles?cat={category_id}&success={quote_plus('Article ajoute a la categorie.')}#categories")


@app.route("/article/<id>/open")
def ouvrir_article(id):
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404)

    article = articles.find_one({"_id": oid})
    if article is None or not is_valid_http_url(article.get("url", "")):
        abort(404)

    consultation_document = {
        "article_id": oid,
        "consulted_at": datetime.now(timezone.utc),
    }
    current_user = get_current_user()
    if current_user is not None:
        consultation_document["user_id"] = current_user["_id"]

    consultations.insert_one(consultation_document)
    articles.update_one({"_id": oid}, {"$inc": {"consultations_count": 1}})

    return redirect(article["url"])


def generer_svg(titres, nb_mots=50):
    texte = " ".join(titres).lower()
    mots = re.findall(r"[a-zA-Zàâäéèêëîïôùûüç]{3,}", texte)
    mots_filtres = [mot for mot in mots if mot not in STOPWORDS_FR]

    frequences = Counter(mots_filtres).most_common(nb_mots)
    if not frequences:
        return None

    freq_max = frequences[0][1]
    freq_min = frequences[-1][1]

    def taille(freq):
        if freq_max == freq_min:
            return 32
        return int(14 + (freq - freq_min) / (freq_max - freq_min) * 46)

    couleurs = ["#58a6ff", "#3fb950", "#d29922", "#f78166", "#79b8ff", "#56d364", "#e3b341", "#ffa198"]
    largeur, hauteur, marge = 900, 520, 10
    rng = random.Random(42)
    boites = []

    def chevauche(x_pos, y_pos, width, height):
        x1 = x_pos - width / 2 - 6
        y1 = y_pos - height - 6
        x2 = x_pos + width / 2 + 6
        y2 = y_pos + 6
        return any(x1 < bx2 and x2 > bx1 and y1 < by2 and y2 > by1 for bx1, by1, bx2, by2 in boites)

    def hors_cadre(x_pos, y_pos, width, height):
        return (
            x_pos - width / 2 < marge
            or x_pos + width / 2 > largeur - marge
            or y_pos - height < marge
            or y_pos > hauteur - marge
        )

    elements = []

    for mot, freq in frequences:
        px = taille(freq)
        width = len(mot) * px * 0.6
        height = px
        couleur = rng.choice(couleurs)

        for _ in range(200):
            x_pos = rng.randint(int(width / 2) + marge, int(largeur - width / 2) - marge)
            y_pos = rng.randint(height + marge, hauteur - marge)

            if not chevauche(x_pos, y_pos, width, height) and not hors_cadre(x_pos, y_pos, width, height):
                boites.append((x_pos - width / 2, y_pos - height, x_pos + width / 2, y_pos))
                elements.append(
                    f'<text x="{x_pos}" y="{y_pos}" font-size="{px}" fill="{couleur}" '
                    f'font-family="Arial, sans-serif" text-anchor="middle">{escape(mot)}</text>'
                )
                break

    if not elements:
        return None

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largeur}" height="{hauteur}" style="background:#161b22;">'
        + "".join(elements)
        + "</svg>"
    )


def generer_svg_interactif(titres, nb_mots=50, word_links=None, word_tooltips=None):
    word_links = word_links or {}
    word_tooltips = word_tooltips or {}

    frequences = build_wordcloud_frequencies(titres, nb_mots)
    if not frequences:
        return None

    freq_values = [freq for _, _, freq in frequences]
    freq_max = max(freq_values)
    freq_min = min(freq_values)
    density_ratio = min(1.0, max(0.0, (nb_mots - 100) / 150))
    min_font_size = 15 - int(3 * density_ratio)
    max_font_size = 53 - int(15 * density_ratio)
    default_font_size = 32 - int(8 * density_ratio)

    def taille(freq):
        if freq_max == freq_min:
            return default_font_size

        ratio = (math.log(freq) - math.log(freq_min)) / (math.log(freq_max) - math.log(freq_min))
        return int(min_font_size + ratio * (max_font_size - min_font_size))

    couleurs = ["#8b18b6", "#2468d8", "#28a745", "#1ca9c9", "#b23a48", "#7a2cc8", "#168f61", "#2d57c8"]
    largeur, hauteur, marge = 960, 580, 8
    rng = random.Random(42)
    boites = []
    centre_x, centre_y = largeur / 2, hauteur / 2
    espace_mots = 1.2
    collision_scale = 1.0

    def chevauche(x_pos, y_pos, width, height):
        compact_width = width * collision_scale
        compact_height = height * collision_scale
        x1 = x_pos - compact_width / 2 - espace_mots
        y1 = y_pos - compact_height / 2 - espace_mots
        x2 = x_pos + compact_width / 2 + espace_mots
        y2 = y_pos + compact_height / 2 + espace_mots
        return any(x1 < bx2 and x2 > bx1 and y1 < by2 and y2 > by1 for bx1, by1, bx2, by2 in boites)

    def hors_cadre(x_pos, y_pos, width, height):
        return (
            x_pos - width / 2 < marge
            or x_pos + width / 2 > largeur - marge
            or y_pos - height / 2 < marge
            or y_pos + height / 2 > hauteur - marge
        )

    def get_word_orientation(index, word, font_size):
        if index == 0 or len(word) > 11 or font_size >= 44:
            return 0
        orientations = [0, 0, -90, 0, 0, 18, 0, -18, 0, 90, 0, 0, 12, 0, -12]
        return orientations[index % len(orientations)]

    def rotated_bounds(text_width, text_height, rotation):
        angle = math.radians(rotation)
        cos_angle = abs(math.cos(angle))
        sin_angle = abs(math.sin(angle))
        return (
            text_width * cos_angle + text_height * sin_angle,
            text_width * sin_angle + text_height * cos_angle,
        )

    def build_word_markup(mot, word_key, x_pos, y_pos, font_size, couleur, rotation):
        tooltip = escape(word_tooltips.get(word_key, f"Ouvrir un article contenant {mot}"))
        transform = f' transform="rotate({rotation} {x_pos} {y_pos})"' if rotation else ""
        text_markup = (
            f'<text class="cloud-word" x="{x_pos}" y="{y_pos}" font-size="{font_size}" fill="{couleur}" '
            f'font-family="Trebuchet MS, Segoe UI, Arial, sans-serif" font-style="italic" font-weight="700" '
            f'text-anchor="middle" dominant-baseline="middle" paint-order="stroke" stroke="#fffdf8" '
            f'stroke-width="0.12" data-rotation="{rotation}"{transform}>'
            f"<title>{tooltip}</title>{escape(mot)}</text>"
        )
        href = word_links.get(word_key)
        if href:
            return (
                f'<a class="cloud-link" href="{escape(href)}" xlink:href="{escape(href)}" target="_blank" '
                f'tabindex="0" aria-label="{tooltip}">'
                f"{text_markup}</a>"
            )
        return text_markup

    elements = []

    for index, (mot, word_key, freq) in enumerate(frequences):
        px = taille(freq)
        text_width = len(mot) * px * (0.54 if px >= 38 else 0.56)
        text_height = px * 0.9
        rotation = get_word_orientation(index, mot, px)
        width, height = rotated_bounds(text_width, text_height, rotation)
        couleur = rng.choice(couleurs)
        placed = False

        base_angle = rng.random() * math.tau
        for step in range(1100):
            radius = 3 + step * 1.05
            angle = base_angle + step * 0.27 + index * 0.17
            x_pos = centre_x + math.cos(angle) * radius * 1.0
            y_pos = centre_y + math.sin(angle) * radius * 0.74

            if not chevauche(x_pos, y_pos, width, height) and not hors_cadre(x_pos, y_pos, width, height):
                compact_width = width * collision_scale
                compact_height = height * collision_scale
                boites.append(
                    (
                        x_pos - compact_width / 2,
                        y_pos - compact_height / 2,
                        x_pos + compact_width / 2,
                        y_pos + compact_height / 2,
                    )
                )
                elements.append(build_word_markup(mot, word_key, x_pos, y_pos, px, couleur, rotation))
                placed = True
                break

        if placed:
            continue

        for _ in range(420):
            radius = rng.random() ** 2.0
            angle = rng.random() * math.tau
            max_x = (largeur - width - 2 * marge) / 2
            max_y = (hauteur - height - 2 * marge) / 2
            x_pos = centre_x + math.cos(angle) * radius * max_x
            y_pos = centre_y + math.sin(angle) * radius * max_y
            if not chevauche(x_pos, y_pos, width, height) and not hors_cadre(x_pos, y_pos, width, height):
                compact_width = width * collision_scale
                compact_height = height * collision_scale
                boites.append(
                    (
                        x_pos - compact_width / 2,
                        y_pos - compact_height / 2,
                        x_pos + compact_width / 2,
                        y_pos + compact_height / 2,
                    )
                )
                elements.append(build_word_markup(mot, word_key, x_pos, y_pos, px, couleur, rotation))
                break

    if not elements:
        return None

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{largeur}" height="{hauteur}" viewBox="0 0 {largeur} {hauteur}" role="img" '
        f'aria-label="Nuage de mots interactif" class="wordcloud-svg" onselectstart="return false">'
        "<defs>"
        '<filter id="soft-word-shadow" x="-20%" y="-20%" width="140%" height="140%">'
        '<feDropShadow dx="0" dy="1" stdDeviation="0.45" flood-color="#000000" flood-opacity="0.10"/>'
        "</filter>"
        "</defs>"
        "<style>"
        ".wordcloud-svg,.cloud-word,.cloud-link{user-select:none;-webkit-user-select:none;-ms-user-select:none;}"
        ".cloud-word{transition:filter .18s ease,fill .18s ease;letter-spacing:0;pointer-events:visiblePainted;filter:url(#soft-word-shadow);}"
        ".cloud-link{text-decoration:none;outline:none;}"
        ".cloud-link .cloud-word{cursor:pointer;}"
        ".cloud-link:hover .cloud-word,.cloud-link:focus .cloud-word{filter:drop-shadow(0 0 12px rgba(15,118,110,.24));fill:#0b4f4a;}"
        ".cloud-link:focus .cloud-word{stroke:#d7c8ad;stroke-width:1.2;}"
        "</style>"
        f'<rect x="0.5" y="0.5" width="{largeur - 1}" height="{hauteur - 1}" rx="18" fill="#fffdf8" stroke="#eadfca"/>'
        + "".join(elements)
        + "</svg>"
    )


@app.route("/wordcloud")
def nuage_de_mots():
    date_debut = request.args.get("date_debut", "").strip()
    date_fin = request.args.get("date_fin", "").strip()
    nb_mots = parse_positive_int(request.args.get("nb_mots", "30"), default=30, minimum=1)

    svg, erreur = build_wordcloud_svg(date_debut, date_fin, nb_mots)
    if erreur:
        return render_template("wordcloud.html", svg=None, erreur=erreur)

    return render_template("wordcloud.html", svg=svg, erreur=None)


@app.route("/wordcloud/download")
def telecharger_nuage_de_mots():
    date_debut = request.args.get("date_debut", "").strip()
    date_fin = request.args.get("date_fin", "").strip()
    nb_mots = parse_positive_int(request.args.get("nb_mots", "30"), default=30, minimum=1)

    svg, erreur = build_wordcloud_svg(date_debut, date_fin, nb_mots)
    if erreur:
        return Response(erreur, status=400, content_type="text/plain; charset=utf-8")
    if not svg:
        return Response(
            "Aucun article disponible pour generer le nuage de mots.",
            status=404,
            content_type="text/plain; charset=utf-8",
        )

    filename_parts = ["nuage_mots"]
    if date_debut:
        filename_parts.append(date_debut)
    if date_fin:
        filename_parts.append(date_fin)
    filename = "_".join(filename_parts) + ".svg"

    svg_bytes = BytesIO(('<?xml version="1.0" encoding="utf-8"?>\n' + svg).encode("utf-8"))
    return send_file(
        svg_bytes,
        mimetype="image/svg+xml",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/subscriptions")
def liste_subscriptions():
    return render_subscriptions_page()


@app.route("/subscriptions/add", methods=["POST"])
def ajouter_subscription():
    source_name = request.form.get("source_name", "").strip()
    sitemap_url = request.form.get("sitemap_url", "").strip()
    refresh_interval = parse_positive_int(
        request.form.get("refresh_interval_minutes", "60"),
        default=None,
        minimum=1,
    )

    if not source_name or not sitemap_url:
        return render_subscriptions_page("Le nom de la source et l'URL du sitemap sont obligatoires.")

    if not is_valid_http_url(sitemap_url):
        return render_subscriptions_page("L'URL du sitemap doit etre une URL HTTP(S) valide.")

    if refresh_interval is None:
        return render_subscriptions_page("L'intervalle doit etre un entier positif (en minutes).")

    try:
        subscriptions.insert_one(
            {
                "source_name": source_name,
                "sitemap_url": sitemap_url,
                "active": True,
                "refresh_interval_minutes": refresh_interval,
                "last_fetch_at": None,
            }
        )
    except DuplicateKeyError:
        return render_subscriptions_page(f"Cet abonnement existe deja : {sitemap_url}")

    synchroniser_jobs()
    return redirect("/subscriptions")


@app.route("/subscriptions/delete/<id>", methods=["POST"])
def supprimer_subscription(id):
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404)

    subscriptions.delete_one({"_id": oid})
    synchroniser_jobs()
    return redirect("/subscriptions")


@app.route("/subscriptions/toggle/<id>", methods=["POST"])
def basculer_subscription(id):
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404)

    abonnement = subscriptions.find_one({"_id": oid})
    if abonnement is None:
        abort(404)

    subscriptions.update_one({"_id": oid}, {"$set": {"active": not abonnement.get("active", True)}})
    synchroniser_jobs()
    return redirect("/subscriptions")


@app.route("/subscriptions/interval/<id>", methods=["POST"])
def modifier_intervalle(id):
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404)

    nouvel_intervalle = parse_positive_int(
        request.form.get("refresh_interval_minutes", ""),
        default=None,
        minimum=1,
    )
    if nouvel_intervalle is None:
        return render_subscriptions_page("L'intervalle doit etre un entier positif (en minutes).")

    subscriptions.update_one(
        {"_id": oid},
        {"$set": {"refresh_interval_minutes": nouvel_intervalle}},
    )
    synchroniser_jobs()
    return redirect("/subscriptions")


@app.route("/subscriptions/update", methods=["POST"])
def mettre_a_jour():
    resume = mettre_a_jour_tous_les_abonnements()
    return render_subscriptions_page(resume=resume)


@app.route("/subscriptions/update/<id>", methods=["POST"])
def mettre_a_jour_source(id):
    try:
        oid = ObjectId(id)
    except InvalidId:
        abort(404)

    abonnement = subscriptions.find_one({"_id": oid})
    if abonnement is None:
        abort(404)

    resume = build_update_resume()

    try:
        inseres, doublons = mettre_a_jour_abonnement_document(abonnement)
        append_update_success(resume, abonnement["source_name"], inseres, doublons)
    except Exception as exc:
        append_update_error(resume, abonnement["source_name"], exc)

    return render_subscriptions_page(resume=resume)


def configure_logging():
    if logging.getLogger().handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def main():
    configure_logging()
    try:
        ensure_indexes()
    except RuntimeError as exc:
        LOGGER.warning("Demarrage sans MongoDB disponible: %s", exc)

    try:
        demarrer_scheduler()
    except Exception as exc:
        LOGGER.warning("Scheduler non demarre car MongoDB est indisponible: %s", exc)

    app.run(
        debug=SETTINGS.flask_debug,
        host=SETTINGS.flask_host,
        port=SETTINGS.flask_port,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
