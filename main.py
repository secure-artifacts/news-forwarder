from __future__ import annotations

import argparse
import logging

import uvicorn

from app.database import Database
from app.pipeline import Pipeline
from app.settings import load_settings
from app.web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect international news and forward it")
    parser.add_argument("command", choices=["serve", "run-once"], nargs="?", default="serve")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(args.config)
    if args.command == "run-once":
        result = Pipeline(settings, Database(settings.database_path)).run()
        print(result)
        raise SystemExit(0 if result["status"] in {"success", "partial"} else 1)
    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
