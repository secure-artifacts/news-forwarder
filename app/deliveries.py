from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import google.auth
import httpx
from googleapiclient.discovery import build


SHEET_HEADERS = [
    "采集时间",
    "发布时间",
    "国家",
    "中文标题",
    "中文摘要",
    "原文标题",
    "来源",
    "原文链接",
]

SOCIAL_SHEET_HEADERS = [
    "采集时间",
    "贴文/发布时间",
    "平台",
    "中文标题",
    "中文内容简介",
    "原文标题",
    "来源账号/网站",
    "原文链接",
]


def chunks(items: list[dict[str, Any]], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class TeamsSender:
    def __init__(self, webhook_url: str, config: dict[str, Any]):
        self.webhook_url = webhook_url
        self.config = config

    def send(self, articles: list[dict[str, Any]]) -> list[int]:
        if not self.webhook_url or not self.config.get("enabled", True):
            return []
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for article in articles:
            grouped[article["country_name"]].append(article)
        sent: list[int] = []
        size = int(self.config.get("max_articles_per_message", 8))
        with httpx.Client(timeout=30) as client:
            for country, group in grouped.items():
                for batch in chunks(group, size):
                    payload = self._payload(country, batch)
                    response = client.post(self.webhook_url, json=payload)
                    response.raise_for_status()
                    sent.extend(item["id"] for item in batch)
        return sent

    def _payload(self, country: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
        if self.config.get("payload_mode") == "text":
            lines = [f"## {country} 新闻速报"]
            for item in articles:
                title = item["title_zh"] or item["title"]
                summary = item["summary_zh"] or item["summary"]
                lines.append(f"**{title}**\n{summary}\n[{item['source']}]({item['url']})")
            return {"text": "\n\n".join(lines)}

        body: list[dict[str, Any]] = [
            {
                "type": "TextBlock",
                "text": f"{country} 新闻速报",
                "weight": "Bolder",
                "size": "Large",
            }
        ]
        for item in articles:
            title = item["title_zh"] or item["title"]
            summary = (item["summary_zh"] or item["summary"] or "暂无摘要")[:450]
            body.extend(
                [
                    {"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
                    {"type": "TextBlock", "text": summary, "wrap": True, "spacing": "Small"},
                    {
                        "type": "TextBlock",
                        "text": f"来源：[{item['source']}]({item['url']})",
                        "wrap": True,
                        "spacing": "Small",
                        "color": "Accent",
                    },
                ]
            )
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": body,
                    },
                }
            ],
        }


class GoogleSheetsSender:
    def __init__(self, spreadsheet_id: str, config: dict[str, Any]):
        self.spreadsheet_id = spreadsheet_id
        self.config = config

    def send(self, articles: list[dict[str, Any]]) -> list[int]:
        if not self.spreadsheet_id or not self.config.get("enabled", True) or not articles:
            return []
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        title = self.config.get("worksheet_name", "News")
        self._ensure_sheet(service, title)
        escaped_title = title.replace("'", "''")
        range_name = f"'{escaped_title}'!A:H"
        current = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
            .get("values", [])
        )
        rows = []
        if not current:
            rows.append(SHEET_HEADERS)
        existing_by_url = {
            row[7]: index
            for index, row in enumerate(current, start=1)
            if len(row) > 7 and row[7]
        }
        append_rows: list[list[str]] = []
        for item in articles:
            row = [
                item["collected_at"],
                item["published_at"] or "",
                item["country_name"],
                item["title_zh"] or item["title"],
                item["summary_zh"] or item["summary"],
                item["title"],
                item["source"],
                item["url"],
            ]
            existing_row = existing_by_url.get(item["url"])
            if existing_row:
                service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{escaped_title}'!A{existing_row}:H{existing_row}",
                    valueInputOption="RAW",
                    body={"values": [row]},
                ).execute()
            else:
                append_rows.append(row)
        rows.extend(append_rows)
        if not rows:
            return [item["id"] for item in articles]
        (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )
        return [item["id"] for item in articles]

    def _ensure_sheet(self, service, title: str) -> None:
        metadata = service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
        if title not in titles:
            service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
            ).execute()


class SocialGoogleSheetsSender(GoogleSheetsSender):
    def send(self, items: list[dict[str, Any]]) -> list[int]:
        if not self.spreadsheet_id or not self.config.get("enabled", True) or not items:
            return []
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        title = self.config.get("worksheet_name", "Social Updates")
        self._ensure_sheet(service, title)
        escaped_title = title.replace("'", "''")
        range_name = f"'{escaped_title}'!A:H"
        current = (
            service.spreadsheets().values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute().get("values", [])
        )
        rows: list[list[str]] = []
        if not current:
            rows.append(SOCIAL_SHEET_HEADERS)
        existing_by_url = {
            row[7]: index for index, row in enumerate(current, start=1) if len(row) > 7 and row[7]
        }
        append_rows: list[list[str]] = []
        for item in items:
            row = [
                item["collected_at"], item["published_at"] or "", item["platform_name"],
                item["title_zh"] or item["title"], item["summary_zh"] or item["summary"],
                item["title"], item["source"], item["url"],
            ]
            existing_row = existing_by_url.get(item["url"])
            if existing_row:
                service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{escaped_title}'!A{existing_row}:H{existing_row}",
                    valueInputOption="RAW", body={"values": [row]},
                ).execute()
            else:
                append_rows.append(row)
        rows.extend(append_rows)
        if rows:
            service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id, range=range_name,
                valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ).execute()
        return [item["id"] for item in items]
