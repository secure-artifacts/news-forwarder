from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Update selected environment entries without exposing or replacing other secrets."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)
    if remaining and output and output[-1]:
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    root: Path

    @property
    def app(self) -> dict[str, Any]:
        return self.raw["app"]

    @property
    def destinations(self) -> dict[str, Any]:
        return self.raw["destinations"]

    @property
    def countries(self) -> list[dict[str, Any]]:
        return self.raw["countries"]

    @property
    def database_path(self) -> Path:
        path = Path(self.app["database_path"])
        return path if path.is_absolute() else self.root / path

    @property
    def teams_webhook_url(self) -> str:
        return os.getenv("TEAMS_WEBHOOK_URL", "").strip()

    @property
    def spreadsheet_id(self) -> str:
        return os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()

    @property
    def google_credentials_path(self) -> str:
        return os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    @property
    def translation_api_key(self) -> str:
        provider = self.raw.get("translation", {}).get("provider", "openai")
        provider_keys = {
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
        }
        provider_env = provider_keys.get(provider)
        return os.getenv("TRANSLATION_API_KEY", "").strip() or (
            os.getenv(provider_env, "").strip() if provider_env else ""
        )

    @property
    def admin_token(self) -> str:
        return os.getenv("ADMIN_TOKEN", "").strip()

    def persist(self) -> None:
        config_path = self.root / "config.yaml"
        temporary = config_path.with_suffix(".yaml.tmp")
        temporary.write_text(
            yaml.safe_dump(self.raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        temporary.replace(config_path)

    def update_secrets(self, updates: dict[str, str | None]) -> None:
        clean: dict[str, str] = {}
        for key, value in updates.items():
            if value is None:
                continue
            if "\n" in value or "\r" in value:
                raise ValueError(f"{key} cannot contain newlines")
            clean[key] = value.strip()
            if clean[key]:
                os.environ[key] = clean[key]
            else:
                os.environ.pop(key, None)
        if clean:
            update_env_file(self.root / ".env", clean)


def load_settings(config_path: str | Path | None = None) -> Settings:
    path = Path(config_path) if config_path else ROOT / "config.yaml"
    path = path.resolve()
    load_env_file(path.parent / ".env")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    required = {"app", "destinations", "countries"}
    missing = required.difference(raw or {})
    if missing:
        raise ValueError(f"Missing config sections: {', '.join(sorted(missing))}")
    country_ids = [item.get("id") for item in raw["countries"]]
    if len(country_ids) != len(set(country_ids)):
        raise ValueError("Country ids must be unique")
    return Settings(raw=raw, root=path.parent)
