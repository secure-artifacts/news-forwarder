from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
import re
import threading
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .database import Database
from .local_model import LocalModelServer
from .pipeline import Pipeline
from .settings import Settings
from .settings_api import apply_settings, settings_snapshot
from .translator import can_translate


class DailyScheduler:
    def __init__(self, job, hour: int, minute: int, timezone_name: str):
        self.job = job
        self.hour = hour
        self.minute = minute
        self.timezone = ZoneInfo(timezone_name)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="daily-news")

    def start(self):
        self.thread.start()

    def shutdown(self):
        self.stop_event.set()
        self.wake_event.set()

    def configure(self, hour: int, minute: int, timezone_name: str):
        self.hour = hour
        self.minute = minute
        self.timezone = ZoneInfo(timezone_name)
        self.wake_event.set()

    def _loop(self):
        while not self.stop_event.is_set():
            now = datetime.now(self.timezone)
            next_run = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            woke_early = self.wake_event.wait((next_run - now).total_seconds())
            self.wake_event.clear()
            if self.stop_event.is_set():
                return
            if not woke_early:
                self.job()


class IntervalScheduler:
    def __init__(self, job, interval_minutes: int, enabled: bool):
        self.job = job
        self.interval_minutes = interval_minutes
        self.enabled = enabled
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="social-monitor")

    def start(self):
        self.thread.start()

    def shutdown(self):
        self.stop_event.set()
        self.wake_event.set()

    def configure(self, interval_minutes: int, enabled: bool):
        self.interval_minutes = interval_minutes
        self.enabled = enabled
        self.wake_event.set()

    def _loop(self):
        while not self.stop_event.is_set():
            timeout = max(15, self.interval_minutes) * 60 if self.enabled else 86400
            woke_early = self.wake_event.wait(timeout)
            self.wake_event.clear()
            if self.stop_event.is_set():
                return
            if not woke_early and self.enabled:
                self.job()


def create_app(settings: Settings) -> FastAPI:
    database = Database(settings.database_path)
    pipeline = Pipeline(settings, database)
    local_model = LocalModelServer(settings.root, settings.raw.get("translation", {}))
    scheduler = DailyScheduler(
        pipeline.run,
        int(settings.app.get("schedule_hour", 8)),
        int(settings.app.get("schedule_minute", 0)),
        settings.app.get("timezone", "UTC"),
    )
    social_scheduler = IntervalScheduler(
        pipeline.run_social,
        int(settings.social_monitor.get("interval_minutes", 360)),
        bool(settings.social_monitor.get("enabled", False)),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        local_model.start()
        scheduler.start()
        social_scheduler.start()
        yield
        scheduler.shutdown()
        social_scheduler.shutdown()
        local_model.stop()

    app = FastAPI(title="国际新闻转发器", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/settings", include_in_schema=False)
    def settings_page():
        return FileResponse(Path(__file__).parent / "static" / "settings.html")

    @app.get("/social", include_in_schema=False)
    def social_page():
        return FileResponse(Path(__file__).parent / "static" / "social.html")

    @app.get("/api/status")
    def status():
        result = database.dashboard()
        result["running"] = pipeline.running
        translation_config = dict(settings.raw.get("translation", {}))
        translation_config["_api_key"] = settings.translation_api_key
        translation_ready = can_translate(translation_config)
        if translation_config.get("provider") == "local_llama":
            translation_ready = translation_ready and local_model.healthy()
        result["config"] = {
            "countries": [item["name"] for item in settings.countries],
            "schedule": (
                f"{int(settings.app.get('schedule_hour', 8)):02d}:"
                f"{int(settings.app.get('schedule_minute', 0)):02d}"
            ),
            "timezone": settings.app.get("timezone", "UTC"),
            "teams_ready": bool(settings.teams_webhook_url),
            "teams_enabled": bool(settings.destinations.get("teams", {}).get("enabled", True)),
            "sheets_ready": bool(settings.spreadsheet_id),
            "sheets_enabled": bool(
                settings.destinations.get("google_sheets", {}).get("enabled", True)
            ),
            "translation_ready": translation_ready,
            "translation_provider": settings.raw.get("translation", {}).get("provider", "openai"),
            "token_required": bool(settings.admin_token),
            "social_enabled": bool(settings.social_monitor.get("enabled", False)),
            "version": __version__,
        }
        return result

    @app.get("/api/articles")
    def articles(limit: int = 500):
        return {"items": database.recent_articles(limit)}

    @app.get("/api/update-check")
    def update_check():
        release_url = "https://github.com/secure-artifacts/news-forwarder/releases/latest"
        try:
            response = httpx.get(
                "https://api.github.com/repos/secure-artifacts/news-forwarder/releases/latest",
                timeout=12,
                follow_redirects=True,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "NewsForwarder/1.3"},
            )
            response.raise_for_status()
            payload = response.json()
            latest = str(payload.get("tag_name") or "").lstrip("vV")
            html_url = str(payload.get("html_url") or release_url)
            return {
                "current_version": __version__,
                "latest_version": latest,
                "update_available": version_tuple(latest) > version_tuple(__version__),
                "url": html_url,
                "message": f"发现新版本 {latest}" if version_tuple(latest) > version_tuple(__version__) else "当前已是最新版本",
            }
        except (httpx.HTTPError, ValueError, TypeError):
            return {
                "current_version": __version__, "latest_version": "",
                "update_available": False, "url": release_url,
                "message": "暂时无法连接 GitHub，请稍后重试",
            }

    @app.get("/api/social/status")
    def social_status():
        result = database.social_dashboard()
        result["running"] = pipeline.running
        result["config"] = {
            "enabled": bool(settings.social_monitor.get("enabled", False)),
            "interval_minutes": int(settings.social_monitor.get("interval_minutes", 360)),
            "max_age_hours": int(settings.social_monitor.get("max_age_hours", 2160)),
            "worksheet_name": settings.social_monitor.get("worksheet_name", "Social Updates"),
            "sheets_ready": bool(settings.spreadsheet_id),
            "translation_enabled": bool(settings.raw.get("translation", {}).get("enabled", True)),
            "token_required": bool(settings.admin_token),
            "version": __version__,
        }
        return result

    @app.get("/api/settings")
    def get_settings(x_admin_token: str = Header(default="")):
        require_admin(settings, x_admin_token)
        return settings_snapshot(settings)

    @app.post("/api/settings")
    def save_settings(payload: dict, x_admin_token: str = Header(default="")):
        require_admin(settings, x_admin_token)
        if pipeline.running:
            raise HTTPException(status_code=409, detail="请等待当前任务完成后再保存设置")
        try:
            previous_provider = settings.raw.get("translation", {}).get("provider")
            apply_settings(settings, payload)
            current_provider = settings.raw.get("translation", {}).get("provider")
            if current_provider == "local_llama":
                local_model.start()
            elif previous_provider == "local_llama":
                local_model.stop()
            scheduler.configure(
                int(settings.app.get("schedule_hour", 8)),
                int(settings.app.get("schedule_minute", 0)),
                settings.app.get("timezone", "UTC"),
            )
            social_scheduler.configure(
                int(settings.social_monitor.get("interval_minutes", 360)),
                bool(settings.social_monitor.get("enabled", False)),
            )
            return {"saved": True, "message": "设置已保存并立即生效"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/run", status_code=202)
    def run_now(background_tasks: BackgroundTasks, x_admin_token: str = Header(default="")):
        require_admin(settings, x_admin_token)
        if pipeline.running:
            return {"accepted": False, "message": "抓取任务正在运行"}
        background_tasks.add_task(pipeline.run)
        return {"accepted": True, "message": "抓取任务已启动"}

    @app.post("/api/social/run", status_code=202)
    def run_social(background_tasks: BackgroundTasks, x_admin_token: str = Header(default="")):
        require_admin(settings, x_admin_token)
        if pipeline.running:
            return {"accepted": False, "message": "其他抓取任务正在运行"}
        background_tasks.add_task(pipeline.run_social)
        return {"accepted": True, "message": "社交平台采集任务已启动"}

    @app.post("/api/social/send-sheets")
    def send_social_sheets(x_admin_token: str = Header(default="")):
        require_admin(settings, x_admin_token)
        return pipeline.send_social_to_sheets()

    @app.post("/api/send-sheets")
    def send_sheets(x_admin_token: str = Header(default="")):
        require_admin(settings, x_admin_token)
        return pipeline.send_pending_to_sheets()

    @app.post("/api/send-selected")
    def send_selected(payload: dict, x_admin_token: str = Header(default="")):
        require_admin(settings, x_admin_token)
        return pipeline.send_selected(
            sheets=bool(payload.get("sheets", False)),
            teams=bool(payload.get("teams", False)),
        )

    return app


def require_admin(settings: Settings, token: str) -> None:
    if settings.admin_token and token != settings.admin_token:
        raise HTTPException(status_code=401, detail="管理令牌不正确")


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = [int(item) for item in re.findall(r"\d+", value or "")[:4]]
    return tuple((numbers + [0, 0, 0, 0])[:4])
