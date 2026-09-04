from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from .collector import collect_country
from .database import Database
from .deliveries import GoogleSheetsSender, SocialGoogleSheetsSender, TeamsSender
from .settings import Settings
from .social import collect_social_source
from .translator import can_translate, translate_articles


logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def run(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"status": "already_running", "collected": 0}
        started = datetime.now(timezone.utc).isoformat()
        run_id = self.database.start_run(started)
        new_count = 0
        errors: list[str] = []
        self._log(run_id, f"开始抓取，共 {len(self.settings.countries)} 个国家或地区")
        try:
            for country in self.settings.countries:
                country_name = country.get("name", country.get("id", "未知地区"))
                try:
                    domains = country.get("preferred_domains", [])
                    keyword_count = len(country.get("keywords", []))
                    self._log(
                        run_id,
                        f"[{country_name}] 开始：{keyword_count or '无专项'}关键词，"
                        f"{len(domains)} 个优先网站",
                    )
                    articles = collect_country(
                        country,
                        self.settings.raw.get("source_policy", {}),
                        int(self.settings.app.get("max_age_hours", 36)),
                        int(self.settings.app.get("max_articles_per_country", 15)),
                        progress=lambda message, name=country_name: self._log(
                            run_id, f"[{name}] {message}"
                        ),
                    )
                    added = sum(self.database.add_article(item) for item in articles)
                    duplicates = len(articles) - added
                    new_count += added
                    self._log(
                        run_id,
                        f"[{country_name}] 整理完成：候选 {len(articles)} 条，新增 {added} 条，"
                        f"历史重复/已存在 {duplicates} 条",
                        "success",
                    )
                except Exception as exc:  # one country should not stop all others
                    logger.exception("Collection failed for %s", country.get("id"))
                    errors.append(f"{country_name}: {exc}")
                    self._log(run_id, f"[{country_name}] 抓取失败：{exc}", "error")

            self._translate(errors, run_id)
            translation_required = can_translate(self._translation_config())
            if self.settings.destinations.get("automatic_delivery", False):
                self._log(run_id, "已启用自动发送，开始写入已启用渠道")
                self._deliver(errors, translation_required, run_id)
            else:
                self._log(run_id, "自动发送未启用；新闻保留为待发送，请在首页选择目标后发送")
            status = "partial" if errors else "success"
            self.database.finish_run(
                run_id,
                datetime.now(timezone.utc).isoformat(),
                status,
                new_count,
                " | ".join(errors),
            )
            self._log(
                run_id,
                f"本次运行完成：状态 {status}，共新增 {new_count} 条新闻"
                + (f"，错误 {len(errors)} 项" if errors else ""),
                "warning" if errors else "success",
            )
            return {"status": status, "collected": new_count, "errors": errors}
        except Exception as exc:
            logger.exception("Pipeline failed")
            self.database.finish_run(
                run_id,
                datetime.now(timezone.utc).isoformat(),
                "failed",
                new_count,
                str(exc),
            )
            self._log(run_id, f"任务异常终止：{exc}", "error")
            return {"status": "failed", "collected": new_count, "errors": [str(exc)]}
        finally:
            self._lock.release()

    def send_pending_to_sheets(self) -> dict[str, Any]:
        return self.send_selected(sheets=True, teams=False)

    def run_social(self) -> dict[str, Any]:
        config = self.settings.social_monitor
        if not config.get("enabled", False):
            return {"status": "disabled", "collected": 0, "message": "社交平台监测未启用"}
        if not self._lock.acquire(blocking=False):
            return {"status": "already_running", "collected": 0, "message": "其他任务正在运行"}
        sources = config.get("sources", [])
        new_count = 0
        errors: list[str] = []
        self._log(None, f"社交平台采集开始：共 {len(sources)} 个监测源")
        try:
            for source in sources:
                name = source.get("name", "未命名平台")
                try:
                    self._log(None, f"[社交/{name}] 开始采集")
                    items = collect_social_source(
                        source,
                        self.settings.raw.get("source_policy", {}),
                        int(config.get("max_age_hours", 72)),
                        int(config.get("max_items_per_source", 20)),
                        progress=lambda message, source_name=name: self._log(
                            None, f"[社交/{source_name}] {message}"
                        ),
                    )
                    added = sum(self.database.add_social_item(item) for item in items)
                    new_count += added
                    self._log(
                        None,
                        f"[社交/{name}] 完成：候选 {len(items)} 条，新增 {added} 条，重复 {len(items)-added} 条",
                        "success",
                    )
                except Exception as exc:
                    logger.exception("Social collection failed for %s", name)
                    errors.append(f"{name}: {exc}")
                    self._log(None, f"[社交/{name}] 采集失败：{exc}", "error")
            self._translate_social(errors)
            if config.get("automatic_sheets", False):
                result = self._send_social_sheets_locked()
                if result["status"] == "failed":
                    errors.append(result["message"])
            status = "partial" if errors else "success"
            self._log(None, f"社交平台采集完成：新增 {new_count} 条", "warning" if errors else "success")
            return {"status": status, "collected": new_count, "errors": errors,
                    "message": f"采集完成，新增 {new_count} 条社交平台动态"}
        finally:
            self._lock.release()

    def send_social_to_sheets(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"status": "already_running", "sent": 0, "message": "其他任务正在运行"}
        try:
            errors: list[str] = []
            self._translate_social(errors)
            result = self._send_social_sheets_locked()
            if errors and result["status"] == "success":
                result["message"] += "；部分中文整理失败"
            return result
        finally:
            self._lock.release()

    def _send_social_sheets_locked(self) -> dict[str, Any]:
        config = self.settings.social_monitor
        if not self.settings.spreadsheet_id:
            return {"status": "failed", "sent": 0, "message": "尚未配置 Google Spreadsheet ID"}
        translated_only = can_translate(self._translation_config())
        pending = self.database.pending_social_sheets(translated_only=translated_only)
        self._log(None, f"社交平台工作表待写入 {len(pending)} 条")
        try:
            sender_config = {
                "enabled": True,
                "worksheet_name": config.get("worksheet_name", "Social Updates"),
            }
            sent = SocialGoogleSheetsSender(self.settings.spreadsheet_id, sender_config).send(pending)
            self.database.mark_social_sheets_sent(sent)
            self._log(None, f"社交平台工作表写入完成：{len(sent)} 条", "success")
            return {"status": "success", "sent": len(sent), "message": f"已写入 {len(sent)} 条社交平台动态"}
        except Exception:
            logger.exception("Social Google Sheets delivery failed")
            self._log(None, "社交平台工作表写入失败，请查看服务器日志", "error")
            return {"status": "failed", "sent": 0, "message": "写入失败，请查看服务器日志"}

    def _translate_social(self, errors: list[str]) -> None:
        pending = self.database.pending_social_translation()
        if not pending:
            return
        try:
            translated = translate_articles(pending, self._social_translation_config())
            self.database.save_social_translations(translated)
            self._log(None, f"社交平台中文整理完成：{len(translated)}/{len(pending)} 条", "success")
        except Exception:
            logger.exception("Social translation failed")
            errors.append("社交平台中文整理失败")
            self._log(None, "社交平台中文整理失败，请查看服务器日志", "error")

    def send_selected(self, sheets: bool, teams: bool) -> dict[str, Any]:
        """Send queued articles only to destinations explicitly selected by the user."""
        if not self._lock.acquire(blocking=False):
            return {"status": "already_running", "sent": 0, "message": "其他任务正在运行"}
        try:
            if not sheets and not teams:
                return {"status": "invalid", "sent": 0, "message": "请至少选择一个发送目标"}
            errors: list[str] = []
            targets = "、".join(name for enabled, name in ((sheets, "Google Sheets"), (teams, "Teams")) if enabled)
            self._log(None, f"开始手动发送，目标：{targets}")
            self._translate(errors, None)
            translation_required = can_translate(self._translation_config())
            sent_total = 0

            if sheets:
                config = self.settings.destinations.get("google_sheets", {})
                if not config.get("enabled", True):
                    errors.append("Google Sheets 已停用")
                elif not self.settings.spreadsheet_id:
                    errors.append("尚未配置表格 ID")
                else:
                    pending = self.database.pending("sheets", translated_only=translation_required)
                    self._log(None, f"Google Sheets 待写入 {len(pending)} 条")
                    try:
                        sent = GoogleSheetsSender(self.settings.spreadsheet_id, config).send(pending)
                        self.database.mark_sent("sheets", sent)
                        sent_total += len(sent)
                        self._log(None, f"Google Sheets 写入完成：{len(sent)} 条", "success")
                    except Exception as exc:
                        logger.exception("Manual Google Sheets delivery failed")
                        self.database.mark_error([item["id"] for item in pending], str(exc))
                        errors.append("Google Sheets 写入失败，请查看服务器日志")
                        self._log(None, "Google Sheets 写入失败，请查看服务器日志", "error")

            if teams:
                config = self.settings.destinations.get("teams", {})
                if not config.get("enabled", True):
                    errors.append("Teams 已停用")
                elif not self.settings.teams_webhook_url:
                    errors.append("尚未配置 Teams Webhook")
                else:
                    pending = self.database.pending("teams", translated_only=translation_required)
                    self._log(None, f"Teams 待发送 {len(pending)} 条")
                    try:
                        sent = TeamsSender(self.settings.teams_webhook_url, config).send(pending)
                        self.database.mark_sent("teams", sent)
                        sent_total += len(sent)
                        self._log(None, f"Teams 发送完成：{len(sent)} 条", "success")
                    except Exception as exc:
                        logger.exception("Manual Teams delivery failed")
                        self.database.mark_error([item["id"] for item in pending], str(exc))
                        errors.append("Teams 发送失败，请查看服务器日志")
                        self._log(None, "Teams 发送失败，请查看服务器日志", "error")

            if errors:
                return {
                    "status": "partial" if sent_total else "failed",
                    "sent": sent_total,
                    "message": f"已发送 {sent_total} 条；" + "；".join(errors),
                }
            return {"status": "success", "sent": sent_total, "message": f"已发送 {sent_total} 条记录"}
        finally:
            self._lock.release()

    def _translate(self, errors: list[str], run_id: int | None = None) -> None:
        pending = self.database.pending_translation()
        if not pending:
            self._log(run_id, "中文整理：没有待翻译新闻")
            return
        try:
            provider = self._translation_config().get("provider", "unknown")
            self._log(run_id, f"中文整理开始：{len(pending)} 条，翻译方式 {provider}")
            translated = translate_articles(pending, self._translation_config())
            self.database.save_translations(translated)
            self._log(run_id, f"中文整理完成：{len(translated)}/{len(pending)} 条", "success")
        except Exception as exc:
            logger.exception("Translation failed")
            errors.append("中文翻译失败，请查看服务器日志")
            self._log(run_id, "中文整理失败，请查看服务器日志", "error")

    def _deliver(
        self, errors: list[str], translation_required: bool, run_id: int | None = None
    ) -> None:
        destinations = self.settings.destinations
        teams_config = destinations.get("teams", {})
        teams_pending = self.database.pending("teams", translated_only=translation_required)
        if teams_config.get("enabled", True) and self.settings.teams_webhook_url and teams_pending:
            try:
                sent = TeamsSender(self.settings.teams_webhook_url, teams_config).send(teams_pending)
                self.database.mark_sent("teams", sent)
                self._log(run_id, f"Teams 自动发送完成：{len(sent)} 条", "success")
            except Exception as exc:
                logger.exception("Teams delivery failed")
                self.database.mark_error([item["id"] for item in teams_pending], str(exc))
                errors.append(f"Teams: {exc}")
                self._log(run_id, f"Teams 自动发送失败：{exc}", "error")

        sheets_config = destinations.get("google_sheets", {})
        sheets_pending = self.database.pending("sheets", translated_only=translation_required)
        if sheets_config.get("enabled", True) and self.settings.spreadsheet_id and sheets_pending:
            try:
                sent = GoogleSheetsSender(self.settings.spreadsheet_id, sheets_config).send(sheets_pending)
                self.database.mark_sent("sheets", sent)
                self._log(run_id, f"Google Sheets 自动写入完成：{len(sent)} 条", "success")
            except Exception as exc:
                logger.exception("Google Sheets delivery failed")
                self.database.mark_error([item["id"] for item in sheets_pending], str(exc))
                errors.append(f"Google Sheets: {exc}")
                self._log(run_id, f"Google Sheets 自动写入失败：{exc}", "error")

    def _log(self, run_id: int | None, message: str, level: str = "info") -> None:
        self.database.add_run_log(
            run_id, datetime.now(timezone.utc).isoformat(), message, level
        )

    def _translation_config(self) -> dict[str, Any]:
        config = dict(self.settings.raw.get("translation", {}))
        config["_api_key"] = self.settings.translation_api_key
        config["_country_languages"] = {
            item["id"]: {
                "language": item.get("source_language", config.get("source_language", "Portuguese")),
                "code": item.get("source_code", config.get("source_code", "pt")),
            }
            for item in self.settings.countries
        }
        return config

    def _social_translation_config(self) -> dict[str, Any]:
        config = self._translation_config()
        config["_country_languages"].update(
            {
                item["id"]: {
                    "language": item.get("source_language", "English"),
                    "code": item.get("source_code", "en"),
                }
                for item in self.settings.social_monitor.get("sources", [])
            }
        )
        return config
