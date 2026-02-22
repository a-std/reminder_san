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
- **LLM-powered parsing** — Uses Groq (LLaMA) to handle ambiguous or complex expressions
- **Recurring reminders** — Supports daily, weekly, and custom recurring schedules
- **Confirmation flow** — Confirms parsed time with the user before setting the reminder
- **Persistent storage** — Reminders survive bot restarts via SQLite
- **Timezone-aware** — All times handled in Asia/Tokyo (JST)

## 🏗 Architecture

```
reminder_san/
├── main.py              # Discord bot entry point
├── parser.py            # Natural language date/time parser
├── scheduler.py         # Reminder scheduling and dispatch
├── database.py          # SQLite persistence layer
├── llm.py               # Groq LLM integration for NLP
├── test_parser.py       # Unit tests for parser
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
2. **LLM fallback** — For complex or ambiguous expressions, delegates to Groq LLaMA
   - `今週中に`, `週明けまでに`, `夕方ごろ`, etc.

```python
# Example parser output
parse_reminder("明日の朝9時に朝会のリマインドして")
# → ReminderRequest(
#       message="朝会のリマインド",
#       trigger_at=datetime(2026, 2, 24, 9, 0, tzinfo=JST),
#       recurring=None
#   )
```

## 🗄 Database Schema

```sql
CREATE TABLE reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    message     TEXT NOT NULL,
    trigger_at  TEXT NOT NULL,       -- ISO 8601 in JST
    recurring   TEXT,                -- 'daily' | 'weekly' | NULL
    created_at  TEXT NOT NULL,
    fired       INTEGER DEFAULT 0
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
pytest test_parser.py -v
```

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `discord.py` | Discord bot framework |
| `groq` | LLM-powered NLP (LLaMA 3) |
| `python-dateutil` | Date parsing utilities |
| `apscheduler` | Reminder scheduling |
| `python-dotenv` | Environment variable management |

## 📄 License

MIT
