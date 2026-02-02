"""設定読み込みモジュール"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# プロジェクトルート
BASE_DIR = Path(__file__).parent

# .env読み込み
load_dotenv(BASE_DIR / ".env")


def get_env(key: str, required: bool = True) -> str:
    """環境変数を取得"""
    value = os.getenv(key)
    if required and not value:
        raise ValueError(f"環境変数 {key} が設定されていません。.env ファイルを確認してください。")
    return value or ""


# 環境変数
DISCORD_BOT_TOKEN = get_env("DISCORD_BOT_TOKEN")
GROQ_API_KEY = get_env("GROQ_API_KEY", required=False)

# パス
DB_PATH = BASE_DIR / "reminders.db"

# タイムゾーン（日本）
TIMEZONE = "Asia/Tokyo"

# スケジューラ設定
SCHEDULER_CHECK_INTERVAL_SEC = 30  # リマインダーチェック間隔

# ログ設定
LOGS_DIR = BASE_DIR / "logs"

# 専用チャンネル設定（DiscordのチャンネルID）
REMINDER_CHANNEL_ID = get_env("REMINDER_CHANNEL_ID", required=False)

# 特殊コマンド（専用チャンネル内）
SPECIAL_COMMANDS = {
    "一覧": "list",
    "リスト": "list",
    "確認": "list",
}
