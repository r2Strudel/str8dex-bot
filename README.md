# Str8Dex — Pokemon TCG Discord Bot

A Discord slash-command bot that lets users search for Pokemon TCG card prices via the [PokeWallet](https://pokewallet.io) API and post cards to a Discord forum channel.

## Features

- `/card` slash command — search by name, filter by language (English/Japanese) and card number
- Browse results with pagination and select up to 3 cards at a time
- Post selected cards directly to a Discord forum channel
- 5-day result cache to minimize API calls
- Local web dashboard to manage config, start/stop the bot, and monitor cache

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/discord-pokemon-bot.git
cd discord-pokemon-bot
```

### 2. Run setup

```bash
bash setup.sh
```

This creates a Python virtual environment and installs dependencies.

### 3. Configure via the dashboard

Start the dashboard:

```bash
source venv/bin/activate
python dashboard.py
```

Open `http://localhost:5000` in your browser and fill in:

| Field | Where to get it |
|---|---|
| `DISCORD_TOKEN` | [Discord Developer Portal](https://discord.com/developers/applications) → Your App → Bot → Token |
| `POKEWALLET_KEY` | [PokeWallet](https://pokewallet.io) → API Keys |
| `FORUM_CHANNEL_ID` | Right-click your Discord forum channel → Copy Channel ID (requires Developer Mode) |

Click **Save**, then click **Start Bot**.

### 4. (Optional) Run without the dashboard

```bash
source venv/bin/activate && python -u bot.py
```

## Requirements

- Python 3.10+
- A Discord bot with `applications.commands` scope and `bot` scope invited to your server
- A PokeWallet API key (free tier available)

## Dashboard

Run `python dashboard.py` (venv activated) and open `http://localhost:5000`.

- **Bot Status** — start/stop the bot
- **Cache** — view and clear cached search results
- **Configuration** — update API keys and channel ID
- **Bot Log** — live tail of the last 20 log lines

See [dashboard.md](dashboard.md) for more details.

## Deployment (VPS)

A systemd service file (`pokebot.service`) is included. Edit the paths for your server user and copy it to `/etc/systemd/system/`, then:

```bash
sudo systemctl enable --now pokebot
```
