from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .collector import collect_country, parse_date, same_story


def collect_social_source(
    source: dict[str, Any],
    policy: dict[str, Any],
    max_age_hours: int,
    limit: int,
    progress: Callable[[str], None] | None = None,
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
        "keywords": list(source.get("keywords") or []),
        "exclude_keywords": list(source.get("exclude_keywords") or []),
        "preferred_domains": list(source.get("preferred_domains") or []),
    }

    batches: list[dict[str, Any]] = []
    feeds = [str(item).strip() for item in source.get("feed_urls", []) if str(item).strip()]
    if source.get("search_enabled", True):
        emit("开始搜索公开网页和已建立索引的公开贴文")
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

