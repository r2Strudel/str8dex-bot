# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
source venv/bin/activate && python -u bot.py
```

The `-u` flag is required — without it stdout buffering hides print output. To restart a running bot:

```bash
pkill -f "python bot.py"
source venv/bin/activate && python -u bot.py
```

## Setup (first time)

```bash
bash setup.sh
# then fill in .env values
source venv/bin/activate && python -u bot.py
```

## Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `POKEWALLET_KEY` | PokeWallet API key |
| `FORUM_CHANNEL_ID` | Discord forum channel ID where card posts are created |
| `SEARCH_CHANNEL_ID` | Text channel ID where the bot posts a persistent "Search Card 🔍" button |

`POKEPRICE_KEY` is in `.env` but unused — PokemonPriceTracker was removed due to rate limits.

## Architecture

Everything lives in `bot.py`. There are no modules or packages.

**Startup sequence** (`on_ready`):
1. `_clean_cache()` — removes expired cache entries from `cache.json`
2. `_load_set_languages()` — fetches all sets from PokeWallet `/sets` endpoint and builds `_set_languages: dict[set_code → "eng"/"jap"]`. This is the authoritative source for language filtering.
3. Guild-specific command sync — uses `bot.tree.copy_global_to(guild)` + `bot.tree.sync(guild=guild)` for instant propagation (global sync takes up to 1 hour).
4. `daily_cache_cleanup` task starts (24-hour loop).

**Cache** (`cache.json`):
- Keyed by `"{query}|{lang}"` (lowercase, stripped)
- Each entry stores `{"ts": unix_timestamp, "cards": [...]}` with up to 100 cards
- TTL is 5 days (`CACHE_TTL`). Number filtering is applied client-side from cached results — never triggers extra API calls.
- Cleaned on startup and every 24 hours.

**Language detection** (`_is_japanese`):
- Primary: looks up `card_info.set_code` in `_set_languages` dict loaded from the API
- Fallback (if set table not loaded): regex pattern matching on set codes

**`/card` command flow**:
1. Check cache for `(name, lang)` pair
2. If miss: call PokeWallet `/search` with `limit=100`, filter by `_lang_ok()` and `_name_matches()`
3. Save results to cache
4. Apply `number` filter client-side if provided
5. Return ephemeral `CardBrowserView`

**UI components**:
- `CardBrowserView` — ephemeral view holding all filtered cards and pagination state
- `CardDropdown` — `max_values=3`, option `value` is the absolute index into `all_cards` (survives pagination). Selecting cards shows a "X Card(s) Selected" confirmation embed — card images are NOT shown until the user presses Show Card.
- `ShowButton` — must be pressed to display card image embeds; disabled until a selection is made
- `PostButton` — when `forum_mode=True` (set when `FORUM_CHANNEL_ID` is configured), clicking it opens `ForumPostModal` instead of posting directly
- `ForumPostModal` — required text input; on submit, creates a new thread in the forum channel with `interaction.user.mention`, the user's message, and the card embed(s)
- `PersistentSearchView` / `SearchButton` — posted to `SEARCH_CHANNEL_ID` on startup; `timeout=None` so it survives restarts. Bot deletes old search button messages on startup and reposts at the bottom.

**Rate limit info** is read from PokeWallet response headers (`X-RateLimit-Remaining-Hour`, `X-RateLimit-Remaining-Day`) into the `_rate_info` global and shown in the search summary embed.

## Deployment (Oracle Cloud Free tier)

The `pokebot.service` systemd unit is configured for a VPS with `User=ubuntu` and paths under `/home/ubuntu/discord-pokemon-bot/`. The local dev machine runs as `jorge` — update the service file paths accordingly when deploying.
