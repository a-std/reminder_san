"""日時解析モジュール（パターンマッチ優先、LLMフォールバック）"""

import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from openai import OpenAI

from config import GROQ_API_KEY, TIMEZONE

logger = logging.getLogger(__name__)
llm_fallback_logger = logging.getLogger("llm_fallback")

# Groqクライアント
client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

# LLM用スキーマ
PARSE_DATETIME_TOOL = {
    "type": "function",
    "function": {
        "name": "set_datetime",
        "description": "解析した日時を設定する",
        "parameters": {
            "type": "object",
            "properties": {
                "datetime": {
                    "type": "string",
                    "description": "日時をISO8601形式(YYYY-MM-DDTHH:MM:SS)で返す",
                },
            },
            "required": ["datetime"],
        },
    },
}


def normalize_numbers(text: str) -> str:
    """全角数字を半角に変換"""
    zen = '０１２３４５６７８９'
    han = '0123456789'
    for z, h in zip(zen, han):
        text = text.replace(z, h)
    return text


WEEKDAY_MAP = {'月': 0, '火': 1, '水': 2, '木': 3, '金': 4, '土': 5, '日': 6}
WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']


def extract_hour(t: str, default: int = 9) -> int:
    """テキストから時刻を抽出"""
    # 午後X時
    m = re.search(r'午後\s*(\d+)\s*時', t)
    if m:
        h = int(m.group(1))
        return h + 12 if h < 12 else h
    # 午前X時
    m = re.search(r'午前\s*(\d+)\s*時', t)
    if m:
        return int(m.group(1))
    # X時半 / X時Y分 / X時
    m = re.search(r'(\d+)\s*時', t)
    if m:
        h = int(m.group(1))
        # 昼/夕方/夜のコンテキストがあれば1〜11時をPMに補正
        is_pm_context = any(w in t for w in ['昼', '夕方', '夜', '深夜'])
        is_am_context = '朝' in t
        if is_pm_context and not is_am_context and 1 <= h <= 11:
            h += 12
        return h
    # 朝昼夕夜（時刻指定なし）— 長いキーワードを先にチェック
    if '朝' in t:
        return 8
    if '正午' in t or 'お昼' in t:
        return 12
    if '昼' in t:
        return 12
    if '夕方' in t:
        return 17
    if '深夜' in t:
        return 23
    if '夜' in t:
        return 20
    return default


def extract_minute(t: str) -> int:
    """テキストから分を抽出"""
    # X時半
    if re.search(r'\d+\s*時\s*半', t):
        return 30
    # X時Y分
    m = re.search(r'\d+\s*時\s*(\d+)\s*分', t)
    if m:
        return int(m.group(1))
    return 0


def parse_repeat_pattern(user_input: str, now: datetime, tz: ZoneInfo) -> dict | None:
    """繰り返し表現をパターンマッチで解析

    Returns:
        {"repeat_type": str, "repeat_value": str|None, "remind_at": datetime} or None
    """
    text = normalize_numbers(user_input)
    # contentを除去した日時表現部分のみから時刻を抽出する
    content = extract_content(user_input)
    time_text = text.replace(content, '') if content != user_input else text

    def make_dt(base_date: datetime, hour: int, minute: int) -> datetime:
        return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def next_weekday_date(target_wd: int) -> datetime:
        """次の指定曜日の日付を返す（今日が該当曜日なら来週）"""
        days_ahead = target_wd - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return now + timedelta(days=days_ahead)

    def nth_weekday_of_month(year: int, month: int, nth: int, weekday: int) -> datetime | None:
        """指定月の第N X曜日を計算。存在しなければNone"""
        # 1日の曜日
        first = datetime(year, month, 1, tzinfo=tz)
        # 第1 X曜日
        days_ahead = weekday - first.weekday()
        if days_ahead < 0:
            days_ahead += 7
        first_target = first + timedelta(days=days_ahead)
        # 第N
        result = first_target + timedelta(weeks=nth - 1)
        if result.month != month:
            return None
        return result

    def next_month_year(y: int, m: int):
        nm = m + 1
        ny = y
        if nm > 12:
            nm = 1
            ny += 1
        return ny, nm

    def find_next_nth_weekday(nths: list[int], weekday: int, offset_days: int = 0) -> datetime | None:
        """複数の第N指定から、now以降の最も近い日を返す"""
        hour = extract_hour(time_text, default=9)
        minute = extract_minute(time_text)
        candidates = []
        # 今月と来月から候補を集める
        for y, m_val in [(now.year, now.month), (*next_month_year(now.year, now.month),)]:
            for n in nths:
                target = nth_weekday_of_month(y, m_val, n, weekday)
                if target is not None:
                    dt = make_dt(target + timedelta(days=offset_days), hour, minute)
                    if dt > now:
                        candidates.append(dt)
        if candidates:
            return min(candidates)
        return None

    # 毎月第N(,N) X曜日の前日（複数対応）
    m = re.search(r'毎月\s*第\s*([\d,、]+)\s*([月火水木金土日])\s*曜?日?\s*の?\s*前日', text)
    if m:
        nths = [int(n) for n in re.split(r'[,、]', m.group(1)) if n.strip()]
        wd_name = m.group(2)
        target_wd = WEEKDAY_MAP[wd_name]
        remind_at = find_next_nth_weekday(nths, target_wd, offset_days=-1)
        if remind_at is None:
            return None
        nth_str = ",".join(str(n) for n in nths)
        return {"repeat_type": "monthly", "repeat_value": f"第{nth_str}{wd_name}の前日", "remind_at": remind_at}

    # 毎月第N(,N) X曜日（複数対応）
    m = re.search(r'毎月\s*第\s*([\d,、]+)\s*([月火水木金土日])\s*曜?日?', text)
    if m:
        nths = [int(n) for n in re.split(r'[,、]', m.group(1)) if n.strip()]
        wd_name = m.group(2)
        target_wd = WEEKDAY_MAP[wd_name]
        remind_at = find_next_nth_weekday(nths, target_wd)
        if remind_at is None:
            return None
        nth_str = ",".join(str(n) for n in nths)
        return {"repeat_type": "monthly", "repeat_value": f"第{nth_str}{wd_name}", "remind_at": remind_at}

    # 毎月X日
    m = re.search(r'毎月\s*(\d+)\s*日', text)
    if m:
        day = int(m.group(1))
        hour = extract_hour(time_text, default=9)
        minute = extract_minute(time_text)
        # 今月の指定日
        try:
            remind_at = make_dt(now.replace(day=day), hour, minute)
        except ValueError:
            # 日付が無効（31日がない月等）→ 来月
            next_month = now.month + 1
            year = now.year
            if next_month > 12:
                next_month = 1
                year += 1
            remind_at = make_dt(datetime(year, next_month, day, tzinfo=tz), hour, minute)
        else:
            if remind_at <= now:
                # 来月
                next_month = now.month + 1
                year = now.year
                if next_month > 12:
                    next_month = 1
                    year += 1
                remind_at = make_dt(datetime(year, next_month, day, tzinfo=tz), hour, minute)
        return {"repeat_type": "monthly", "repeat_value": str(day), "remind_at": remind_at}

    # 隔週X曜
    m = re.search(r'隔週\s*([月火水木金土日])\s*曜?日?', text)
    if m:
        wd_name = m.group(1)
        target_wd = WEEKDAY_MAP[wd_name]
        hour = extract_hour(time_text, default=9)
        minute = extract_minute(time_text)
        target_date = next_weekday_date(target_wd)
        remind_at = make_dt(target_date, hour, minute)
        return {"repeat_type": "biweekly", "repeat_value": wd_name, "remind_at": remind_at}

    # 毎週X曜
    m = re.search(r'毎週\s*([月火水木金土日])\s*曜?日?', text)
    if m:
        wd_name = m.group(1)
        target_wd = WEEKDAY_MAP[wd_name]
        hour = extract_hour(time_text, default=9)
        minute = extract_minute(time_text)
        target_date = next_weekday_date(target_wd)
        remind_at = make_dt(target_date, hour, minute)
        return {"repeat_type": "weekly", "repeat_value": wd_name, "remind_at": remind_at}

    # 毎週（曜日なし）→ 今日の曜日
    if re.search(r'毎週', text):
        wd_name = WEEKDAY_NAMES[now.weekday()]
        hour = extract_hour(time_text, default=9)
        minute = extract_minute(time_text)
        target_date = next_weekday_date(now.weekday())
        remind_at = make_dt(target_date, hour, minute)
        return {"repeat_type": "weekly", "repeat_value": wd_name, "remind_at": remind_at}

    # 毎朝 → daily, デフォルト8:00
    if '毎朝' in text:
        hour = extract_hour(time_text.replace('毎朝', ''), default=8)
        minute = extract_minute(time_text)
        remind_at = make_dt(now, hour, minute)
        if remind_at <= now:
            remind_at += timedelta(days=1)
        return {"repeat_type": "daily", "repeat_value": None, "remind_at": remind_at}

    # 毎晩 → daily, デフォルト20:00
    if '毎晩' in text:
        hour = extract_hour(time_text.replace('毎晩', ''), default=20)
        minute = extract_minute(time_text)
        remind_at = make_dt(now, hour, minute)
        if remind_at <= now:
            remind_at += timedelta(days=1)
        return {"repeat_type": "daily", "repeat_value": None, "remind_at": remind_at}

    # 毎夕(方)? → daily, デフォルト17:00
    if re.search(r'毎夕(方)?', text):
        hour = extract_hour(time_text.replace('毎夕方', '').replace('毎夕', ''), default=17)
        minute = extract_minute(time_text)
        remind_at = make_dt(now, hour, minute)
        if remind_at <= now:
            remind_at += timedelta(days=1)
        return {"repeat_type": "daily", "repeat_value": None, "remind_at": remind_at}

    # 毎日
    if '毎日' in text:
        hour = extract_hour(time_text, default=9)
        minute = extract_minute(time_text)
        remind_at = make_dt(now, hour, minute)
        if remind_at <= now:
            remind_at += timedelta(days=1)
        return {"repeat_type": "daily", "repeat_value": None, "remind_at": remind_at}

    # 平日
    if '平日' in text:
        hour = extract_hour(time_text, default=9)
        minute = extract_minute(time_text)
        remind_at = make_dt(now, hour, minute)
        if remind_at <= now or now.weekday() >= 5:
            # 次の平日を探す
            remind_at += timedelta(days=1)
            while remind_at.weekday() >= 5:
                remind_at += timedelta(days=1)
        return {"repeat_type": "weekdays", "repeat_value": None, "remind_at": remind_at}

    return None


def parse_datetime_pattern(user_input: str, now: datetime, tz: ZoneInfo) -> datetime | None:
    """正規表現パターンで日時を解析"""
    text = normalize_numbers(user_input)
    weekdays = WEEKDAY_MAP

    def make_time(base_date: datetime, t: str, default_hour: int = 9) -> datetime:
        """日付と時刻を組み合わせる"""
        hour = extract_hour(t, default_hour)
        minute = extract_minute(t)
        return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # === 相対時間 ===

    # X時間半後
    m = re.search(r'(\d+)\s*時間\s*半\s*後', text)
    if m:
        return now + timedelta(hours=int(m.group(1)), minutes=30)

    # X時間後
    m = re.search(r'(\d+)\s*時間\s*後', text)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    # X分後 / あとX分
    m = re.search(r'(\d+)\s*分\s*後', text) or re.search(r'あと\s*(\d+)\s*分', text)
    if m:
        return now + timedelta(minutes=int(m.group(1)))

    # あとX時間
    m = re.search(r'あと\s*(\d+)\s*時間', text)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    # X日後 / あとX日
    m = re.search(r'(\d+)\s*日\s*後', text) or re.search(r'あと\s*(\d+)\s*日', text)
    if m:
        days = int(m.group(1))
        target = now + timedelta(days=days)
        return make_time(target, text)

    # X週間後
    m = re.search(r'(\d+)\s*週間?\s*後', text)
    if m:
        weeks = int(m.group(1))
        target = now + timedelta(weeks=weeks)
        return make_time(target, text)

    # === 特定日付 ===

    # 明々後日（しあさって）
    if '明々後日' in text or 'しあさって' in text:
        day = now + timedelta(days=3)
        return make_time(day, text)

    # 明後日
    if '明後日' in text:
        day = now + timedelta(days=2)
        return make_time(day, text)

    # 明日
    if '明日' in text:
        day = now + timedelta(days=1)
        return make_time(day, text)

    # 今日
    if '今日' in text:
        return make_time(now, text, default_hour=now.hour + 1)

    # === 週末 ===

    # 来週末
    if '来週末' in text:
        days_until_monday = (7 - now.weekday()) % 7 or 7
        next_saturday = now + timedelta(days=days_until_monday + 5)
        return make_time(next_saturday, text)

    # 今週末 / 週末
    if '今週末' in text or '週末' in text:
        days_until_saturday = (5 - now.weekday()) % 7
        if days_until_saturday == 0 and now.weekday() == 5:
            saturday = now
        elif days_until_saturday == 0:
            days_until_saturday = 7
        else:
            pass
        saturday = now + timedelta(days=(5 - now.weekday()) % 7 or 7)
        if now.weekday() == 5:  # 今日が土曜
            saturday = now
        elif now.weekday() == 6:  # 今日が日曜
            saturday = now
        return make_time(saturday, text)

    # === 再来月 ===

    # 再来月末
    if '再来月末' in text:
        month = now.month + 2
        year = now.year
        while month > 12:
            month -= 12
            year += 1
        if month == 12:
            last_day = datetime(year + 1, 1, 1, tzinfo=tz) - timedelta(days=1)
        else:
            last_day = datetime(year, month + 1, 1, tzinfo=tz) - timedelta(days=1)
        return make_time(last_day, text)

    # 再来月初
    if '再来月初' in text:
        month = now.month + 2
        year = now.year
        while month > 12:
            month -= 12
            year += 1
        first_day = datetime(year, month, 1, tzinfo=tz)
        return make_time(first_day, text)

    # 再来月X日
    m = re.search(r'再来月\s*(\d+)\s*日', text)
    if m:
        day = int(m.group(1))
        month = now.month + 2
        year = now.year
        while month > 12:
            month -= 12
            year += 1
        try:
            target = datetime(year, month, day, tzinfo=tz)
        except ValueError:
            return None
        return make_time(target, text)

    # 再来月（日付なし）
    if '再来月' in text:
        month = now.month + 2
        year = now.year
        while month > 12:
            month -= 12
            year += 1
        first_day = datetime(year, month, 1, tzinfo=tz)
        return make_time(first_day, text)

    # === 月末・月初 ===

    # 来月末
    if '来月末' in text:
        next_month = now.month + 1
        year = now.year
        if next_month > 12:
            next_month = 1
            year += 1
        # 来月の最終日
        if next_month == 12:
            last_day = datetime(year + 1, 1, 1, tzinfo=tz) - timedelta(days=1)
        else:
            last_day = datetime(year, next_month + 1, 1, tzinfo=tz) - timedelta(days=1)
        return make_time(last_day, text)

    # 月末 / 今月末
    if '月末' in text or '今月末' in text:
        next_month = now.month + 1
        year = now.year
        if next_month > 12:
            next_month = 1
            year += 1
        last_day = datetime(year, next_month, 1, tzinfo=tz) - timedelta(days=1)
        return make_time(last_day, text)

    # 来月初
    if '来月初' in text:
        next_month = now.month + 1
        year = now.year
        if next_month > 12:
            next_month = 1
            year += 1
        first_day = datetime(year, next_month, 1, tzinfo=tz)
        return make_time(first_day, text)

    # 月初 / 今月初
    if '月初' in text:
        first_day = now.replace(day=1)
        return make_time(first_day, text)

    # === 今月X日 / 来月X日 ===

    # 来月X日
    m = re.search(r'来月\s*(\d+)\s*日', text)
    if m:
        day = int(m.group(1))
        month = now.month + 1
        year = now.year
        if month > 12:
            month = 1
            year += 1
        try:
            target = datetime(year, month, day, tzinfo=tz)
        except ValueError:
            return None
        return make_time(target, text)

    # 今月X日
    m = re.search(r'今月\s*(\d+)\s*日', text)
    if m:
        day = int(m.group(1))
        try:
            target = now.replace(day=day)
        except ValueError:
            return None
        return make_time(target, text)

    # === 曜日 ===

    # 再来週のX曜日
    m = re.search(r'再来週\s*の?\s*([月火水木金土日])\s*曜?日?', text)
    if m:
        target_weekday = weekdays[m.group(1)]
        days_until_monday = (7 - now.weekday()) % 7 or 7
        next_next_monday = now + timedelta(days=days_until_monday + 7)
        target_date = next_next_monday + timedelta(days=target_weekday)
        return make_time(target_date, text)

    # 来週のX曜日
    m = re.search(r'来週\s*の?\s*([月火水木金土日])\s*曜?日?', text)
    if m:
        target_weekday = weekdays[m.group(1)]
        days_until_monday = (7 - now.weekday()) % 7 or 7
        next_monday = now + timedelta(days=days_until_monday)
        target_date = next_monday + timedelta(days=target_weekday)
        return make_time(target_date, text)

    # 今週のX曜日
    m = re.search(r'今週\s*の?\s*([月火水木金土日])\s*曜?日?', text)
    if m:
        target_weekday = weekdays[m.group(1)]
        this_monday = now - timedelta(days=now.weekday())
        target_date = this_monday + timedelta(days=target_weekday)
        return make_time(target_date, text)

    # 次のX曜日 / 今度のX曜日
    m = re.search(r'(次|今度)\s*の?\s*([月火水木金土日])\s*曜?日?', text)
    if m:
        target_weekday = weekdays[m.group(2)]
        days_ahead = target_weekday - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        target_date = now + timedelta(days=days_ahead)
        return make_time(target_date, text)

    # === 日付指定 ===

    # X月X日
    m = re.search(r'(\d+)\s*月\s*(\d+)\s*日', text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year
        try:
            target = datetime(year, month, day, tzinfo=tz)
            if target.date() < now.date():
                target = datetime(year + 1, month, day, tzinfo=tz)
        except ValueError:
            return None
        return make_time(target, text)

    # === 時刻のみ ===

    # 正午 / お昼
    if '正午' in text or 'お昼' in text:
        result = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result

    # 深夜
    if '深夜' in text:
        result = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result

    # 午後X時 / 午前X時
    m = re.search(r'午後\s*(\d+)\s*時', text)
    if m:
        hour = int(m.group(1))
        hour = hour + 12 if hour < 12 else hour
        minute = extract_minute(text)
        result = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result

    m = re.search(r'午前\s*(\d+)\s*時', text)
    if m:
        hour = int(m.group(1))
        minute = extract_minute(text)
        result = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result

    # X時半
    m = re.search(r'(\d+)\s*時\s*半', text)
    if m and not any(w in text for w in ['明日', '明後日', '来週', '今週', '次の', '月', '今度']):
        hour = int(m.group(1))
        result = now.replace(hour=hour, minute=30, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result

    # X時Y分
    m = re.search(r'(\d+)\s*時\s*(\d+)\s*分', text)
    if m and not any(w in text for w in ['明日', '明後日', '来週', '今週', '次の', '月', '今度']):
        hour, minute = int(m.group(1)), int(m.group(2))
        result = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result

    # X時（今日または翌日）
    m = re.search(r'(\d+)\s*時', text)
    if m and not any(w in text for w in ['明日', '明後日', '来週', '今週', '次の', '月', '今度', '午前', '午後']):
        hour = int(m.group(1))
        result = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result

    # 朝/昼/夕方/夜（単体）
    time_words = {'朝': 8, '昼': 12, '夕方': 17, '夜': 20}
    for word, hour in time_words.items():
        if word in text:
            result = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if result <= now:
                result += timedelta(days=1)
            return result

    return None


def extract_content(user_input: str) -> str:
    """ユーザー入力から日時表現を除去してcontentを抽出"""
    # 「から揚げ」「から拭き」「からし」「からあげ」を保護する「から」
    _kara = r'から(?!揚|拭|し|あげ)'
    # 末尾の助詞パターン（「のから」等の二重助詞にも対応、複合語を保護）
    _p = rf'(の{_kara}|のまでに|のまで|までに|に|{_kara}|まで|で|の)?'
    # 日付のみ（時刻なし）用の助詞パターン（「にんじん」「でかける」等も保護）
    _p_date = rf'({_kara}|までに|まで|に(?!ん|こ|が)|で(?!ん|か|き))?'
    # 時刻コンテキスト語（長い語を先に）
    _t = r'(朝イチ|朝|夕方|深夜|夜|昼|正午|お昼)'

    patterns = [
        # 繰り返し表現
        rf'毎月\s*第\s*[\d,、]+\s*[月火水木金土日]\s*曜?日?\s*の?\s*前日\s*の?\s*({_t}\s*)?\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        rf'毎月\s*第\s*[\d,、]+\s*[月火水木金土日]\s*曜?日?\s*の?\s*({_t}\s*)?\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        rf'毎月\s*\d+\s*日\s*の?\s*({_t}\s*)?\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        rf'(隔週|毎週)\s*[月火水木金土日]?\s*曜?日?\s*の?\s*({_t}\s*)?\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        rf'毎(朝|晩|夕方?|日)\s*の?\s*({_t}\s*)?\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        rf'平日\s*の?\s*({_t}\s*)?\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        rf'\d+\s*日\s*後\s*{_p}',
        rf'\d+\s*週間?\s*後\s*{_p}',
        rf'あと\s*\d+\s*日\s*{_p}',
        rf'\d+\s*時間\s*半?\s*後\s*{_p}',
        rf'\d+\s*分\s*後\s*{_p}',
        rf'あと\s*\d+\s*(分|時間)\s*{_p}',
        # 明日/今日 + 時刻あり → 助詞除去
        rf'明々?後?日\s*の?\s*{_t}?\s*\d+\s*時\s*半?\s*\d*\s*分?\s*{_p}',
        rf'今日\s*の?\s*{_t}?\s*\d+\s*時\s*半?\s*\d*\s*分?\s*{_p}',
        # 明日/今日 + 朝昼夕夜のみ → 助詞除去
        rf'明々?後?日\s*の?\s*{_t}\s*{_p}',
        rf'今日\s*の?\s*{_t}\s*{_p}',
        # 明日/今日のみ（時刻なし）→ 助詞除去（複合語保護付き）
        rf'明々?後?日(\s*の(?=\s*(\d|朝|昼|夕|夜|午|から|まで)))?\s*{_p_date}',
        rf'今日(\s*の(?=\s*(\d|朝|昼|夕|夜|午|から|まで)))?\s*{_p_date}',
        rf'(今|来|再来)週\s*(末|の?\s*[月火水木金土日]\s*曜?日?)?\s*(の?\s*{_t})?\s*\d*\s*時?\s*半?\s*{_p}',
        rf'(次|今度)\s*の?\s*[月火水木金土日]\s*曜?日?\s*(の?\s*{_t})?\s*\d*\s*時?\s*半?\s*{_p}',
        # 再来月X日 / 来月X日 / 今月X日
        rf'再来月\s*(末|初|\d+\s*日)?\s*の?\s*({_t}\s*)?\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        rf'(今|来)月\s*\d+\s*日\s*の?\s*({_t}\s*)?\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        # X月X日を先に処理（「(今|来)?月」が「2月」の月を食わないように）
        rf'\d+\s*月\s*\d+\s*日\s*(の?\s*{_t})?\s*\d*\s*時?\s*半?\s*\d*\s*分?\s*{_p}',
        rf'(今|来)?月\s*(末|初)?\s*の?\s*{_t}?\s*\d*\s*時?\s*{_p}',
        rf'午前\s*\d+\s*時\s*半?\s*\d*\s*分?\s*{_p}',
        rf'午後\s*\d+\s*時\s*半?\s*\d*\s*分?\s*{_p}',
        rf'\d+\s*時\s*半?\s*\d*\s*分?\s*{_p}',
        rf'{_t}(?=\s|に|で|の|から|まで|\d|$)\s*{_p}',
        rf'週末\s*{_p}',
    ]

    content = normalize_numbers(user_input)
    for pattern in patterns:
        content = re.sub(pattern, '', content)

    # 除去後に残った先頭の孤立助詞を除去（直後にスペースがある場合のみ）
    content = re.sub(r'^[のにでへ]\s+', '', content)
    content = re.sub(r'\s+', ' ', content).strip()
    return content if content else user_input


async def parse_datetime_llm(user_input: str, now: datetime, tz: ZoneInfo) -> datetime | None:
    """LLMで日時を解析（フォールバック用）"""
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"]

    days_until_monday = (7 - now.weekday()) % 7 or 7
    next_monday = now + timedelta(days=days_until_monday)
    next_week = {d: (next_monday + timedelta(days=i)).strftime('%Y-%m-%d') for i, d in enumerate(weekday_ja)}

    prompt = f"""日時を解析してISO8601形式で返してください。

現在: {now.strftime('%Y-%m-%d')} ({weekday_ja[now.weekday()]}曜) {now.strftime('%H:%M')}
来週: 月={next_week['月']}, 火={next_week['火']}, 水={next_week['水']}, 木={next_week['木']}, 金={next_week['金']}, 土={next_week['土']}, 日={next_week['日']}

入力: {user_input}"""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            tools=[PARSE_DATETIME_TOOL],
            tool_choice={"type": "function", "function": {"name": "set_datetime"}},
        )

        message = response.choices[0].message
        if message.tool_calls:
            args = json.loads(message.tool_calls[0].function.arguments)
            dt_str = args.get("datetime", "")
            logger.info(f"LLM解析結果: {dt_str}")
            try:
                result = datetime.fromisoformat(dt_str)
                if result.tzinfo is None:
                    result = result.replace(tzinfo=tz)
                return result
            except ValueError:
                return None
        return None
    except Exception as e:
        logger.error(f"LLM解析エラー: {e}")
        return None


async def parse_reminder_input(user_input: str) -> dict | None:
    """ユーザー入力を解析してリマインダー情報を抽出"""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    # 先に繰り返しパターンをチェック
    repeat_result = parse_repeat_pattern(user_input, now, tz)
    if repeat_result:
        content = extract_content(user_input)
        return {
            "content": content,
            "datetime": repeat_result["remind_at"],
            "repeat_type": repeat_result["repeat_type"],
            "repeat_value": repeat_result.get("repeat_value"),
        }

    # まずパターンマッチで解析
    remind_at = parse_datetime_pattern(user_input, now, tz)

    # パターンで解析できなければLLMにフォールバック
    if remind_at is None:
        logger.info(f"パターンマッチ失敗、LLMで解析: {user_input}")
        llm_fallback_logger.info(f"入力: {user_input}")
        remind_at = await parse_datetime_llm(user_input, now, tz)
        if remind_at:
            llm_fallback_logger.info(f"LLM解析成功: {user_input} -> {remind_at.isoformat()}")
        else:
            llm_fallback_logger.warning(f"LLM解析失敗: {user_input}")

    if remind_at is None:
        return None

    content = extract_content(user_input)

    return {
        "content": content,
        "datetime": remind_at,
    }
