"""读取本地配置，避免在代码或数据库中保存密钥。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.secrets import load_stored_secrets


@dataclass(frozen=True)
class Settings:
    """应用运行配置。"""

    database_path: Path
    max_upload_bytes: int
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    moonshot_api_key: str | None
    moonshot_base_url: str
    moonshot_model: str


def load_settings() -> Settings:
    """从环境变量构造配置，并对数值设置安全下限。"""

    max_upload_mb = max(1, min(200, int(os.getenv("NOVEL_MAX_UPLOAD_MB", "30"))))
    stored_secrets = load_stored_secrets()
    return Settings(
        database_path=Path(os.getenv("NOVEL_DB_PATH", "data/novel_atlas.db")),
        max_upload_bytes=max_upload_mb * 1024 * 1024,
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or stored_secrets.get("deepseek") or None,
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        moonshot_api_key=os.getenv("MOONSHOT_API_KEY") or stored_secrets.get("moonshot") or None,
        moonshot_base_url=os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1").rstrip("/"),
        moonshot_model=os.getenv("MOONSHOT_MODEL", "kimi-k2.5"),
    )
