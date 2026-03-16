# Vertretungsplan Discord Bot

Discord bot that monitors your WebUntis timetable and sends notifications when periods get cancelled, substituted, or moved to a different room.

## Features

- Polls WebUntis every N minutes (configurable)
- Detects three types of changes:
  - **Entfall** (cancelled period) — red embed
  - **Vertretung** (substitute teacher) — orange embed
  - **Raumwechsel** (room change) — blue embed
- Persists state across restarts (JSON file)
- Manual check via `!vplan` command
- Status overview via `!status` command

## Setup

### 1. Create a Discord Bot

1. Go to https://discord.com/developers/applications
2. Click "New Application", give it a name
3. Go to **Bot** → click "Reset Token" → copy the token
4. Under **Privileged Gateway Intents**, enable **Message Content Intent**
5. Go to **OAuth2** → **URL Generator**:
   - Scopes: `bot`
   - Permissions: `Send Messages`, `Embed Links`
6. Open the generated URL to invite the bot to your server
7. Copy the channel ID where notifications should go (right-click channel → Copy Channel ID; enable Developer Mode in Discord settings if you don't see this)

### 2. Find your WebUntis details

- **School name**: The value after `?school=` in your WebUntis URL
- **Server**: The hostname, e.g. `neilo.webuntis.com`
- **Class name**: Your class as shown in the timetable, e.g. `FI24`

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your actual values
```

### 4. Deploy with Docker

```bash
docker compose up -d
```

Check logs:

```bash
docker compose logs -f
```

### 5. Deploy without Docker (alternative)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
# export all vars:
set -a && source .env && set +a
python bot.py
```

## Commands

| Command   | Description                       |
|-----------|-----------------------------------|
| `!vplan`  | Trigger a manual timetable check  |
| `!status` | Show bot config and tracked state |

## Configuration

| Variable               | Required | Description                              |
|------------------------|----------|------------------------------------------|
| `DISCORD_TOKEN`        | yes      | Bot token from Discord Developer Portal  |
| `DISCORD_CHANNEL_ID`   | yes      | Channel ID for notifications             |
| `UNTIS_SCHOOL`         | yes      | School name in WebUntis                  |
| `UNTIS_SERVER`         | yes      | WebUntis server hostname                 |
| `UNTIS_USERNAME`       | yes      | Your WebUntis login                      |
| `UNTIS_PASSWORD`       | yes      | Your WebUntis password                   |
| `UNTIS_CLASS`          | yes      | Class name (e.g. `FI24`)                 |
| `POLL_INTERVAL_MINUTES`| no       | Poll interval in minutes (default: 5)    |
| `LOOKAHEAD_DAYS`       | no       | Days to look ahead (default: 3)          |
