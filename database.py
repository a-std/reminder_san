"""SQLiteデータベース操作モジュール（非同期・共有接続）"""

import asyncio
import logging
from datetime import datetime

import aiosqlite

from config import DB_PATH

logger = logging.getLogger(__name__)

# 共有接続（asyncio.Lockで競合状態を防止）
_db: aiosqlite.Connection | None = None
_db_lock = asyncio.Lock()


async def _get_db() -> aiosqlite.Connection:
    """共有DB接続を取得（未接続なら接続）"""
    global _db
    async with _db_lock:
        if _db is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _db = await aiosqlite.connect(DB_PATH)
            _db.row_factory = aiosqlite.Row
            # WALモードで読み書きの並行性向上
            result = await _db.execute("PRAGMA journal_mode=WAL")
            mode = await result.fetchone()
            logger.info(f"SQLite journal_mode: {mode[0] if mode else 'unknown'}")
    return _db


async def close_db():
    """共有接続を閉じる"""
    global _db
    async with _db_lock:
        if _db is not None:
            await _db.close()
            _db = None


async def init_db():
    """データベース初期化"""
    db = await _get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            guild_id TEXT,
            channel_id TEXT NOT NULL,
            content TEXT NOT NULL,
            remind_at DATETIME NOT NULL,
            repeat_type TEXT,
            repeat_value TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_remind_at ON reminders(remind_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON reminders(user_id)")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_active_remind_at ON reminders(is_active, remind_at)"
    )

    # Bot状態保存テーブル（常設メッセージIDなど）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    await db.commit()
    logger.info("データベース初期化完了")


async def create_reminder(
    user_id: str,
    guild_id: str | None,
    channel_id: str,
    content: str,
    remind_at: datetime,
    repeat_type: str | None = None,
    repeat_value: str | None = None,
) -> int:
    """リマインダーを作成"""
    db = await _get_db()
    cursor = await db.execute(
        """INSERT INTO reminders
           (user_id, guild_id, channel_id, content, remind_at, repeat_type, repeat_value)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, guild_id, channel_id, content, remind_at.isoformat(), repeat_type, repeat_value),
    )
    await db.commit()
    reminder_id = cursor.lastrowid
    logger.info(f"リマインダー作成: ID={reminder_id}, user={user_id}, at={remind_at}")
    return reminder_id


async def get_due_reminders(now: datetime) -> list[dict]:
    """通知すべきリマインダーを取得"""
    db = await _get_db()
    cursor = await db.execute(
        """SELECT * FROM reminders
           WHERE is_active = 1 AND remind_at <= ?
           ORDER BY remind_at""",
        (now.isoformat(),),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_user_reminders(user_id: str, include_inactive: bool = False) -> list[dict]:
    """ユーザーのリマインダー一覧を取得"""
    db = await _get_db()
    if include_inactive:
        cursor = await db.execute(
            "SELECT * FROM reminders WHERE user_id = ? ORDER BY remind_at",
            (user_id,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND is_active = 1 ORDER BY remind_at",
            (user_id,),
        )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_reminder_by_id(reminder_id: int) -> dict | None:
    """IDでリマインダーを取得"""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE id = ?",
        (reminder_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def deactivate_reminder(reminder_id: int) -> bool:
    """リマインダーを非アクティブにする"""
    db = await _get_db()
    cursor = await db.execute(
        "UPDATE reminders SET is_active = 0 WHERE id = ?",
        (reminder_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_reminder(reminder_id: int, user_id: str) -> bool:
    """リマインダーを削除（ユーザー確認付き）"""
    db = await _get_db()
    cursor = await db.execute(
        "DELETE FROM reminders WHERE id = ? AND user_id = ?",
        (reminder_id, user_id),
    )
    await db.commit()
    deleted = cursor.rowcount > 0
    if deleted:
        logger.info(f"リマインダー削除: ID={reminder_id}, user={user_id}")
    return deleted


async def update_reminder_time(reminder_id: int, new_remind_at: datetime) -> bool:
    """リマインダーの通知時刻を更新（繰り返し用）"""
    db = await _get_db()
    cursor = await db.execute(
        "UPDATE reminders SET remind_at = ? WHERE id = ?",
        (new_remind_at.isoformat(), reminder_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_bot_state(key: str) -> str | None:
    """Bot状態を取得"""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT value FROM bot_state WHERE key = ?",
        (key,),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def set_bot_state(key: str, value: str) -> None:
    """Bot状態を保存"""
    db = await _get_db()
    await db.execute(
        "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
        (key, value),
    )
    await db.commit()


async def get_all_active_reminders() -> list[dict]:
    """全ユーザーのアクティブなリマインダーを取得"""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE is_active = 1 ORDER BY remind_at",
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def update_reminder_content(reminder_id: int, user_id: str, new_content: str) -> bool:
    """リマインダーの内容を更新（ユーザー所有権チェック付き）"""
    db = await _get_db()
    cursor = await db.execute(
        "UPDATE reminders SET content = ? WHERE id = ? AND user_id = ?",
        (new_content, reminder_id, user_id),
    )
    await db.commit()
    if cursor.rowcount > 0:
        logger.info(f"リマインダー内容更新: ID={reminder_id}, user={user_id}")
        return True
    return False


async def update_reminder_time_by_user(reminder_id: int, user_id: str, new_time: datetime) -> bool:
    """リマインダーの通知時刻を更新（ユーザー所有権チェック付き）"""
    db = await _get_db()
    cursor = await db.execute(
        "UPDATE reminders SET remind_at = ? WHERE id = ? AND user_id = ?",
        (new_time.isoformat(), reminder_id, user_id),
    )
    await db.commit()
    if cursor.rowcount > 0:
        logger.info(f"リマインダー時刻更新: ID={reminder_id}, user={user_id}, new_time={new_time}")
        return True
    return False


async def snooze_reminder(reminder_id: int, new_remind_at: datetime) -> bool:
    """リマインダーをスヌーズ（再通知時刻を設定）"""
    db = await _get_db()
    cursor = await db.execute(
        "UPDATE reminders SET remind_at = ?, is_active = 1 WHERE id = ?",
        (new_remind_at.isoformat(), reminder_id),
    )
    await db.commit()
    if cursor.rowcount > 0:
        logger.info(f"リマインダースヌーズ: ID={reminder_id}, new_time={new_remind_at}")
        return True
    return False
