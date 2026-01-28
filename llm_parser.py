"""Gemini APIで自然言語を解析するモジュール"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import google.generativeai as genai

from config import GEMINI_API_KEY, TIMEZONE

logger = logging.getLogger(__name__)

# Gemini設定
genai.configure(api_key=GEMINI_API_KEY)

# Function Callingスキーマ
CREATE_REMINDER_SCHEMA = {
    "name": "create_reminder",
    "description": "ユーザーの入力からリマインダー情報を抽出して登録する",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "リマインドする内容（例：歯医者、会議、友達とご飯）",
            },
            "datetime": {
                "type": "string",
                "description": "通知日時（ISO 8601形式、例：2024-07-12T18:00:00）",
            },
            "repeat_type": {
                "type": "string",
                "enum": ["none", "daily", "weekly", "monthly"],
                "description": "繰り返しタイプ（なし、毎日、毎週、毎月）",
            },
            "repeat_value": {
                "type": "string",
                "description": "繰り返しの詳細（毎週なら曜日、毎月なら日付など）",
            },
        },
        "required": ["content", "datetime"],
    },
}


def get_system_prompt(current_time: datetime) -> str:
    """システムプロンプトを生成"""
    return f"""あなたはリマインダーアシスタントです。ユーザーの入力から、リマインダーの内容と日時を解析してください。

現在の日時: {current_time.strftime('%Y年%m月%d日 %H:%M')}（{current_time.strftime('%A')}）

## 日時解析ルール

### 相対時間
- 「10分後」「3時間後」→ 現在時刻に加算
- 「明日」→ 翌日の同時刻（時刻指定がなければ09:00）
- 「明後日」→ 2日後
- 「来週」→ 7日後
- 「来月」→ 1ヶ月後

### 曖昧な表現（以下のデフォルト時刻を使用）
- 「朝」→ 08:00
- 「昼」「昼頃」→ 12:00
- 「午後」→ 14:00
- 「夕方」→ 17:00
- 「夜」→ 20:00
- 「仕事終わり」「退勤後」→ 18:00

### 曜日指定
- 「今週の金曜」→ 今週の金曜日
- 「来週の月曜」→ 来週の月曜日
- 「次の水曜」→ 次に来る水曜日

### 繰り返し
- 「毎日9時」→ repeat_type="daily", repeat_value="09:00"
- 「毎週金曜18時」→ repeat_type="weekly", repeat_value="friday"
- 「毎月25日」→ repeat_type="monthly", repeat_value="25"
- 「毎朝」→ repeat_type="daily", repeat_value="08:00"

### 複雑な繰り返し（対応する）
- 「毎月第3火曜日」→ repeat_type="monthly", repeat_value="third_tuesday"
- 「平日毎朝」→ repeat_type="weekdays", repeat_value="08:00"
- 「隔週月曜」→ repeat_type="biweekly", repeat_value="monday"

## 出力ルール
- datetimeは必ずISO 8601形式（YYYY-MM-DDTHH:MM:SS）
- 時刻が曖昧な場合は上記デフォルトを使用
- 1メッセージに複数の予定が含まれる場合、最初の1つだけを処理
- 日時が解析できない場合はcontentのみを返す（datetimeは現在時刻+1時間）
"""


async def parse_reminder_input(user_input: str) -> dict | None:
    """
    ユーザー入力を解析してリマインダー情報を抽出

    Args:
        user_input: ユーザーからの自然言語入力

    Returns:
        {
            "content": str,       # リマインド内容
            "datetime": datetime, # 通知日時
            "repeat_type": str,   # 繰り返しタイプ (optional)
            "repeat_value": str,  # 繰り返し詳細 (optional)
        }
        または解析失敗時はNone
    """
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    try:
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            tools=[{"function_declarations": [CREATE_REMINDER_SCHEMA]}],
        )

        chat = model.start_chat()
        response = chat.send_message(
            f"{get_system_prompt(now)}\n\nユーザー入力: {user_input}"
        )

        # Function Calling結果を抽出
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    if fc.name == "create_reminder":
                        args = dict(fc.args)
                        logger.info(f"LLM解析結果: {args}")

                        # datetimeをパース
                        dt_str = args.get("datetime", "")
                        try:
                            remind_at = datetime.fromisoformat(dt_str)
                            if remind_at.tzinfo is None:
                                remind_at = remind_at.replace(tzinfo=tz)
                        except ValueError:
                            logger.warning(f"日時パース失敗: {dt_str}")
                            remind_at = (now + timedelta(hours=1)).replace(
                                minute=0, second=0, microsecond=0
                            )

                        result = {
                            "content": args.get("content", user_input),
                            "datetime": remind_at,
                        }

                        if args.get("repeat_type") and args["repeat_type"] != "none":
                            result["repeat_type"] = args["repeat_type"]
                            result["repeat_value"] = args.get("repeat_value")

                        return result

        # Function Callがない場合、テキストレスポンスからJSONを抽出を試みる
        if response.text:
            logger.warning(f"Function Callなし、テキスト応答: {response.text[:200]}")

        return None

    except Exception as e:
        logger.error(f"LLM解析エラー: {e}")
        return None
