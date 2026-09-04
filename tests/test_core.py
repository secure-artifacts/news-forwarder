from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.collector import (
    compact_summary,
    external_url,
    google_news_url,
    keyword_groups,
    matches_keywords,
    same_story,
    search_queries,
    source_allowed,
)
from app.database import Database
from app.deliveries import TeamsSender
from app.social import RECOMMENDED_SOCIAL_TOPICS, social_keywords
from app.settings_api import bounded_int, normalize_domains, normalize_list, normalize_urls, slugify_country
from app.translator import extract_key_points, translation_instruction
from app.web import version_tuple


class SourcePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "require_https": True,
            "allowed_domains": [],
            "blocked_domain_suffixes": [".cn", "xinhuanet.com"],
            "blocked_source_words": ["Xinhua", "新华"],
        }

    def test_allows_international_https(self):
        self.assertTrue(source_allowed("https://reuters.com/a", "", "Reuters", self.policy))

    def test_blocks_cn_and_named_chinese_source(self):
        self.assertFalse(source_allowed("https://example.cn/a", "", "Example", self.policy))
        self.assertFalse(source_allowed("https://news.google.com/a", "https://example.com", "Xinhua", self.policy))

    def test_blocks_local_and_private_article_urls(self):
        self.assertFalse(external_url("https://localhost/admin"))
        self.assertFalse(external_url("https://127.0.0.1/admin"))
        self.assertFalse(external_url("https://10.0.0.8/internal"))
        self.assertTrue(external_url("https://www.reuters.com/world/"))

    def test_keywords(self):
        country = {"keywords": ["economy"], "exclude_keywords": ["football"]}
        self.assertTrue(matches_keywords("Angola economy grows", country))
        self.assertFalse(matches_keywords("Angola football economy", country))

    def test_multiple_keywords_and_preferred_sites_build_ordered_searches(self):
        country = {
            "query": "Angola",
            "keywords": ["economy", "security"],
            "preferred_domains": ["reuters.com", "bbc.com"],
        }
        queries = search_queries(country)
        self.assertEqual(queries[0], ("(Angola) (economy OR security) site:reuters.com", 0))
        self.assertIn(("(Angola) (economy OR security)", 1), queries)
        self.assertTrue(all(priority == 0 for _, priority in queries[:2]))

    def test_large_keyword_list_is_fully_grouped(self):
        keywords = [f"topic {index}" for index in range(40)]
        groups = keyword_groups(keywords)
        self.assertEqual([item for group in groups for item in group], keywords)
        self.assertGreater(len(groups), 1)
        queries = search_queries({"query": "Angola", "keywords": keywords, "preferred_domains": []})
        self.assertIn('"topic 39"', " ".join(query for query, _ in queries))

    def test_google_news_query_uses_configured_lookback(self):
        country = {"query": "Facebook", "language": "en-US", "region": "US", "ceid": "US:en"}
        self.assertIn("when%3A30d", google_news_url(country, max_age_hours=720))
        self.assertIn("when%3A1d", google_news_url(country, max_age_hours=12))
        self.assertIn("when%3A365d", google_news_url(country, max_age_hours=99999))

    def test_social_research_topics_are_added_and_can_be_disabled(self):
        source = {"keywords": ["Facebook ads", "algorithm update"]}
        expanded = social_keywords(source)
        self.assertIn("Reels algorithm", expanded)
        self.assertIn("advertising policy", expanded)
        self.assertEqual(expanded.count("algorithm update"), 1)
        self.assertEqual(social_keywords(source, False), source["keywords"])
        self.assertGreater(len(RECOMMENDED_SOCIAL_TOPICS), 15)

    def test_same_event_deduplicates_similar_headlines(self):
        first = "Angola central bank keeps interest rates unchanged - Reuters"
        second = "Angola central bank keeps interest rate unchanged"
        unrelated = "Mozambique opens a new coastal railway project"
        self.assertTrue(same_story(first, second))
        self.assertFalse(same_story(first, unrelated))

    def test_article_body_is_preferred_for_compact_summary(self):
        body = (
            "The government approved the investment package on Tuesday. "
            "It will fund roads and hospitals across three provinces. "
            "Officials said implementation begins in September. Extra sentence."
        )
        result = compact_summary("Investment approved", "Short feed text only.", body)
        self.assertIn("fund roads and hospitals", result)
        self.assertNotIn("Extra sentence", result)


class DatabaseTests(unittest.TestCase):
    def test_deduplicates_articles(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "news.db")
            item = {
                "fingerprint": "abc",
                "country_id": "angola",
                "country_name": "安哥拉",
                "title": "Title",
                "title_zh": "标题",
                "summary": "Summary",
                "summary_zh": "摘要",
                "source": "Reuters",
                "url": "https://reuters.com/a",
                "published_at": None,
                "collected_at": "2026-01-01T00:00:00+00:00",
            }
            self.assertTrue(database.add_article(item))
            self.assertFalse(database.add_article(item))
            similar = {**item, "fingerprint": "def", "url": "https://bbc.com/b"}
            self.assertFalse(database.add_article(similar))
            unrelated = {
                **item, "fingerprint": "ghi", "url": "https://bbc.com/c",
                "title": "Mozambique opens a new coastal railway project",
            }
            self.assertTrue(database.add_article(unrelated))
            self.assertEqual(database.dashboard()["stats"]["total"], 2)

    def test_run_logs_are_persisted_for_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "news.db")
            run_id = database.start_run("2026-01-01T00:00:00+00:00")
            database.add_run_log(run_id, "2026-01-01T00:00:01+00:00", "开始搜索优先网站")
            logs = database.dashboard()["logs"]
            self.assertEqual(logs[-1]["message"], "开始搜索优先网站")
            self.assertEqual(logs[-1]["run_id"], run_id)

    def test_social_items_are_separate_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "news.db")
            item = {
                "fingerprint": "social-1", "platform_id": "whatsapp",
                "platform_name": "WhatsApp", "title": "WhatsApp updates privacy policy",
                "title_zh": "", "summary": "Policy details", "summary_zh": "",
                "source": "WhatsApp", "url": "https://www.whatsapp.com/legal/update",
                "published_at": "2026-09-01T10:00:00+00:00",
                "collected_at": "2026-09-01T11:00:00+00:00",
            }
            self.assertTrue(database.add_social_item(item))
            self.assertFalse(database.add_social_item(item))
            dashboard = database.social_dashboard()
            self.assertEqual(dashboard["stats"]["total"], 1)
            self.assertEqual(dashboard["items"][0]["platform_name"], "WhatsApp")


class TeamsTests(unittest.TestCase):
    def test_adaptive_card_contains_chinese_and_link(self):
        sender = TeamsSender("https://example.com", {"payload_mode": "adaptive_card"})
        article = {
            "id": 1, "title": "Title", "title_zh": "中文标题", "summary": "Summary",
            "summary_zh": "中文摘要", "source": "Reuters", "url": "https://reuters.com/a",
        }
        payload = sender._payload("安哥拉", [article])
        rendered = str(payload)
        self.assertIn("中文标题", rendered)
        self.assertIn("https://reuters.com/a", rendered)


class SettingsAndTranslationTests(unittest.TestCase):
    def test_country_keyword_input_accepts_comma_separated_text(self):
        self.assertEqual(normalize_list("economia, governo, investimento"), [
            "economia", "governo", "investimento"
        ])

    def test_country_id_and_domains_are_normalized_automatically(self):
        self.assertEqual(slugify_country("Cabo Verde", "佛得角"), "cabo-verde")
        self.assertEqual(
            normalize_domains("https://www.reuters.com/world, bbc.com/news"),
            ["reuters.com", "bbc.com"],
        )

    def test_schedule_bounds_are_validated(self):
        self.assertEqual(bounded_int("23", 0, 23), 23)
        with self.assertRaises(ValueError):
            bounded_int(24, 0, 23)

    def test_social_feed_urls_only_accept_https(self):
        self.assertEqual(
            normalize_urls("https://example.com/feed.xml, http://unsafe.test/feed"),
            ["https://example.com/feed.xml"],
        )

    def test_translation_uses_only_a_compact_source_excerpt(self):
        source = "One. Two. Three. Four should not be included."
        self.assertEqual(extract_key_points(source), "One. Two. Three.")
        self.assertIn("不要逐字翻译全文", translation_instruction())

    def test_version_comparison_handles_release_tags(self):
        self.assertGreater(version_tuple("v1.4.1"), version_tuple("1.4.0"))
        self.assertEqual(version_tuple("v1.4.0"), version_tuple("1.4.0"))


if __name__ == "__main__":
    unittest.main()
