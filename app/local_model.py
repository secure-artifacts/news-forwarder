from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import httpx


class LocalModelServer:
    def __init__(self, root: Path, config: dict[str, Any]):
        self.root = root
        self.config = config
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        if not self.enabled or self.healthy():
            return
        executable = self.root / "runtime" / "ollama" / "lib" / "ollama" / "llama-server.exe"
        model = self.root / "data" / "models" / "translategemma-4b-q4_k_m.gguf"
        if not executable.exists() or not model.exists():
            raise RuntimeError("本地翻译引擎或 TranslateGemma 模型不存在")
        log_path = self.root / "local-translation.log"
        log_handle = log_path.open("ab")
        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        self.process = subprocess.Popen(
            [
                str(executable), "-m", str(model), "--host", "127.0.0.1",
                "--port", "11435", "--ctx-size", "4096", "--no-webui",
            ],
            cwd=self.root,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        for _ in range(90):
            if self.healthy():
                return
            if self.process.poll() is not None:
                raise RuntimeError("本地翻译服务启动失败，请查看 local-translation.log")
            time.sleep(1)
        raise RuntimeError("本地翻译模型加载超时")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.get("enabled", True)
            and self.config.get("provider") == "local_llama"
        )

    def healthy(self) -> bool:
        try:
            return httpx.get(
                self.config.get("base_url", "http://127.0.0.1:11435") + "/health",
                timeout=2,
            ).status_code == 200
        except httpx.HTTPError:
            return False
