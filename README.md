# Reminder San (リマインダーさん)

自然言語でリマインダーを登録できる Discord Bot。

「明日18時に歯医者」のように話しかけるだけで、日時と内容を自動解析して登録します。

## 主な機能

- **自然言語パーサー** — 「来週の月曜14時にミーティング」「3日後の朝に薬を飲む」など、日本語の多様な日時表現を解析
- **パターンマッチ + LLMフォールバック** — 正規表現で高速解析し、複雑な表現はGroq LLMで補完
- **繰り返しリマインダー** — 毎日・毎週・毎月の繰り返し設定に対応
- **Discord UI** — ボタン・モーダルで編集・削除・一覧表示
- **スヌーズ** — 通知時にワンタップでスヌーズ

## 技術スタック

- **Python 3.13+**
- **discord.py** — Bot本体（Views / Modals）
- **aiosqlite** — SQLite 非同期アクセス（WALモード）
- **APScheduler** — 30秒間隔でリマインド通知チェック
- **Groq API** — 自然言語日時解析のLLMフォールバック

## セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env を編集（DISCORD_BOT_TOKEN, GROQ_API_KEY）
python main.py
```

## 使い方

Discord で Bot にメンションまたは DM:

```
明日18時に歯医者
来週の金曜15時にミーティング
毎週月曜9時に週報を書く
3時間後に洗濯物を取り込む
```

Bot が日時を解析して確認ボタンを表示します。「登録」を押せば完了。

## 環境変数

| 変数 | 必須 | 説明 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✅ | Discord Bot トークン |
| `GROQ_API_KEY` | ✅ | Groq API キー（LLM解析用） |
| `REMINDER_CHANNEL_ID` | | デフォルト通知チャンネル |

## ライセンス

MIT
