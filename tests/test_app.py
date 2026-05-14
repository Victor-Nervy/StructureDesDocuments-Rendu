import sys
import unittest
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import app as app_module


class FakeResponse:
    def __init__(self, content):
        self.content = content.encode("utf-8")

    def raise_for_status(self):
        return None


class AppTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def test_convertir_date_normalizes_to_utc(self):
        converted = app_module.convertir_date("2026-03-10T14:32:00+02:00")

        self.assertIsNotNone(converted)
        self.assertEqual(converted.isoformat(), "2026-03-10T12:32:00+00:00")
        self.assertEqual(converted.tzinfo, timezone.utc)

    def test_build_publication_date_filter_rejects_inverted_range(self):
        filtre, erreur = app_module.build_publication_date_filter("2026-03-10", "2026-03-01")

        self.assertEqual(filtre, {})
        self.assertEqual(
            erreur,
            "La date de debut doit etre anterieure ou egale a la date de fin.",
        )

    def test_build_consultation_date_filter_rejects_inverted_range(self):
        filtre, erreur = app_module.build_consultation_date_filter(
            "2026-03-10T18:00",
            "2026-03-10T08:00",
        )

        self.assertEqual(filtre, {})
        self.assertEqual(
            erreur,
            "La date de debut de consultation doit etre anterieure ou egale a la date de fin.",
        )

    def test_is_valid_http_url_accepts_http_and_https(self):
        self.assertTrue(app_module.is_valid_http_url("https://example.com/sitemap.xml"))
        self.assertTrue(app_module.is_valid_http_url("http://example.com/news"))
        self.assertFalse(app_module.is_valid_http_url("ftp://example.com/file.xml"))
        self.assertFalse(app_module.is_valid_http_url("example.com/no-scheme"))

    def test_http_session_ignores_system_proxy_environment(self):
        session = app_module.create_http_session()

        self.assertFalse(session.trust_env)

    def test_lire_sitemap_follows_sitemap_index_children(self):
        responses = {
            "https://example.com/sitemap.xml": FakeResponse(
                """<?xml version="1.0" encoding="UTF-8"?>
                <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <sitemap><loc>https://example.com/sitemap-news.xml</loc></sitemap>
                </sitemapindex>"""
            ),
            "https://example.com/sitemap-news.xml": FakeResponse(
                """<?xml version="1.0" encoding="UTF-8"?>
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
                        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
                  <url>
                    <loc>https://example.com/article</loc>
                    <news:news>
                      <news:title>Titre test</news:title>
                      <news:publication_date>2026-05-04T10:00:00+00:00</news:publication_date>
                    </news:news>
                  </url>
                </urlset>"""
            ),
        }

        with patch.object(app_module.HTTP_SESSION, "get", side_effect=lambda url, timeout: responses[url]):
            resultats = app_module.lire_sitemap("https://example.com/sitemap.xml")

        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0]["loc"], "https://example.com/article")
        self.assertEqual(resultats[0]["title"], "Titre test")

    def test_parse_positive_int_rejects_zero(self):
        self.assertIsNone(app_module.parse_positive_int("0", default=None, minimum=1))
        self.assertEqual(app_module.parse_positive_int("15", default=None, minimum=1), 15)

    def test_build_category_title_condition_rejects_unknown_category(self):
        condition, erreur = app_module.build_category_title_condition("inconnue")

        self.assertIsNone(condition)
        self.assertEqual(erreur, "Categorie de mots-cles inconnue.")

    def test_normalize_image_url_supports_relative_paths(self):
        normalized = app_module.normalize_image_url(
            "https://example.com/articles/123",
            "/images/couverture.jpg",
        )

        self.assertEqual(normalized, "https://example.com/images/couverture.jpg")

    def test_normalize_image_url_keeps_valid_commas_in_image_paths(self):
        normalized = app_module.normalize_image_url(
            "https://www.lequipe.fr/article",
            "https://www.lequipe.fr/_medias/img-photo-jpg/photo/123/0:0,1500:1000-640-427-75/image.jpg",
        )

        self.assertEqual(
            normalized,
            "https://www.lequipe.fr/_medias/img-photo-jpg/photo/123/0:0,1500:1000-640-427-75/image.jpg",
        )

    def test_should_refresh_article_image_rejects_incomplete_lequipe_sitemap_url(self):
        self.assertTrue(
            app_module.should_refresh_article_image(
                "https://medias.lequipe.fr/img-photo-jpg/photo/123/0:0"
            )
        )

    def test_generer_svg_returns_none_for_only_stopwords(self):
        svg = app_module.generer_svg(["le la les de du des"], nb_mots=10)
        self.assertIsNone(svg)

    def test_extract_title_words_filters_generic_cloud_words(self):
        mots = app_module.extract_title_words(
            "Après le retour contre Paris en mars, toujours en match, faut voir le titre"
        )

        self.assertNotIn("après", mots)
        self.assertNotIn("retour", mots)
        self.assertNotIn("contre", mots)
        self.assertNotIn("mars", mots)
        self.assertNotIn("toujours", mots)
        self.assertNotIn("match", mots)
        self.assertNotIn("faut", mots)
        self.assertNotIn("titre", mots)
        self.assertIn("paris", mots)

    def test_build_wordcloud_frequencies_prefers_repeated_words_when_cloud_is_dense(self):
        repeated_words = [
            "france", "guerre", "ligue", "tour", "foot", "paris",
            "iran", "orient", "prix", "monde", "coupe", "nations",
        ]
        titles = repeated_words + repeated_words + ["alpha", "bravo", "charlie", "delta"]

        frequencies = app_module.build_wordcloud_frequencies(titles, nb_mots=12)
        kept_words = {display_word for display_word, _, _ in frequencies}

        self.assertEqual(len(frequencies), 12)
        self.assertIn("france", kept_words)
        self.assertIn("guerre", kept_words)
        self.assertNotIn("alpha", kept_words)
        self.assertNotIn("bravo", kept_words)

    def test_articles_route_returns_error_for_invalid_dates(self):
        with patch.object(app_module, "articles") as mock_articles:
            mock_articles.distinct.return_value = []

            response = self.client.get("/articles?date_debut=2026-03-10&date_fin=2026-03-01")

        self.assertEqual(response.status_code, 200)
        self.assertIn("La date de debut doit etre anterieure ou egale a la date de fin.", response.get_data(as_text=True))
        mock_articles.find.assert_not_called()

    def test_profile_requires_login(self):
        response = self.client.get("/profile")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_add_subscription_rejects_invalid_url(self):
        with patch.object(app_module, "subscriptions") as mock_subscriptions:
            mock_subscriptions.find.return_value.sort.return_value = []

            response = self.client.post(
                "/subscriptions/add",
                data={
                    "source_name": "Source test",
                    "sitemap_url": "ftp://invalid.example.com/sitemap.xml",
                    "refresh_interval_minutes": "30",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("L'URL du sitemap doit etre une URL HTTP(S) valide.", response.get_data(as_text=True))
        mock_subscriptions.insert_one.assert_not_called()

    def test_inserer_articles_skips_image_fetch_for_known_duplicate(self):
        article = {
            "loc": "https://example.com/article",
            "title": "Titre",
            "publication_date": "2026-03-10",
        }

        with patch.object(app_module, "articles") as mock_articles, patch.object(
            app_module,
            "recuperer_image_article",
        ) as mock_fetch_image:
            mock_articles.find_one.return_value = {
                "_id": "existing",
                "image_url": "https://example.com/image.jpg",
            }

            inseres, doublons = app_module.inserer_articles([article], "sub-id", "Source test")

        self.assertEqual(inseres, 0)
        self.assertEqual(doublons, 1)
        mock_fetch_image.assert_not_called()
        mock_articles.insert_one.assert_not_called()

    def test_inserer_articles_uses_sitemap_image_before_fetching_article_page(self):
        article = {
            "loc": "https://example.com/article",
            "title": "Titre",
            "publication_date": "2026-03-10",
            "image_url": "/images/couverture.jpg",
        }

        with patch.object(app_module, "articles") as mock_articles, patch.object(
            app_module,
            "recuperer_image_article",
        ) as mock_fetch_image:
            mock_articles.find_one.return_value = None

            inseres, doublons = app_module.inserer_articles([article], "sub-id", "Source test")

        self.assertEqual(inseres, 1)
        self.assertEqual(doublons, 0)
        mock_fetch_image.assert_not_called()
        inserted_document = mock_articles.insert_one.call_args.args[0]
        self.assertEqual(inserted_document["image_url"], "https://example.com/images/couverture.jpg")

    def test_inserer_articles_refreshes_broken_duplicate_image(self):
        article = {
            "loc": "https://www.lequipe.fr/article",
            "title": "Titre",
            "publication_date": "2026-03-10",
            "image_url": "https://medias.lequipe.fr/img-photo-jpg/photo/123/0:0",
        }

        with patch.object(app_module, "articles") as mock_articles, patch.object(
            app_module,
            "recuperer_image_article",
            return_value="https://www.lequipe.fr/_medias/img-photo-jpg/photo/123/0:0,1500:1000-640-427-75/image.jpg",
        ):
            mock_articles.find_one.return_value = {
                "_id": "existing",
                "image_url": "https://medias.lequipe.fr/img-photo-jpg/photo/123/0:0",
            }

            inseres, doublons = app_module.inserer_articles([article], "sub-id", "Source test")

        self.assertEqual(inseres, 0)
        self.assertEqual(doublons, 1)
        mock_articles.update_one.assert_called_once()
        update_document = mock_articles.update_one.call_args.args[1]
        self.assertEqual(
            update_document["$set"]["image_url"],
            "https://www.lequipe.fr/_medias/img-photo-jpg/photo/123/0:0,1500:1000-640-427-75/image.jpg",
        )

    def test_wordcloud_download_returns_svg_attachment(self):
        with patch.object(app_module, "build_wordcloud_svg", return_value=("<svg></svg>", None)):
            response = self.client.get("/wordcloud/download?nb_mots=20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/svg+xml")
        self.assertIn("attachment; filename=", response.headers["Content-Disposition"])

    def test_wordcloud_form_accepts_custom_word_count(self):
        with patch.object(app_module, "build_wordcloud_svg", return_value=("<svg></svg>", None)):
            response = self.client.get("/wordcloud?nb_mots=150")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('type="number"', html)
        self.assertIn('value="150"', html)
        self.assertNotIn("<select id=\"nb_mots\"", html)

    def test_build_wordcloud_svg_contains_clickable_article_links(self):
        with patch.object(app_module, "articles") as mock_articles:
            mock_articles.find.return_value.sort.return_value = [
                {
                    "_id": "507f191e810c19729de860ea",
                    "title": "Foot france et ligue des champions",
                    "publication_date": None,
                }
            ]

            svg, erreur = app_module.build_wordcloud_svg("", "", 10)

        self.assertIsNone(erreur)
        self.assertIsNotNone(svg)
        self.assertIn('class="cloud-link"', svg)
        self.assertIn("/article/507f191e810c19729de860ea/open", svg)

    def test_interactive_wordcloud_contains_rotated_words_and_disables_selection(self):
        titles = [
            "france tennis football",
            "france tennis football",
            "paris rugby mercato",
            "paris rugby mercato",
            "rome cyclisme finale",
            "rome cyclisme finale",
        ]

        svg = app_module.generer_svg_interactif(titles, nb_mots=8)

        self.assertIsNotNone(svg)
        self.assertIn('data-rotation="0"', svg)
        self.assertIn("rotate(-90", svg)
        self.assertIn("rotate(18", svg)
        self.assertIn('dominant-baseline="middle"', svg)
        self.assertIn('font-style="italic"', svg)
        self.assertIn('font-weight="700"', svg)
        self.assertIn("user-select:none", svg)
        self.assertIn('onselectstart="return false"', svg)

    def test_interactive_wordcloud_uses_compact_visual_style(self):
        titles = [
            "football tennis ligue coupe finale",
            "football tennis ligue coupe finale",
            "basket psg france monde champions",
            "basket psg france monde champions",
            "tour rugby victoire saison equipe",
            "tour rugby victoire saison equipe",
        ]

        svg = app_module.generer_svg_interactif(titles, nb_mots=20)

        self.assertIsNotNone(svg)
        self.assertIn('font-style="italic"', svg)
        self.assertIn('font-weight="700"', svg)
        self.assertIn('stroke-width="0.12"', svg)
        self.assertIn('fill="#fffdf8"', svg)

    def test_update_single_subscription_route_targets_requested_source(self):
        subscription_id = "507f191e810c19729de860ea"
        abonnement = {
            "_id": "sub-id",
            "source_name": "Source test",
            "sitemap_url": "https://example.com/sitemap.xml",
        }

        with patch.object(app_module, "subscriptions") as mock_subscriptions, patch.object(
            app_module,
            "mettre_a_jour_abonnement_document",
            return_value=(4, 2),
        ) as mock_update, patch.object(
            app_module,
            "render_subscriptions_page",
            return_value="ok",
        ) as mock_render:
            mock_subscriptions.find_one.return_value = abonnement

            response = self.client.post(f"/subscriptions/update/{subscription_id}")

        self.assertEqual(response.status_code, 200)
        mock_update.assert_called_once_with(abonnement)
        _, kwargs = mock_render.call_args
        self.assertEqual(kwargs["resume"]["traites"], 1)
        self.assertEqual(kwargs["resume"]["inseres"], 4)
        self.assertEqual(kwargs["resume"]["doublons"], 2)
        self.assertEqual(kwargs["resume"]["sources"], ["Source test"])

    def test_open_article_stores_user_id_when_logged_in(self):
        user_id = "507f191e810c19729de860ea"
        article_id = "507f191e810c19729de860eb"

        with self.client.session_transaction() as flask_session:
            flask_session["user_id"] = user_id

        with patch.object(app_module, "users") as mock_users, patch.object(
            app_module,
            "articles",
        ) as mock_articles, patch.object(
            app_module,
            "consultations",
        ) as mock_consultations:
            mock_users.find_one.return_value = {
                "_id": ObjectId(user_id),
                "username": "demo",
                "email": "demo@example.com",
            }
            mock_articles.find_one.return_value = {
                "_id": ObjectId(article_id),
                "url": "https://example.com/article",
            }

            response = self.client.get(f"/article/{article_id}/open")

        self.assertEqual(response.status_code, 302)
        inserted_document = mock_consultations.insert_one.call_args.args[0]
        self.assertEqual(inserted_document["user_id"], ObjectId(user_id))


if __name__ == "__main__":
    unittest.main()

    
