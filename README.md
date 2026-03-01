# Reminder-san ⏰

A Discord bot that sets reminders using natural language — in Japanese.

[![CI](https://github.com/a-std/reminder_san/actions/workflows/ci.yml/badge.svg)](https://github.com/a-std/reminder_san/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python)](https://www.python.org)
[![discord.py](https://img.shields.io/badge/discord.py-2.x-5865F2?logo=discord)](https://discordpy.readthedocs.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 💬 Usage

Just mention the bot with a natural language reminder in Japanese:

```
@reminder-san 明日の朝9時に朝会のリマインドして
@reminder-san 30分後にご飯食べるのリマインドして
@reminder-san 毎週月曜の10時に週次レビューを思い出させて
```

The bot parses the date/time expression, confirms with the user, and sends a reminder at the specified time.

## ✨ Features

- **Natural language parsing** — Understands Japanese time expressions like `明日の朝9時`, `30分後`, `来週月曜`
- **LLM-powered parsing** — Uses Groq API with tool_call for ambiguous or complex expressions
- **Recurring reminders** — Supports daily, weekly, biweekly, weekdays, and monthly (Nth weekday) schedules
- **Confirmation flow** — Confirms parsed time with the user before setting the reminder
- **Persistent storage** — Reminders survive bot restarts via SQLite
- **Timezone-aware** — All times handled in Asia/Tokyo (JST)

## 🏗 Architecture

```
reminder_san/
├── main.py              # Entry point (logging, crash recovery)
├── bot.py               # Discord bot (message handling, Views/Modals)
├── config.py            # Environment variables and constants
├── database.py          # SQLite persistence (aiosqlite, WAL mode)
├── scheduler.py         # APScheduler reminder dispatch (30s interval)
├── llm_parser.py        # NL date/time parser (regex + Groq LLM fallback)
├── utils.py             # Shared utilities
├── health_server.py     # Health check endpoint
├── test_parser.py       # Parser tests
└── requirements.txt
```

## 🚀 Quick Start

### Prerequisites

- Python 3.13+
- Discord Bot Token ([create one here](https://discord.com/developers/applications))
- Groq API key (free tier available at [console.groq.com](https://console.groq.com))

### Installation

```bash
git clone https://github.com/a-std/reminder_san.git
cd reminder_san

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your tokens

python main.py
```

### Environment Variables

```env
DISCORD_BOT_TOKEN=your_discord_bot_token
GROQ_API_KEY=your_groq_api_key
TIMEZONE=Asia/Tokyo
```

### Discord Bot Permissions

Required permissions when inviting the bot:
- `Send Messages`
- `Read Message History`
- `Mention Everyone` (for reminder pings)

## 🧠 How Parsing Works

The parser uses a two-stage approach:

1. **Rule-based parsing** — Handles common, unambiguous patterns with regex
   - `n分後`, `n時間後`, `明日`, `来週月曜`, etc.
2. **LLM fallback** (`llm_parser.py`) — For complex or ambiguous expressions, delegates to Groq API using tool_call to return structured ISO 8601 dates
   - `今週中に`, `週明けまでに`, `夕方ごろ`, etc.
   - Fallback usage is logged to `logs/llm_fallback.log`

## 🗄 Database Schema

```sql
CREATE TABLE reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    guild_id     TEXT,
    channel_id   TEXT NOT NULL,
    content      TEXT NOT NULL,
    remind_at    DATETIME NOT NULL,
    repeat_type  TEXT,        -- daily/weekly/biweekly/weekdays/monthly/none
    repeat_value TEXT,        -- for monthly: day number or "第N曜日"
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_active    INTEGER DEFAULT 1
);
```

## 🔄 Running as a Service

### Windows (Task Scheduler + VBS)

```vbs
' start.vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw.exe C:\reminder_san\main.py", 0, False
```

### Linux (systemd)

```ini
[Unit]
Description=Reminder-san Discord Bot
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/reminder_san/main.py
EnvironmentFile=/opt/reminder_san/.env
Restart=always

[Install]
WantedBy=multi-user.target
```

## 🧪 Testing

```bash
# Custom test runner (not pytest-compatible)
python test_parser.py
```

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `discord.py` | Discord bot framework |
| `openai` | Groq LLM API (OpenAI-compatible) |
| `aiosqlite` | Async SQLite access (WAL mode) |
| `APScheduler` | Reminder scheduling |
| `python-dotenv` | Environment variable management |
| `pytz` | Timezone handling |

## 📄 License

MIT
