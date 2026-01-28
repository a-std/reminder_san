"""リマインダーBot エントリーポイント"""

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR

# ログディレクトリ作成
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ロギング設定
log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
log_file = LOGS_DIR / f"bot_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)

# discord.pyのログレベルを調整
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def main():
    """メインエントリーポイント"""
    logger.info("リマインダーBot起動中...")

    try:
        from bot import run_bot
        run_bot()
    except KeyboardInterrupt:
        logger.info("Bot停止（Ctrl+C）")
    except Exception as e:
        logger.error(f"致命的エラー: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
