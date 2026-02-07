# リマインダー Discord Bot

## プロジェクト概要

自然言語でリマインダーを登録できるDiscord Bot。
「明日18時に歯医者」のように話しかけると、日時とリマインド内容を解析して登録する。

## 技術スタック

- Python 3.13.5 / Windows (GMKtec NucBoxG3 Plus, Intel N150, 16GB RAM)
- discord.py: Discord Bot（Views/Modals対応）
- aiosqlite: SQLite非同期アクセス（WALモード）
- APScheduler: リマインダー通知スケジューラ（30秒間隔チェック）
- Groq API (OpenAI互換): 自然言語日時解析のLLMフォールバック
- python-dotenv: 環境変数管理

## プロジェクト構造

```
C:\reminder_san\
├── main.py              # エントリーポイント（ログ設定、クラッシュ時自動再起動）
├── config.py            # 環境変数・定数管理（dotenv）
├── bot.py               # Discord Bot本体（メッセージ処理・UI View/Modal）
├── database.py          # SQLite CRUD（aiosqlite、共有接続）
├── scheduler.py         # APSchedulerでリマインド通知管理
├── llm_parser.py        # 日時解析（パターンマッチ優先 + LLMフォールバック）
├── utils.py             # 共通ユーティリティ（曜日・繰り返しラベル等）
├── test_parser.py       # パーサーテスト
├── reminders.db         # SQLiteデータベース
├── requirements.txt     # 依存関係
├── .env                 # APIキー（DISCORD_BOT_TOKEN, GROQ_API_KEY等）
├── .env.example         # APIキーテンプレート
├── run.bat              # 起動スクリプト（手動起動用）
├── run_hidden.vbs       # pythonw.exe非表示起動ラッパー（タスクスケジューラ用）
├── watchdog.ps1         # Watchdog（bot.pidのPIDで生存判定、5分間隔）
├── watchdog.vbs         # watchdog.ps1の非表示実行ラッパー
├── install_task.bat     # タスクスケジューラ登録
└── logs/                # ログファイル
```

## 主要コマンド

```bash
# Bot起動（フォアグラウンド・デバッグ用）
python main.py

# テスト実行（独自ランナー。pytestではcollect 0 itemsになる）
python test_parser.py

# Bot再起動（Claude Code から実行する手順）
python -c "
import psutil, os, signal, subprocess
# 1. 現在のreminder_sanプロセスを特定してkill
for p in psutil.process_iter(['pid', 'name']):
    if p.info['name'] and 'pythonw' in p.info['name'].lower():
        try:
            if p.cwd() == r'C:\reminder_san':
                os.kill(p.info['pid'], signal.SIGTERM)
                print(f'Killed PID={p.info[\"pid\"]}')
        except: pass
# 2. 新プロセス起動 + bot.pid書き込み
proc = subprocess.Popen(['pythonw', 'main.py'], cwd=r'C:\reminder_san', creationflags=0x00000008)
open(r'C:\reminder_san\bot.pid', 'w').write(str(proc.pid))
print(f'Started PID={proc.pid}')
"
```

## アーキテクチャ

### 処理フロー
1. ユーザーが専用チャンネルにメッセージ送信（例: 「明日18時に歯医者」）
2. `llm_parser.parse_reminder_input()` で解析:
   - まずパターンマッチ（正規表現）で日時・内容・繰り返しを抽出
   - パターンマッチ失敗時 → Groq API（LLMフォールバック）で解析
3. 確認画面（ConfirmReminderView）を表示:
   - 登録 / 日時変更 / キャンセル ボタン
4. 登録 → SQLiteに保存
5. APSchedulerが30秒間隔でDB検索 → 期限到来のリマインダーを通知
6. 通知時にスヌーズボタン付きEmbed送信

### 日時パーサー（llm_parser.py）
- **パターンマッチ優先**: 全角→半角変換、HH:MM→X時Y分正規化後に正規表現で解析
- **対応表現**: 明日/明後日/来週/N日後/N時間後/X月Y日/毎日/毎週X曜/毎月N日/毎月第N X曜日/隔週/平日 等
- **LLMフォールバック**: パターンマッチ失敗時のみGroq APIで解析（tool_call方式でISO8601を返す）
- **フォールバック記録**: `logs/llm_fallback.log` にLLM解析した入力を記録

### 繰り返し対応
| repeat_type | 説明 | repeat_value例 |
|---|---|---|
| daily | 毎日 | - |
| weekly | 毎週 | - |
| biweekly | 隔週 | - |
| weekdays | 平日 | - |
| monthly | 毎月 | `15`（日）/ `第1,3金` / `第2火の前日` |

### UI構成（discord.py Views/Modals）
- **ConfirmReminderView**: 登録確認（登録/日時変更/キャンセル）
- **DateTimeModal**: 日時変更モーダル
- **ReminderListView**: 一覧表示（セレクトメニューで選択→ReminderActionViewへ遷移）
- **ReminderActionView**: 個別操作（削除/タイトル変更/時刻変更、ephemeral表示）
- **SnoozeView**: スヌーズボタン（通知時に表示）
- **EditContentModal / EditTimeModal**: 編集モーダル

### 特殊コマンド（専用チャンネル内テキスト）
- 「一覧」「リスト」「確認」→ リマインダー一覧を表示

## 設定（config.py）

```python
DISCORD_BOT_TOKEN          # Discord Bot Token（必須）
GROQ_API_KEY               # Groq API Key（LLMフォールバック用、オプション）
REMINDER_CHANNEL_ID        # 専用チャンネルID（オプション）
SCHEDULER_CHECK_INTERVAL_SEC = 30  # リマインダーチェック間隔（秒）
DB_PATH = "reminders.db"   # SQLiteデータベースパス
TIMEZONE = "Asia/Tokyo"    # タイムゾーン
```

## データベース（SQLite）

```sql
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    guild_id TEXT,
    channel_id TEXT NOT NULL,
    content TEXT NOT NULL,
    remind_at DATETIME NOT NULL,
    repeat_type TEXT,       -- daily/weekly/monthly/biweekly/weekdays/none
    repeat_value TEXT,      -- 毎月の場合: 日数 or 第N曜日
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1
);
```

## 運用（タスクスケジューラ + Watchdog）

### 自動起動
- `ReminderBot`: PC起動時に `run_hidden.vbs` → `pythonw.exe main.py` を実行
- `ReminderBotWatchdog`: 5分間隔で `watchdog.vbs` → `watchdog.ps1` を実行

### Watchdog仕組み（watchdog.ps1）
1. `bot.pid` ファイルからPIDを読み取り（watchdog自身が `-PassThru` で書出し）
2. `Get-Process -Id $botPid` でプロセス生存を確認
3. 生存していれば何もしない、死んでいれば再起動してPIDを `bot.pid` に記録

### 注意事項
- `pythonw.exe` のWMI CommandLineは常に空になる（Windows制限）ため、プロセス検出にWMIは使えない
- watchdog.ps1は **UTF-8 BOM + CRLF** で保存すること（PowerShellのパースエラー防止）
- main.pyにクラッシュ時自動再起動あり（5分以内に5回連続クラッシュで停止）

## 設計判断・注意点

- **時刻一致判定**: `result < now`（`<=`ではない）。ちょうどの時刻は「今日」扱い
- **DB共有接続**: `asyncio.Lock` で `_get_db()` / `close_db()` の競合状態を防止
- **LLM API**: 同期クライアント（openai SDK）を `run_in_executor` + `asyncio.wait_for(timeout=15)` で非同期化
- **同時通知制限**: `asyncio.Semaphore(3)` でDiscord API rate limit対策
- **二重通知防止**: `handle_after_send` でDB更新失敗時は `deactivate_reminder` にフォールバック
- **Viewタイムアウト**: 180秒 + `on_timeout` でボタン自動無効化
- **起動時即時チェック**: Bot停止中に期限を迎えたリマインダーを起動直後に通知

## コーディング規約

- 日本語のログメッセージ・コメント
- 非同期処理（discord.py / aiosqlite / APScheduler）
- パターンマッチ優先、LLMフォールバックは最小限
- `from config import ...` で設定参照
