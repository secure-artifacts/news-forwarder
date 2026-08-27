from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .collector import same_story


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    country_id TEXT NOT NULL,
    country_name TEXT NOT NULL,
    title TEXT NOT NULL,
    title_zh TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    summary_zh TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    published_at TEXT,
    collected_at TEXT NOT NULL,
    teams_sent INTEGER NOT NULL DEFAULT 0,
    sheets_sent INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_articles_pending
ON articles(teams_sent, sheets_sent, collected_at);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    collected_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_logs_created ON run_logs(id DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def add_article(self, article: dict[str, Any]) -> bool:
        with self.connect() as connection:
            recent = connection.execute(
                """SELECT title, url FROM articles
                WHERE country_id = ? ORDER BY collected_at DESC LIMIT 300""",
                (article["country_id"],),
            ).fetchall()
            if any(
                row["url"] == article["url"] or same_story(row["title"], article["title"])
                for row in recent
            ):
                return False
            cursor = connection.execute(
                """INSERT OR IGNORE INTO articles
                (fingerprint, country_id, country_name, title, title_zh, summary, summary_zh, source, url,
                 published_at, collected_at)
                VALUES (:fingerprint, :country_id, :country_name, :title, :title_zh, :summary,
                        :summary_zh, :source, :url, :published_at, :collected_at)""",
                article,
            )
            return cursor.rowcount == 1

    def pending(
        self, destination: str, limit: int = 200, translated_only: bool = False
    ) -> list[dict[str, Any]]:
        if destination not in {"teams", "sheets"}:
            raise ValueError("Unknown destination")
        column = f"{destination}_sent"
        translation_clause = " AND title_zh <> ''" if translated_only else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM articles WHERE {column} = 0{translation_clause} "
                "ORDER BY collected_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_translation(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM articles WHERE title_zh = '' ORDER BY collected_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_translations(self, translations: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            connection.executemany(
                """UPDATE articles
                SET title_zh=:title_zh, summary_zh=:summary_zh, sheets_sent=0
                WHERE id=:id""",
                translations,
            )

    def mark_sent(self, destination: str, ids: list[int]) -> None:
        if not ids:
            return
        if destination not in {"teams", "sheets"}:
            raise ValueError("Unknown destination")
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE articles SET {destination}_sent = 1, last_error = '' WHERE id IN ({placeholders})",
                ids,
            )

    def mark_error(self, ids: list[int], message: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE articles SET last_error = ? WHERE id IN ({placeholders})",
                [message[:1000], *ids],
            )

    def start_run(self, started_at: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO runs(started_at, status) VALUES (?, 'running')", (started_at,)
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, finished_at: str, status: str, count: int, error: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE runs SET finished_at=?, status=?, collected_count=?, error=?
                WHERE id=?""",
                (finished_at, status, count, error[:2000], run_id),
            )

    def add_run_log(
        self, run_id: int | None, created_at: str, message: str, level: str = "info"
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO run_logs(run_id, created_at, level, message) VALUES (?, ?, ?, ?)",
                (run_id, created_at, level, message[:2000]),
            )
            connection.execute(
                "DELETE FROM run_logs WHERE id NOT IN (SELECT id FROM run_logs ORDER BY id DESC LIMIT 2000)"
            )

    def dashboard(self) -> dict[str, Any]:
        with self.connect() as connection:
            stats = connection.execute(
                """SELECT COUNT(*) total,
                SUM(CASE WHEN teams_sent=0 THEN 1 ELSE 0 END) teams_pending,
                SUM(CASE WHEN sheets_sent=0 THEN 1 ELSE 0 END) sheets_pending
                FROM articles"""
            ).fetchone()
            latest = connection.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            articles = connection.execute(
                "SELECT * FROM articles ORDER BY collected_at DESC LIMIT 30"
            ).fetchall()
            logs = connection.execute(
                "SELECT * FROM run_logs ORDER BY id DESC LIMIT 200"
            ).fetchall()
        return {
            "stats": dict(stats),
            "latest_run": dict(latest) if latest else None,
            "articles": [dict(row) for row in articles],
            "logs": [dict(row) for row in reversed(logs)],
        }
