"""リマインダーBot エントリーポイント"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import LOGS_DIR

# ロックファイル（多重起動防止用）
LOCK_FILE = Path(__file__).parent / "bot.lock"

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


def _is_process_alive(pid: int) -> bool:
    """PIDのプロセスが生存しているか確認"""
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # プロセスは存在するが権限不足
    except OSError:
        return False


def _acquire_lock():
    """ロックファイルに自分のPIDを書き込む（常に上書き）"""
    my_pid = os.getpid()
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            if old_pid != my_pid and _is_process_alive(old_pid):
                logger.warning(f"旧プロセス（PID {old_pid}）が生存中。上書きして引き継ぎます。")
        except (ValueError, OSError):
            pass
    LOCK_FILE.write_text(str(my_pid))
    logger.info(f"ロックファイル取得: PID {my_pid}")


def main():
    """メインエントリーポイント（クラッシュ時自動再起動）"""
    import time

    max_retries = 5
    retry_window = 300  # 5分以内に連続クラッシュしたらカウント
    retry_count = 0
    last_crash = 0

    # ヘルスエンドポイント起動
    try:
        from health_server import start_health_server
        start_health_server(port=18791)
    except Exception as e:
        logger.warning(f"Health server failed to start: {e}")

    while True:
        _acquire_lock()
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
