from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from .settings import Settings


PROVIDERS = {"local_llama", "gemini", "groq", "openai"}
DEFAULT_MODELS = {
    "local_llama": "translategemma-4b",
    "gemini": "gemini-3.7-flash",
    "groq": "openai/gpt-oss-120b",
    "openai": "gpt-5.4-mini",
}


def settings_snapshot(settings: Settings) -> dict[str, Any]:
    translation = settings.raw.get("translation", {})
    destinations = settings.destinations
    return {
        "countries": settings.countries,
        "social_monitor": settings.social_monitor,
        "app": {
            "timezone": settings.app.get("timezone", "Europe/Lisbon"),
            "schedule_hour": int(settings.app.get("schedule_hour", 8)),
            "schedule_minute": int(settings.app.get("schedule_minute", 0)),
            "max_age_hours": int(settings.app.get("max_age_hours", 36)),
            "max_articles_per_country": int(
                settings.app.get("max_articles_per_country", 15)
            ),
        },
        "destinations": {
            "automatic_delivery": bool(destinations.get("automatic_delivery", False)),
            "teams": {
                **destinations.get("teams", {}),
                "webhook_configured": bool(settings.teams_webhook_url),
            },
            "google_sheets": {
                **destinations.get("google_sheets", {}),
                "spreadsheet_id": settings.spreadsheet_id,
                "credentials_path": settings.google_credentials_path,
            },
        },
        "translation": {
            **translation,
            "api_key_configured": bool(settings.translation_api_key),
            "api_key": "",
        },
        "provider_defaults": DEFAULT_MODELS,
    }


def apply_settings(settings: Settings, payload: dict[str, Any]) -> None:
    countries = payload.get("countries")
    if not isinstance(countries, list) or not countries:
        raise ValueError("请至少添加一个国家或地区")
    normalized_countries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(countries, start=1):
        name = str(item.get("name", "")).strip()
        query = str(item.get("query", "")).strip()
        if not name or not query:
            raise ValueError(f"第 {index} 个国家必须填写显示名称和搜索词")
        requested_id = str(item.get("id", "")).strip().lower()
        country_id = requested_id if valid_country_id(requested_id) else slugify_country(query, name)
        country_id = unique_country_id(country_id, seen)
        seen.add(country_id)
        normalized_countries.append(
            {
                "id": country_id,
                "name": name,
                "query": query,
                "language": str(item.get("language") or "en-US").strip(),
                "region": str(item.get("region") or "US").strip().upper(),
                "ceid": str(item.get("ceid") or "US:en").strip(),
                "source_language": str(item.get("source_language") or "Portuguese").strip(),
                "source_code": str(item.get("source_code") or "pt").strip(),
                "keywords": normalize_list(item.get("keywords")),
                "exclude_keywords": normalize_list(item.get("exclude_keywords")),
                "preferred_domains": normalize_domains(item.get("preferred_domains")),
            }
        )
    settings.raw["countries"] = normalized_countries

    social_payload = payload.get("social_monitor", settings.social_monitor)
    if not isinstance(social_payload, dict):
        raise ValueError("社交平台监测设置格式不正确")
    normalized_sources: list[dict[str, Any]] = []
    social_seen: set[str] = set()
    for index, item in enumerate(social_payload.get("sources", []), start=1):
        name = str(item.get("name") or "").strip()
        query = str(item.get("query") or name).strip()
        if not name or not query:
            raise ValueError(f"第 {index} 个社交平台必须填写名称和搜索词")
        source_id = slugify_country(query, name)
        source_id = unique_country_id(source_id, social_seen)
        social_seen.add(source_id)
        normalized_sources.append(
            {
                "id": source_id,
                "name": name,
                "query": query,
                "search_enabled": bool(item.get("search_enabled", True)),
                "keywords": normalize_list(item.get("keywords")),
                "exclude_keywords": normalize_list(item.get("exclude_keywords")),
                "preferred_domains": normalize_domains(item.get("preferred_domains")),
                "feed_urls": normalize_urls(item.get("feed_urls")),
                "language": str(item.get("language") or "en-US").strip(),
                "region": str(item.get("region") or "US").strip().upper(),
                "ceid": str(item.get("ceid") or "US:en").strip(),
                "source_language": str(item.get("source_language") or "English").strip(),
                "source_code": str(item.get("source_code") or "en").strip(),
            }
        )
    if bool(social_payload.get("enabled", False)) and not normalized_sources:
        raise ValueError("启用社交平台监测时，请至少添加一个平台")
    settings.raw["social_monitor"] = {
        "enabled": bool(social_payload.get("enabled", False)),
        "interval_minutes": bounded_int(social_payload.get("interval_minutes", 360), 15, 10080),
        "max_age_hours": bounded_int(social_payload.get("max_age_hours", 72), 1, 720),
        "max_items_per_source": bounded_int(social_payload.get("max_items_per_source", 20), 1, 100),
        "worksheet_name": str(social_payload.get("worksheet_name") or "Social Updates").strip(),
        "automatic_sheets": bool(social_payload.get("automatic_sheets", False)),
        "sources": normalized_sources,
    }

    app_payload = payload.get("app", {})
    settings.app.update(
        {
            "timezone": str(app_payload.get("timezone") or settings.app.get("timezone", "UTC")),
            "schedule_hour": bounded_int(app_payload.get("schedule_hour", 8), 0, 23),
            "schedule_minute": bounded_int(app_payload.get("schedule_minute", 0), 0, 59),
            "max_age_hours": bounded_int(app_payload.get("max_age_hours", 36), 1, 168),
            "max_articles_per_country": bounded_int(
                app_payload.get("max_articles_per_country", 15), 1, 100
            ),
        }
    )

    destinations_payload = payload.get("destinations", {})
    destinations = settings.destinations
    destinations["automatic_delivery"] = bool(
        destinations_payload.get("automatic_delivery", False)
    )
    teams_payload = destinations_payload.get("teams", {})
    teams = destinations.setdefault("teams", {})
    teams.update(
        {
            "enabled": bool(teams_payload.get("enabled", False)),
            "payload_mode": teams_payload.get("payload_mode", "adaptive_card"),
            "max_articles_per_message": bounded_int(
                teams_payload.get("max_articles_per_message", 8), 1, 15
            ),
        }
    )
    sheets_payload = destinations_payload.get("google_sheets", {})
    sheets = destinations.setdefault("google_sheets", {})
    sheets.update(
        {
            "enabled": bool(sheets_payload.get("enabled", True)),
            "worksheet_name": str(sheets_payload.get("worksheet_name") or "News").strip(),
        }
    )

    translation_payload = payload.get("translation", {})
    provider = str(translation_payload.get("provider") or "local_llama")
    if provider not in PROVIDERS:
        raise ValueError("不支持的翻译服务")
    translation = settings.raw.setdefault("translation", {})
    translation.update(
        {
            "enabled": bool(translation_payload.get("enabled", True)),
            "provider": provider,
            "model": str(translation_payload.get("model") or DEFAULT_MODELS[provider]).strip(),
            "base_url": "http://127.0.0.1:11435",
            "source_language": str(
                translation_payload.get("source_language") or "Portuguese"
            ).strip(),
            "source_code": str(translation_payload.get("source_code") or "pt").strip(),
            "key_points_only": True,
        }
    )

    secrets: dict[str, str | None] = {
        "TEAMS_WEBHOOK_URL": secret_update(
            teams_payload, "webhook_url", "clear_webhook"
        ),
        "GOOGLE_SHEETS_SPREADSHEET_ID": ordinary_update(
            sheets_payload, "spreadsheet_id"
        ),
        "GOOGLE_APPLICATION_CREDENTIALS": ordinary_update(
            sheets_payload, "credentials_path"
        ),
        "TRANSLATION_API_KEY": secret_update(
            translation_payload, "api_key", "clear_api_key"
        ),
    }
    settings.update_secrets(secrets)
    settings.persist()


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        item.strip()
        for item in re.split(r"[,;，；\n]+", str(value or ""))
        if item.strip()
    ]


def valid_country_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,49}", value))


def slugify_country(query: str, name: str) -> str:
    source = unicodedata.normalize("NFKD", query or name).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", source.casefold())
    slug = "-".join(words[:5]).strip("-")[:42]
    if len(slug) < 2:
        digest = hashlib.sha1(f"{name}|{query}".encode("utf-8")).hexdigest()[:8]
        slug = f"region-{digest}"
    return slug


def unique_country_id(base: str, seen: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in seen:
        candidate = f"{base[:45]}-{suffix}"
        suffix += 1
    return candidate


def normalize_domains(value: Any) -> list[str]:
    domains: list[str] = []
    for item in normalize_list(value):
        raw = item.strip().casefold()
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = (parsed.hostname or "").removeprefix("www.").rstrip(".")
        if host and re.fullmatch(r"[a-z0-9.-]+", host) and host not in domains:
            domains.append(host)
    return domains


def normalize_urls(value: Any) -> list[str]:
    urls: list[str] = []
    for item in normalize_list(value):
        parsed = urlparse(item.strip())
        if parsed.scheme == "https" and parsed.hostname and item not in urls:
            urls.append(item)
    return urls


def bounded_int(value: Any, minimum: int, maximum: int) -> int:
    number = int(value)
    if number < minimum or number > maximum:
        raise ValueError(f"数值必须在 {minimum}–{maximum} 之间")
    return number


def secret_update(payload: dict[str, Any], value_key: str, clear_key: str) -> str | None:
    if payload.get(clear_key):
        return ""
    value = str(payload.get(value_key, "")).strip()
    return value or None


def ordinary_update(payload: dict[str, Any], value_key: str) -> str | None:
    return str(payload[value_key]).strip() if value_key in payload else None
