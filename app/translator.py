from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "title_zh": {"type": "string"},
                    "summary_zh": {"type": "string"},
                },
                "required": ["id", "title_zh", "summary_zh"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


def api_key_for(config: dict[str, Any]) -> str:
    provider = config.get("provider", "openai")
    env_names = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
    }
    provider_env = env_names.get(provider)
    return str(config.get("_api_key", "")).strip() or os.getenv(
        "TRANSLATION_API_KEY", ""
    ).strip() or (os.getenv(provider_env, "").strip() if provider_env else "")


def can_translate(config: dict[str, Any]) -> bool:
    if not config.get("enabled", True):
        return False
    if config.get("provider") == "local_llama":
        return True
    return bool(api_key_for(config))


def translate_articles(articles: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    if not articles or not can_translate(config):
        return []
    if config.get("provider") == "local_llama":
        return translate_articles_local(articles, config)
    payload = [
        {
            "id": item["id"],
            "title": item["title"],
            "key_points_source": extract_key_points(item["summary"] or item["title"]),
        }
        for item in articles
    ]
    provider = config.get("provider", "openai")
    if provider == "gemini":
        return translate_articles_gemini(payload, config)
    if provider == "groq":
        return translate_articles_groq(payload, config)
    return translate_articles_openai(payload, config)


def translation_instruction() -> str:
    return (
        "你是严谨的国际新闻编辑。根据原文标题和已提取的重点内容，输出简体中文标题与中文重点摘要。"
        "只保留最重要的事实，包括人物、机构、地点、数字、日期和事件结果；不要逐字翻译全文，"
        "不要添加原文没有的信息。summary_zh 限180个汉字。输出必须符合指定JSON结构。"
    )


def translate_articles_openai(
    payload: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    request_body = {
        "model": config.get("model", "gpt-5.4-mini"),
        "store": False,
        "instructions": translation_instruction(),
        "input": json.dumps(payload, ensure_ascii=False),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "news_translations",
                "strict": True,
                "schema": TRANSLATION_SCHEMA,
            }
        },
    }
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key_for(config)}",
            "Content-Type": "application/json",
        },
        json=request_body,
        timeout=90,
    )
    response.raise_for_status()
    result = response.json()
    output_text = next(
        content["text"]
        for item in result.get("output", [])
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    )
    parsed = json.loads(output_text)
    expected = {item["id"] for item in payload}
    return [item for item in parsed["translations"] if item.get("id") in expected]


def translate_articles_gemini(
    payload: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    model = config.get("model") or "gemini-3.7-flash"
    prompt = translation_instruction() + "\n输入：\n" + json.dumps(payload, ensure_ascii=False)
    response = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key_for(config), "Content-Type": "application/json"},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": TRANSLATION_SCHEMA,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    result = response.json()
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    return validate_translations(parse_json_text(text), payload)


def translate_articles_groq(
    payload: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    response = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key_for(config)}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.get("model") or "openai/gpt-oss-120b",
            "messages": [
                {"role": "system", "content": translation_instruction()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_completion_tokens": 4096,
        },
        timeout=120,
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    return validate_translations(parse_json_text(text), payload)


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    return json.loads(cleaned)


def validate_translations(
    parsed: dict[str, Any], payload: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = {item["id"] for item in payload}
    return [
        item
        for item in parsed.get("translations", [])
        if item.get("id") in expected and item.get("title_zh") and item.get("summary_zh")
    ]


def extract_key_points(text: str, limit: int = 420) -> str:
    """Create a compact factual source excerpt before translation."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return ""
    sentences = re.split(r"(?<=[.!?。！？])\s+", normalized)
    selected: list[str] = []
    size = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or sentence in selected:
            continue
        if selected and size + len(sentence) > limit:
            break
        selected.append(sentence)
        size += len(sentence)
        if len(selected) >= 3:
            break
    return " ".join(selected)[:limit]


def translate_articles_local(
    articles: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    base_url = config.get("base_url", "http://127.0.0.1:11435").rstrip("/")
    source_language = config.get("source_language", "Portuguese")
    source_code = config.get("source_code", "pt")
    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=180) as client:
        for article in articles:
            language_info = config.get("_country_languages", {}).get(article["country_id"], {})
            article_language = language_info.get("language", source_language)
            article_code = language_info.get("code", source_code)
            title_zh = translate_text_local(
                client, base_url, article["title"], article_language, article_code, 256
            )
            summary_text = extract_key_points(article["summary"] or article["title"])
            summary_zh = translate_text_local(
                client, base_url, summary_text, article_language, article_code, 512
            )
            results.append(
                {"id": article["id"], "title_zh": title_zh, "summary_zh": summary_zh[:220]}
            )
    return results


def translate_text_local(
    client: httpx.Client,
    base_url: str,
    text: str,
    source_language: str,
    source_code: str,
    max_tokens: int,
) -> str:
    if not text:
        return ""
    prompt = (
        f"You are a professional {source_language} ({source_code}) to Chinese (zh-Hans) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {source_language} "
        "text while adhering to Chinese grammar, vocabulary, and cultural sensitivities.\n"
        "Produce only the Chinese translation, without any additional explanations or commentary. "
        f"Please translate the following {source_language} text into Chinese:\n\n{text}"
    )
    response = client.post(
        f"{base_url}/v1/chat/completions",
        json={
            "model": "translategemma",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        },
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip().strip('"')
