"""共通ユーティリティ"""

from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

REPEAT_TYPE_MAP = {
    "daily": "毎日",
    "weekly": "毎週",
    "monthly": "毎月",
    "biweekly": "隔週",
    "weekdays": "平日",
}


def format_repeat_label(repeat_type: str, repeat_value: str | None = None) -> str:
    """繰り返し設定を表示用ラベルにフォーマット"""
    base = REPEAT_TYPE_MAP.get(repeat_type, repeat_type)
    if not repeat_value:
        return base
    if repeat_type == "monthly":
        if repeat_value.isdigit():
            return f"毎月{repeat_value}日"
        return f"毎月{repeat_value}"
    return f"{base}{repeat_value}"


def parse_datetime_input(date_str: str, time_str: str) -> datetime | None:
    """日付文字列と時刻文字列からdatetimeを生成。パース失敗時はNone。"""
    tz = ZoneInfo(TIMEZONE)
    try:
        if "/" in date_str:
            parts = date_str.split("/")
            if len(parts) == 3:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            elif len(parts) == 2:
                year = datetime.now(tz).year
                month, day = int(parts[0]), int(parts[1])
            else:
                return None
        else:
            return None

        time_parts = time_str.split(":")
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

        return datetime(year, month, day, hour, minute, tzinfo=tz)
    except (ValueError, IndexError):
        return None
