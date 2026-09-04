from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import webbrowser

import httpx
from fastapi.testclient import TestClient
import uvicorn

from app.settings import load_settings
from app.web import create_app


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ensure_first_run_files(root: Path) -> Path:
    config_path = root / "config.yaml"
    if not config_path.exists():
        shutil.copy2(root / "config.example.yaml", config_path)
    (root / "data").mkdir(exist_ok=True)
    return config_path


def healthy(url: str) -> bool:
    try:
        return httpx.get(f"{url}/api/status", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def open_when_ready(url: str, no_browser: bool) -> None:
    if no_browser:
        return
    for _ in range(120):
        if healthy(url):
            webbrowser.open(url)
            return
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-local-model", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    root = application_root()
    os.chdir(root)
    url = f"http://{args.host}:{args.port}"
    if healthy(url):
        if not args.no_browser:
            webbrowser.open(url)
        return

    config_path = ensure_first_run_files(root)
    settings = load_settings(config_path)
    if args.no_local_model or args.self_test:
        settings.raw.setdefault("translation", {})["enabled"] = False
    app = create_app(settings)
    if args.self_test:
        with TestClient(app) as client:
            assert client.get("/api/status").status_code == 200
            assert client.get("/").status_code == 200
            assert client.get("/settings").status_code == 200
            assert client.get("/social").status_code == 200
            assert client.get("/api/social/status").status_code == 200
        return

    stdout_log = (root / "server.out.log").open("a", encoding="utf-8")
    stderr_log = (root / "server.err.log").open("a", encoding="utf-8")
    sys.stdout = stdout_log
    sys.stderr = stderr_log
    opener = threading.Thread(
        target=open_when_ready,
        args=(url, args.no_browser),
        daemon=True,
        name="browser-opener",
    )
    opener.start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
