from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .collector import collect_country, parse_date, same_story


RECOMMENDED_SOCIAL_TOPICS = [
    "policy update",
    "community standards",
    "terms of service",
    "privacy update",
    "content moderation",
    "monetization policy",
    "advertising policy",
    "ads manager",
    "campaign optimization",
    "algorithm update",
    "ranking signals",
    "recommendation system",
    "organic reach",
    "content strategy",
    "viral content",
    "engagement growth",
    "Reels algorithm",
    "Reels best practices",
    "short-form video",
    "creator tips",
    "audience growth",
]


def social_keywords(source: dict[str, Any], include_recommended: bool = True) -> list[str]:
    configured = [str(item).strip() for item in source.get("keywords", []) if str(item).strip()]
    combined = configured + (RECOMMENDED_SOCIAL_TOPICS if include_recommended else [])
    unique: list[str] = []
    seen: set[str] = set()
    for item in combined:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def collect_social_source(
    source: dict[str, Any],
    policy: dict[str, Any],
    max_age_hours: int,
    limit: int,
    progress: Callable[[str], None] | None = None,
    include_recommended_topics: bool = True,
) -> list[dict[str, Any]]:
    """Collect public platform updates from searches and optional RSS/Atom feeds."""
    emit = progress or (lambda _: None)
    platform_id = str(source.get("id") or "social")
    platform_name = str(source.get("name") or source.get("platform") or "社交平台")
    base = {
        "id": f"social-{platform_id}",
        "name": platform_name,
        "query": str(source.get("query") or platform_name),
        "language": str(source.get("language") or "en-US"),
        "region": str(source.get("region") or "US"),
        "ceid": str(source.get("ceid") or "US:en"),
        "source_language": str(source.get("source_language") or "English"),
        "source_code": str(source.get("source_code") or "en"),
        "keywords": social_keywords(source, include_recommended_topics),
        "exclude_keywords": list(source.get("exclude_keywords") or []),
        "preferred_domains": list(source.get("preferred_domains") or []),
        # The Google query already includes the topic expressions. Do not reject
        # semantically related results just because the headline uses a synonym.
        "trust_topic_search": True,
        "latest_first": True,
    }

    batches: list[dict[str, Any]] = []
    feeds = [str(item).strip() for item in source.get("feed_urls", []) if str(item).strip()]
    if source.get("search_enabled", True):
        if include_recommended_topics:
            emit(f"已自动加入 {len(RECOMMENDED_SOCIAL_TOPICS)} 个政策、算法、广告与运营主题")
        emit("开始搜索公开网页、新闻资料和已建立索引的公开贴文")
        batches.extend(collect_country(base, policy, max_age_hours, limit, emit))
    for position, feed_url in enumerate(feeds, start=1):
        emit(f"读取公开 RSS/Atom {position}/{len(feeds)}")
        feed_config = {**base, "feed_url": feed_url}
        batches.extend(collect_country(feed_config, policy, max_age_hours, limit, emit))

    batches.sort(
        key=lambda item: (parse_date(item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    results: list[dict[str, Any]] = []
    for item in batches:
        if any(item["url"] == old["url"] or same_story(item["title"], old["title"]) for old in results):
            continue
        item["platform_id"] = platform_id
        item["platform_name"] = platform_name
        item.pop("country_id", None)
        item.pop("country_name", None)
        results.append(item)
        if len(results) >= limit:
            break
    return results
