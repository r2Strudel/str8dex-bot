# Str8Dex Dashboard

Local web dashboard for managing the Str8Dex Discord bot. Runs at `http://localhost:5000`.

## Running the dashboard

```bash
cd ~/discord-pokemon-bot
source venv/bin/activate
python dashboard.py
```

Then open `http://localhost:5000` in your browser.

No extra dependencies — uses only Python's standard library (`http.server`).

## Sections

### Bot Status
Shows whether the bot process is running (with PID) or stopped.
- **Start Bot** — launches `bot.py` inside the venv, appends output to `bot.log`
- **Stop Bot** — sends SIGTERM to the running bot process

### Cache
Shows total, active, and expired cache entries from `cache.json`.
- Each row shows the search query, language flag, number of cards cached, and how long ago it was cached
- Entries older than 5 days are shown in red
- **Clear Cache** — wipes `cache.json` entirely (next search will hit the API)

### Configuration (.env)
Edit `DISCORD_TOKEN`, `POKEWALLET_KEY`, and `FORUM_CHANNEL_ID` directly in the browser.
- Token and key fields are masked (password input)
- **Save Config** — writes changes back to `.env`; restart the bot for changes to take effect

### Bot Log
Displays the last 20 lines of `bot.log`. Click **Refresh** to reload.

## Notes

- The dashboard binds to `127.0.0.1` only — not accessible from other machines
- Config changes require a bot restart to take effect
- `POKEPRICE_KEY` in `.env` is preserved but not shown (it's unused)
