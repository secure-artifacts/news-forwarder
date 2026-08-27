from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import quote_plus, urlparse

import httpx
from dateutil import parser as date_parser


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
GOOGLE_NEWS_HOSTS = {"news.google.com", "news.googleusercontent.com"}
BOILERPLATE_WORDS = (
    "cookie", "privacy policy", "subscribe", "newsletter", "advertisement",
    "all rights reserved", "javascript", "enable cookies", "sign up",
)
STORY_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "da", "de", "do", "dos", "das",
    "e", "em", "for", "from", "in", "is", "na", "no", "of", "on", "or",
    "para", "the", "to", "um", "uma", "with", "com", "que", "sobre",
}


def clean_text(value: str | None, limit: int = 1200) -> str:
    text = html.unescape(TAG_RE.sub(" ", value or ""))
    return SPACE_RE.sub(" ", text).strip()[:limit]


def google_news_url(country: dict, search_query: str | None = None) -> str:
    query = f"({search_query or country['query']}) when:1d"
    return (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(query)}&hl={quote_plus(country.get('language', 'en-US'))}"
        f"&gl={quote_plus(country.get('region', 'US'))}"
        f"&ceid={quote_plus(country.get('ceid', 'US:en'))}"
    )


def search_queries(country: dict) -> list[tuple[str, int]]:
    """Return preferred-domain searches first and general searches as fallback."""
    base = str(country.get("query") or country.get("name") or "").strip()
    keywords = [str(item).strip() for item in country.get("keywords", []) if str(item).strip()]
    groups = keyword_groups(keywords)
    topics: list[str] = []
    for group in groups:
        expression = " OR ".join(f'"{item}"' if " " in item else item for item in group)
        topics.append(f"({base}) ({expression})")
    if not topics:
        topics = [base]
    preferred = country.get("preferred_domains", [])[:10]
    queries = [(f"{topic} site:{domain}", 0) for domain in preferred for topic in topics]
    queries.extend((topic, 1) for topic in topics)
    return queries


def keyword_groups(
    keywords: list[str], max_terms: int = 14, max_characters: int = 360
) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for keyword in keywords:
        added_size = len(keyword) + (4 if current else 0)
        if current and (len(current) >= max_terms or current_size + added_size > max_characters):
            groups.append(current)
            current = []
            current_size = 0
        current.append(keyword)
        current_size += len(keyword) + (4 if len(current) > 1 else 0)
    if current:
        groups.append(current)
    return groups


def domain_matches(host: str, rule: str) -> bool:
    normalized = rule.casefold().lstrip(".")
    return host == normalized or host.endswith("." + normalized)


def source_allowed(article_url: str, source_url: str, source_name: str, policy: dict) -> bool:
    checked_url = source_url or article_url
    parsed = urlparse(checked_url)
    if policy.get("require_https", True) and parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").casefold()
    if not host:
        return False
    if any(domain_matches(host, item) for item in policy.get("blocked_domain_suffixes", [])):
        return False
    source_folded = source_name.casefold()
    if any(word.casefold() in source_folded for word in policy.get("blocked_source_words", [])):
        return False
    allowed = policy.get("allowed_domains", [])
    return not allowed or any(domain_matches(host, item) for item in allowed)


def matches_keywords(text: str, country: dict) -> bool:
    folded = text.casefold()
    includes = [item.casefold() for item in country.get("keywords", []) if item]
    excludes = [item.casefold() for item in country.get("exclude_keywords", []) if item]
    return (not includes or any(item in folded for item in includes)) and not any(
        item in folded for item in excludes
    )


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = date_parser.parse(raw)
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


class ArticleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.paragraph_depth = 0
        self.current: list[str] = []
        self.paragraphs: list[str] = []
        self.canonical_url = ""
        self.og_url = ""
        self.google_id = ""
        self.google_signature = ""
        self.google_timestamp = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg", "form", "nav", "footer"}:
            self.skip_depth += 1
        if tag == "p" and not self.skip_depth:
            self.paragraph_depth += 1
            self.current = []
        if tag == "link" and "canonical" in attributes.get("rel", "").casefold():
            self.canonical_url = attributes.get("href", "")
        if tag == "meta" and attributes.get("property", "").casefold() == "og:url":
            self.og_url = attributes.get("content", "")
        if attributes.get("data-n-a-id"):
            self.google_id = attributes["data-n-a-id"]
            self.google_signature = attributes.get("data-n-a-sg", "")
            self.google_timestamp = attributes.get("data-n-a-ts", "")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "p" and self.paragraph_depth:
            paragraph = clean_text(" ".join(self.current), 1800)
            folded = paragraph.casefold()
            if len(paragraph) >= 45 and not any(word in folded for word in BOILERPLATE_WORDS):
                self.paragraphs.append(paragraph)
            self.current = []
            self.paragraph_depth -= 1
        if tag in {"script", "style", "noscript", "svg", "form", "nav", "footer"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.paragraph_depth and not self.skip_depth:
            self.current.append(data)


def external_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host or host in GOOGLE_NEWS_HOSTS:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global


def fetch_page(client: httpx.Client, url: str) -> tuple[str, ArticleHTMLParser]:
    response = client.get(url)
    response.raise_for_status()
    parser = ArticleHTMLParser()
    if "html" in response.headers.get("content-type", "").casefold():
        parser.feed(response.text[:2_000_000])
    return str(response.url), parser


def find_external_url(value: object) -> str:
    if isinstance(value, str):
        if external_url(value):
            return value
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return ""
        return find_external_url(decoded)
    if isinstance(value, list):
        for item in value:
            found = find_external_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for item in value.values():
            found = find_external_url(item)
            if found:
                return found
    return ""


def decode_google_news_url(client: httpx.Client, url: str) -> str:
    if (urlparse(url).hostname or "").casefold() not in GOOGLE_NEWS_HOSTS:
        return url
    try:
        response = client.get(url)
        response.raise_for_status()
        parser = ArticleHTMLParser()
        parser.feed(response.text[:1_000_000])
        article_id = parser.google_id or urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        if not article_id or not parser.google_signature or not parser.google_timestamp:
            return url
        request_value = (
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
            'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
            f'"{article_id}",{parser.google_timestamp},"{parser.google_signature}"]'
        )
        payload = json.dumps([[["Fbv4je", request_value]]], separators=(",", ":"))
        decoded_response = client.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
            data={"f.req": payload},
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Referer": "https://news.google.com/",
            },
        )
        decoded_response.raise_for_status()
        for line in decoded_response.text.splitlines():
            line = line.strip()
            if not line.startswith("["):
                continue
            try:
                found = find_external_url(json.loads(line))
            except json.JSONDecodeError:
                continue
            if found:
                return found
    except (httpx.HTTPError, ValueError, UnicodeError):
        pass
    return url


def fetch_article_content(url: str) -> tuple[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NewsForwarder/2.0",
        "Accept-Language": "en,pt;q=0.9,fr;q=0.8",
    }
    try:
        with httpx.Client(timeout=14, follow_redirects=True, headers=headers) as client:
            decoded_url = decode_google_news_url(client, url)
            resolved, parser = fetch_page(client, decoded_url)
            candidates = [parser.canonical_url, parser.og_url, resolved]
            target = next((item for item in candidates if external_url(item)), "")
            if target and target != resolved:
                resolved, parser = fetch_page(client, target)
            if not external_url(resolved):
                return url, ""
            content = " ".join(dict.fromkeys(parser.paragraphs))[:12_000]
            return resolved, content
    except (httpx.HTTPError, ValueError, UnicodeError):
        return url, ""


def compact_summary(title: str, rss_summary: str, article_text: str, limit: int = 900) -> str:
    """Select a few factual body sentences, with the feed description as fallback."""
    source = article_text if len(article_text) >= 180 else rss_summary
    source = clean_text(source, 12_000)
    if not source:
        return title
    title_folded = clean_text(title, 500).casefold()
    selected: list[str] = []
    size = 0
    for sentence in SENTENCE_RE.split(source):
        sentence = clean_text(sentence, 700)
        folded = sentence.casefold()
        if len(sentence) < 35 or folded == title_folded or any(word in folded for word in BOILERPLATE_WORDS):
            continue
        if selected and size + len(sentence) > limit:
            break
        selected.append(sentence)
        size += len(sentence)
        if len(selected) >= 3:
            break
    return " ".join(selected)[:limit] or source[:limit]


def normalize_story_title(value: str) -> str:
    value = re.sub(r"\s+[-–—|:]\s+[^-–—|:]{2,45}$", "", value or "")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    tokens = [token for token in re.findall(r"[a-z0-9]+", value) if token not in STORY_STOPWORDS]
    return " ".join(tokens)


def same_story(first_title: str, second_title: str) -> bool:
    first = normalize_story_title(first_title)
    second = normalize_story_title(second_title)
    if not first or not second:
        return False
    if first == second or SequenceMatcher(None, first, second).ratio() >= 0.82:
        return True
    first_tokens, second_tokens = set(first.split()), set(second.split())
    if min(len(first_tokens), len(second_tokens)) < 4:
        return False
    overlap = len(first_tokens & second_tokens)
    return overlap / min(len(first_tokens), len(second_tokens)) >= 0.72 and overlap / len(
        first_tokens | second_tokens
    ) >= 0.52


def parse_feed(content: bytes, country: dict, policy: dict, cutoff: datetime, priority: int) -> list[dict]:
    root = ET.fromstring(content)
    results: list[dict] = []
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
    for entry in entries:
        children = {child.tag.rsplit("}", 1)[-1]: child for child in list(entry)}
        source_node = children.get("source")
        source_name = clean_text(
            (source_node.text if source_node is not None else "")
            or (children.get("author").text if children.get("author") is not None else "")
            or "未知来源", 160,
        )
        source_url = ""
        if source_node is not None:
            source_url = source_node.attrib.get("url") or source_node.attrib.get("href") or ""
        link_node = children.get("link")
        url = (link_node.text or link_node.attrib.get("href") or "").strip() if link_node is not None else ""
        title = clean_text(children.get("title").text if children.get("title") is not None else "", 500)
        summary_node = next((children[key] for key in ("summary", "description", "content") if key in children), None)
        rss_summary = clean_text(summary_node.text if summary_node is not None else "", 2500)
        date_node = next((children[key] for key in ("pubDate", "published", "updated") if key in children), None)
        published = parse_date(date_node.text if date_node is not None else None)
        if not title or not url or (published and published < cutoff):
            continue
        if not source_allowed(url, source_url, source_name, policy):
            continue
        if not matches_keywords(f"{title} {rss_summary}", country):
            continue
        results.append({
            "country_id": country["id"], "country_name": country["name"], "title": title,
            "title_zh": "", "summary": rss_summary, "summary_zh": "", "source": source_name,
            "url": url, "published_at": published.isoformat() if published else None,
            "_priority": priority,
        })
    return results


def collect_country(
    country: dict,
    policy: dict,
    max_age_hours: int,
    limit: int,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    emit = progress or (lambda _: None)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    collected_at = datetime.now(timezone.utc).isoformat()
    candidates: list[dict] = []
    seen_urls: set[str] = set()
    if country.get("feed_url"):
        feeds = [(country["feed_url"], 0, "自定义 RSS")]
    else:
        feeds = []
        queries = search_queries(country)
        totals = {priority: sum(1 for _, item_priority in queries if item_priority == priority) for priority in (0, 1)}
        positions = {0: 0, 1: 0}
        for query, priority in queries:
            positions[priority] += 1
            site_match = re.search(r"\bsite:([^\s)]+)", query)
            if site_match:
                label = (
                    f"优先网站 {site_match.group(1)} "
                    f"（关键词组 {positions[priority]}/{totals[priority]}）"
                )
            else:
                label = f"全网关键词组 {positions[priority]}/{totals[priority]}"
            feeds.append((google_news_url(country, query), priority, label))
    domains = country.get("preferred_domains", [])
    if domains:
        emit(f"优先网站：{', '.join(domains)}")
    else:
        emit("未设置优先网站，将直接搜索其他国际来源")
    emit(f"时间范围：最近 {max_age_hours} 小时；目标最多 {limit} 条")
    general_announced = False
    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "NewsForwarder/2.0"}) as client:
        for feed_url, priority, label in feeds:
            preferred_count = sum(item["_priority"] == 0 for item in candidates)
            general_count = sum(item["_priority"] == 1 for item in candidates)
            if priority == 0 and preferred_count >= limit * 2:
                continue
            if priority == 1 and general_count >= limit * 2:
                break
            if priority == 1 and not general_announced:
                emit(f"优先网站阶段完成，获得 {preferred_count} 条候选；开始从其他国际来源补充")
                general_announced = True
            emit(f"正在搜索：{label}")
            try:
                response = client.get(feed_url)
                response.raise_for_status()
                parsed = parse_feed(response.content, country, policy, cutoff, priority)
                emit(f"{label}：找到 {len(parsed)} 条符合时间、关键词和来源规则的结果")
            except (httpx.HTTPError, ET.ParseError) as exc:
                emit(f"{label}：搜索失败（{type(exc).__name__}）")
                continue
            for item in parsed:
                if item["url"] in seen_urls:
                    continue
                seen_urls.add(item["url"])
                candidates.append(item)

    preferred_total = sum(item["_priority"] == 0 for item in candidates)
    general_total = sum(item["_priority"] == 1 for item in candidates)
    emit(f"搜索阶段完成：优先网站 {preferred_total} 条，其他来源 {general_total} 条")
    if not candidates:
        emit("没有找到符合当前时间范围、关键词和来源规则的新闻")
        return []
    candidates.sort(key=lambda item: (item["_priority"], -(parse_date(item.get("published_at")) or cutoff).timestamp()))
    preferred_candidates = [item for item in candidates if item["_priority"] == 0][: limit * 2]
    general_candidates = [item for item in candidates if item["_priority"] == 1][: limit * 2]
    candidates = preferred_candidates + general_candidates
    emit(f"开始解析 {len(candidates)} 个原文链接并提取正文")
    with ThreadPoolExecutor(max_workers=4) as executor:
        enriched = list(executor.map(lambda item: fetch_article_content(item["url"]), candidates))

    prepared: list[dict] = []
    resolved_count = 0
    body_count = 0
    for item, (resolved_url, article_text) in zip(candidates, enriched):
        if resolved_url != item["url"] and not source_allowed(resolved_url, "", item["source"], policy):
            continue
        resolved_count += int(resolved_url != item["url"])
        item["url"] = resolved_url
        body_count += int(len(article_text) >= 180)
        item["summary"] = compact_summary(item["title"], item["summary"], article_text)
        item["collected_at"] = collected_at
        item["_has_body"] = len(article_text) >= 180
        fingerprint_value = f"{country['id']}|{normalize_story_title(item['title'])}|{item['url']}"
        item["fingerprint"] = hashlib.sha256(fingerprint_value.encode("utf-8")).hexdigest()
        prepared.append(item)

    emit(
        f"正文解析完成：{body_count} 条读取到正文，{len(prepared) - body_count} 条使用 RSS 后备内容，"
        f"{resolved_count} 条还原为媒体原文链接"
    )

    # Prefer articles whose body can be summarized; within that group, keep configured sites first.
    prepared.sort(
        key=lambda item: (
            0 if item["_has_body"] else 1,
            item["_priority"],
            -(parse_date(item.get("published_at")) or cutoff).timestamp(),
        )
    )
    results: list[dict] = []
    for item in prepared:
        if any(same_story(item["title"], old["title"]) for old in results):
            continue
        item.pop("_priority", None)
        item.pop("_has_body", None)
        results.append(item)
        if len(results) >= limit:
            break
    duplicate_count = max(0, len(prepared) - len(results))
    emit(f"同事件合并完成：保留 {len(results)} 条，合并或截断 {duplicate_count} 条")
    return results
