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

# LLMフォールバック専用ログ（パターンマッチ失敗 → LLM解析した入力を記録）
llm_fallback_logger = logging.getLogger("llm_fallback")
llm_fallback_handler = RotatingFileHandler(
    LOGS_DIR / "llm_fallback.log",
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3,
    encoding="utf-8",
)
llm_fallback_handler.setFormatter(logging.Formatter(log_format))
llm_fallback_logger.addHandler(llm_fallback_handler)
llm_fallback_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)


def main():
    """メインエントリーポイント（クラッシュ時自動再起動）"""
    import time

    max_retries = 5
    retry_window = 300  # 5分以内に連続クラッシュしたらカウント
    retry_count = 0
    last_crash = 0

    while True:
        logger.info("リマインダーBot起動中...")
        start_time = time.time()

        try:
            from bot import run_bot
            run_bot()
            # 正常終了
            break
        except KeyboardInterrupt:
            logger.info("Bot停止（Ctrl+C）")
            break
        except Exception as e:
            now = time.time()
            logger.error(f"致命的エラー: {e}", exc_info=True)

            # 長時間動いていたらカウントリセット
            if now - last_crash > retry_window:
                retry_count = 0

            retry_count += 1
            last_crash = now

            if retry_count >= max_retries:
                logger.error(f"連続{max_retries}回クラッシュのため停止")
                break

            wait = min(30, 5 * retry_count)
            logger.info(f"{wait}秒後に再起動... (試行 {retry_count}/{max_retries})")
            time.sleep(wait)


if __name__ == "__main__":
    main()
