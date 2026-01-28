"""リマインダーBot エントリーポイント"""

import logging
import sys

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
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
        logger.error(f"致命的エラー: {e}")
        raise


if __name__ == "__main__":
    main()
