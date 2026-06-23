import re
import json
import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
POKEWALLET_KEY   = os.getenv("POKEWALLET_KEY")
POKEWALLET_BASE  = "https://api.pokewallet.io"
FORUM_CHANNEL_ID  = int(os.getenv("FORUM_CHANNEL_ID",  "0"))
SEARCH_CHANNEL_ID = int(os.getenv("SEARCH_CHANNEL_ID", "0"))

CACHE_FILE = Path(__file__).parent / "cache.json"
CACHE_TTL  = 5 * 24 * 60 * 60   # 5 days in seconds
PER_PAGE   = 25

RARITY_COLORS = {
    "common":                      0xA0A0A0,
    "uncommon":                    0x4CAF50,
    "rare":                        0x2196F3,
    "holo rare":                   0xFFD700,
    "rare holo":                   0xFFD700,
    "ultra rare":                  0x9C27B0,
    "rare ultra":                  0x9C27B0,
    "secret rare":                 0xFF4500,
    "rare secret":                 0xFF4500,
    "promo":                       0xFF69B4,
    "illustration rare":           0x00BCD4,
    "special illustration rare":   0xE91E63,
}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Loaded once at startup: set_code -> "eng" or "jap"
_set_languages: dict = {}

# Last known rate limit info (updated every time we hit the API)
_rate_info: dict = {}


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}

def _save_cache(cache: dict):
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass

def _cache_key(query: str, lang: str) -> str:
    return f"{query.strip().lower()}|{lang}"

def _get_cached(query: str, lang: str):
    """Return (cards, age_seconds) if a fresh cache entry exists, else (None, None)."""
    cache = _load_cache()
    entry = cache.get(_cache_key(query, lang))
    if entry:
        age = time.time() - entry.get("ts", 0)
        if age < CACHE_TTL:
            return entry.get("cards", []), age
    return None, None

def _set_cache(query: str, lang: str, cards: list):
    cache = _load_cache()
    cache[_cache_key(query, lang)] = {"ts": time.time(), "cards": cards}
    _save_cache(cache)

def _clean_cache():
    """Delete entries older than CACHE_TTL. Called once on startup."""
    cache = _load_cache()
    now     = time.time()
    before  = len(cache)
    cache   = {k: v for k, v in cache.items() if now - v.get("ts", 0) < CACHE_TTL}
    removed = before - len(cache)
    if removed:
        _save_cache(cache)
    print(f"Cache cleanup: removed {removed} expired entry/entries, {len(cache)} remaining.", flush=True)

def _fmt_age(seconds: float) -> str:
    days  = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    if days:
        return f"{days}d {hours}h ago"
    mins = int(seconds // 60)
    return f"{hours}h {mins}m ago" if hours else f"{mins}m ago"


# ── Filtering ─────────────────────────────────────────────────────────────────

def _is_japanese(card: dict) -> bool:
    code = (card.get("card_info") or {}).get("set_code", "")

    # Primary: use the set language table loaded from the API at startup
    if _set_languages and code:
        lang = _set_languages.get(code)
        if lang == "jap":
            return True
        if lang == "eng":
            return False

    # Fallback (in case set table isn't loaded yet): pattern matching
    if not code:
        return False
    return bool(re.match(
        r"^[a-z]|^S\d|^SM\d|^M\d|^XY-P|^BW-P|^DP-P|^BWP$|^SMP$|^SVP$",
        code
    ))

def _lang_ok(card: dict, lang: str) -> bool:
    return _is_japanese(card) if lang == "japanese" else not _is_japanese(card)

def _name_matches(card: dict, query: str) -> bool:
    name  = (card.get("card_info") or {}).get("name", "")
    clean = re.sub(r"\(.*?\)", "", name)
    clean = re.sub(r"\s*-\s*\d+/\d+.*$", "", clean).strip().lower()
    q = query.strip().lower()
    if q in clean:
        return True
    # Word-by-word fallback — single letters (X, V, M) are skipped so
    # "Charizard X ex" still matches a card named "Charizard ex"
    words = [w for w in q.split() if len(w) > 1]
    return bool(words) and all(w in clean for w in words)

def _number_matches(card: dict, number: str) -> bool:
    card_num = (card.get("card_info") or {}).get("card_number") or ""
    if not card_num:
        return False
    def norm(s):
        return re.sub(r"(?<![A-Za-z])0+(\d)", r"\1", s.strip().lower())
    return norm(number) in norm(card_num)


# ── Embed builder ─────────────────────────────────────────────────────────────

def make_embed(card: dict) -> discord.Embed:
    info     = card.get("card_info") or {}
    name     = info.get("name") or "Unknown"
    set_name = info.get("set_name") or info.get("set_code") or "Unknown"
    card_num = info.get("card_number") or ""
    rarity   = info.get("rarity") or ""
    color    = RARITY_COLORS.get((rarity or "").lower(), 0xFFCB05)

    tcg_url = (card.get("tcgplayer") or {}).get("url") or None
    embed   = discord.Embed(title=name, color=color, url=tcg_url)

    parts = [f"**{set_name}**"]
    if card_num:
        parts.append(f"`{card_num}`")
    if rarity:
        parts.append(rarity)
    embed.description = "  •  ".join(parts)

    embed.add_field(name="💰 Price", value=_price_str(card), inline=False)
    embed.set_footer(text="pokewallet.io")

    product_id = (tcg_url or "").rstrip("/").split("/")[-1] if tcg_url else ""
    if product_id.isdigit():
        embed.set_image(url=f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_400x400.jpg")

    return embed

def _price_str(item: dict) -> str:
    tcg    = item.get("tcgplayer") or {}
    prices = tcg.get("prices") or []
    if isinstance(prices, list) and prices:
        lines = []
        for p in prices[:4]:
            val = p.get("market_price") or p.get("mid_price")
            sub = (p.get("sub_type_name") or "").strip()
            if val:
                label = sub if sub and sub.lower() != "normal" else "Market"
                lines.append(f"{label}: **${val:,.2f}**")
        if lines:
            return "\n".join(lines)
    cm  = item.get("cardmarket") or {}
    cmp = cm.get("prices") or {}
    if isinstance(cmp, dict):
        val = cmp.get("averageSellPrice") or cmp.get("trendPrice")
        if val:
            return f"Cardmarket: **€{val:,.2f}**"
    return "N/A"


# ── UI ────────────────────────────────────────────────────────────────────────

class CardBrowserView(discord.ui.View):
    def __init__(self, all_cards: list, query: str, number: str = None,
                 lang: str = "english", from_cache: bool = False, cache_age: float = 0):
        super().__init__(timeout=180)
        self.all_cards      = all_cards
        self.query          = query
        self.number         = number
        self.lang           = lang
        self.from_cache     = from_cache
        self.cache_age      = cache_age
        self.page           = 0
        self.selected_cards = []
        self.post_btn       = PostButton(bool(FORUM_CHANNEL_ID))
        self.show_btn       = ShowButton()
        self._rebuild()

    @property
    def total_pages(self):
        return max(1, (len(self.all_cards) + PER_PAGE - 1) // PER_PAGE)

    def _page_slice(self):
        s = self.page * PER_PAGE
        return self.all_cards[s:s + PER_PAGE], s

    def _rebuild(self):
        self.clear_items()
        page_cards, offset = self._page_slice()
        self.add_item(CardDropdown(page_cards, offset))
        if self.page > 0:
            self.add_item(PrevButton())
        if self.page < self.total_pages - 1:
            self.add_item(NextButton())
        self.add_item(self.show_btn)
        self.add_item(self.post_btn)

    def _summary_embed(self) -> discord.Embed:
        s = self.page * PER_PAGE + 1
        e = min((self.page + 1) * PER_PAGE, len(self.all_cards))

        title = f'Search: "{self.query}"'
        if self.number:
            title += f"  #{self.number}"

        # Cache / API status line
        if self.from_cache:
            source_line = f"📦 **Cached** ({_fmt_age(self.cache_age)} — refreshes after 5 days)"
        else:
            rem_hour = _rate_info.get("remaining_hour", "?")
            rem_day  = _rate_info.get("remaining_day", "?")
            source_line = f"🔄 **Live from API** • {rem_hour} calls left this hour  •  {rem_day} left today"

        lang_label = "🇯🇵 Japanese" if self.lang == "japanese" else "🇺🇸 English"

        desc = (
            f"{source_line}\n"
            f"{lang_label}  •  **{len(self.all_cards)}** card(s) found  •  "
            f"page {self.page + 1}/{self.total_pages} (showing {s}–{e})\n\n"
            "Pick **up to 3 cards** from the dropdown, then hit **Show Card 🖼️** to preview.\n"
            + ("Hit **Post to Forum 📋** to create a new forum post with your selection." if FORUM_CHANNEL_ID
               else "Hit **Post to Chat 📢** to share your selection.")
        )
        return discord.Embed(title=title, description=desc, color=0xFFCB05)

    async def go_page(self, interaction: discord.Interaction, delta: int):
        self.page += delta
        self._rebuild()
        await interaction.response.edit_message(embed=self._summary_embed(), view=self)

    def _selected_embed(self) -> discord.Embed:
        count = len(self.selected_cards)
        names = [(c.get("card_info") or {}).get("name") or "Card" for c in self.selected_cards]
        desc  = "\n".join(f"• {n}" for n in names)
        desc += "\n\nPress **Show Card 🖼️** to preview."
        return discord.Embed(
            title=f"{count} Card{'s' if count > 1 else ''} Selected",
            description=desc,
            color=0xFFCB05,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class CardDropdown(discord.ui.Select):
    def __init__(self, page_cards: list, offset: int):
        options = []
        for i, card in enumerate(page_cards):
            info  = card.get("card_info") or {}
            label = (info.get("name") or "Unknown")[:100]
            desc  = (info.get("set_name") or info.get("set_code") or "")[:100]
            options.append(discord.SelectOption(label=label, description=desc, value=str(offset + i)))

        end = offset + len(page_cards)
        super().__init__(
            placeholder=f"Cards {offset + 1}–{end} — pick up to 3…",
            options=options,
            min_values=1,
            max_values=min(3, len(options)),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = [self.view.all_cards[int(v)] for v in self.values]
        self.view.selected_cards    = selected
        count                       = len(selected)
        icon = "📋" if self.view.post_btn.forum_mode else "📢"
        self.view.post_btn.disabled = False
        self.view.post_btn.label    = f"Post {count} Card{'s' if count > 1 else ''} {icon}"
        self.view.show_btn.disabled = False
        self.view.show_btn.label    = f"Show {'Cards' if count > 1 else 'Card'} 🖼️"
        await interaction.response.edit_message(
            embeds=[self.view._selected_embed()], view=self.view
        )


class PrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="◀ Prev", style=discord.ButtonStyle.secondary, row=1)
    async def callback(self, interaction):
        await self.view.go_page(interaction, -1)


class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Next ▶", style=discord.ButtonStyle.secondary, row=1)
    async def callback(self, interaction):
        await self.view.go_page(interaction, +1)


class ShowButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Show Card 🖼️", style=discord.ButtonStyle.secondary, disabled=True, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not self.view.selected_cards:
            await interaction.response.defer()
            return
        await interaction.response.edit_message(
            embeds=[make_embed(c) for c in self.view.selected_cards], view=self.view
        )


class AddPhotosButton(discord.ui.Button):
    def __init__(self, thread: discord.Thread):
        super().__init__(label="Add Trade Photos 📷", style=discord.ButtonStyle.secondary, row=2)
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        await self.thread.send(
            f"{interaction.user.mention} — reply here with photos of cards you have to trade! 📷"
        )
        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
        await interaction.followup.send(
            f"Prompt posted! Jump to the thread: {self.thread.jump_url}",
            ephemeral=True,
        )


class ForumPostModal(discord.ui.Modal, title="Post to Forum"):
    message = discord.ui.TextInput(
        label="What do you want to say?",
        placeholder="e.g. Looking for this card in near mint condition...",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, card_view: "CardBrowserView"):
        super().__init__()
        self.card_view = card_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()  # close modal immediately; gives time for slow API call

        embeds      = [make_embed(c) for c in self.card_view.selected_cards]
        names       = [(c.get("card_info") or {}).get("name") or "Card" for c in self.card_view.selected_cards]
        thread_name = " / ".join(names)[:100]
        user_msg    = self.message.value.strip()

        content = interaction.user.mention
        if user_msg:
            content += f"\n{user_msg}"

        posted = False
        thread = None
        if FORUM_CHANNEL_ID and interaction.guild:
            forum = interaction.guild.get_channel(FORUM_CHANNEL_ID)
            if isinstance(forum, discord.ForumChannel):
                thread = (await forum.create_thread(name=thread_name, content=content, embeds=embeds)).thread
                posted = True

        if not posted and interaction.channel:
            await interaction.channel.send(content=content, embeds=embeds)

        count = len(embeds)
        self.card_view.post_btn.disabled = True
        self.card_view.post_btn.label    = f"✅ Posted {count} Card{'s' if count > 1 else ''}!"
        if thread:
            self.card_view.add_item(AddPhotosButton(thread))
        await interaction.edit_original_response(view=self.card_view)

        async def _auto_delete():
            await asyncio.sleep(8)
            try:
                await interaction.delete_original_response()
            except Exception:
                pass

        asyncio.create_task(_auto_delete())


class PostButton(discord.ui.Button):
    def __init__(self, forum_mode: bool = False):
        super().__init__(label="Select a card first", style=discord.ButtonStyle.green, disabled=True, row=1)
        self.forum_mode = forum_mode

    async def callback(self, interaction: discord.Interaction):
        if not self.view.selected_cards:
            await interaction.response.defer()
            return

        if self.forum_mode:
            await interaction.response.send_modal(ForumPostModal(self.view))
        else:
            await interaction.response.defer()
            embeds = [make_embed(c) for c in self.view.selected_cards]
            count  = len(embeds)
            if interaction.channel:
                await interaction.channel.send(embeds=embeds)
            self.disabled = True
            self.label    = f"✅ Posted {count} Card{'s' if count > 1 else ''}!"
            await interaction.edit_original_response(view=self.view)

            async def _auto_delete():
                await asyncio.sleep(5)
                try:
                    await interaction.delete_original_response()
                except Exception:
                    pass

            asyncio.create_task(_auto_delete())


# ── Persistent search button (always-at-bottom UX) ───────────────────────────

class SearchButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Search Card 🔍",
            style=discord.ButtonStyle.primary,
            custom_id="str8dex:search_card",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CardSearchModal())


class PersistentSearchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SearchButton())


class CardSearchModal(discord.ui.Modal, title="Search Pokemon Card"):
    card_name = discord.ui.TextInput(
        label="Pokemon Name",
        placeholder="e.g. Charizard, Pikachu",
        required=True,
        max_length=100,
    )
    language = discord.ui.TextInput(
        label="Language  (english / japanese)",
        placeholder="english",
        required=False,
        max_length=20,
    )
    number = discord.ui.TextInput(
        label="Card Number  (optional)",
        placeholder="e.g. 4,  025/102",
        required=False,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        lang_raw = (self.language.value or "").strip().lower()
        lang     = "japanese" if "jap" in lang_raw else "english"
        number   = self.number.value.strip() or None
        name     = self.card_name.value.strip()
        await _run_card_search(interaction, name, lang, number)


# ── Bot events ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})", flush=True)

    # Remove expired cache entries so the file doesn't grow forever
    _clean_cache()

    # Load set language table so Japanese filtering is accurate
    await _load_set_languages()

    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to {guild.name}", flush=True)
        except Exception as e:
            print(f"Sync failed for {guild.name}: {e}", flush=True)
    daily_cache_cleanup.start()
    await _post_search_button()
    print("Bot is ready.", flush=True)


@tasks.loop(hours=24)
async def daily_cache_cleanup():
    _clean_cache()


async def _load_set_languages():
    global _set_languages
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"X-API-Key": POKEWALLET_KEY}
            async with session.get(f"{POKEWALLET_BASE}/sets", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sets = data if isinstance(data, list) else data.get("sets") or data.get("data") or []
                    _set_languages = {
                        s["set_code"]: s.get("language", "")
                        for s in sets if s.get("set_code")
                    }
                    print(f"Loaded language data for {len(_set_languages)} sets.", flush=True)
    except Exception as e:
        print(f"Could not load set languages: {e}", flush=True)


async def _post_search_button():
    """Delete any old bot search button in SEARCH_CHANNEL_ID and repost at bottom."""
    if not SEARCH_CHANNEL_ID:
        return
    channel = bot.get_channel(SEARCH_CHANNEL_ID)
    if not channel:
        print("Search channel not found — set SEARCH_CHANNEL_ID in .env", flush=True)
        return

    async for msg in channel.history(limit=50):
        if msg.author == bot.user and msg.components:
            try:
                await msg.delete()
            except Exception:
                pass

    embed = discord.Embed(
        title="Pokemon Card Search",
        description=(
            "Click **Search Card 🔍** to look up a card's price.\n\n"
            "Results are private — only you see them.\n"
            "You can then post selected cards to the forum from the results."
        ),
        color=0xFFCB05,
    )
    embed.set_footer(text="Powered by PokeWallet  •  Str8Dex")
    await channel.send(embed=embed, view=PersistentSearchView())
    print(f"Search button posted in #{channel.name}", flush=True)


async def _run_card_search(interaction: discord.Interaction, name: str, lang: str, number: str | None):
    """Shared search logic used by both the modal and the /card command."""
    try:
        cached_cards, cache_age = _get_cached(name, lang)
        from_cache = cached_cards is not None

        if from_cache:
            cards = cached_cards
        else:
            cards, err = await _fetch(name, lang)
            if err:
                await interaction.followup.send(embed=err, ephemeral=True)
                return
            _set_cache(name, lang, cards)

        if number:
            cards = [c for c in cards if _number_matches(c, number)]

        if not cards:
            lang_label = "Japanese" if lang == "japanese" else "English"
            desc = f'No {lang_label} cards found for **{name}**'
            if number:
                desc += f' with number **{number}**'
            await interaction.followup.send(
                embed=discord.Embed(title="No Results", description=desc + ".", color=discord.Color.orange()),
                ephemeral=True,
            )
            return

        view = CardBrowserView(cards, name, number, lang, from_cache, cache_age or 0)

        if len(cards) == 1:
            view.selected_cards    = [cards[0]]
            view.post_btn.disabled = False
            icon = "📋" if FORUM_CHANNEL_ID else "📢"
            view.post_btn.label    = f"Post 1 Card {icon}"
            view.show_btn.disabled = False
            view.show_btn.label    = "Show Card 🖼️"
            await interaction.followup.send(embed=view._selected_embed(), view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=view._summary_embed(), view=view, ephemeral=True)

    except Exception as exc:
        await interaction.followup.send(
            embed=discord.Embed(title="Error", description=str(exc), color=discord.Color.red()),
            ephemeral=True,
        )


# ── Slash command ─────────────────────────────────────────────────────────────

@bot.tree.command(name="card", description="Look up Pokemon card prices on PokeWallet")
@app_commands.describe(
    name="Pokemon name (e.g. Charizard, Pikachu)",
    language="Card language — defaults to English",
    number="Card number to narrow results (e.g. 4, 025/102) — optional",
)
@app_commands.choices(language=[
    app_commands.Choice(name="English",  value="english"),
    app_commands.Choice(name="Japanese", value="japanese"),
])
async def card_cmd(
    interaction: discord.Interaction,
    name: str,
    language: app_commands.Choice[str] = None,
    number: str = None,
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    lang = language.value if language else "english"
    await _run_card_search(interaction, name, lang, number)


# ── API fetch ─────────────────────────────────────────────────────────────────

async def _fetch(query: str, lang: str):
    global _rate_info
    async with aiohttp.ClientSession() as session:
        headers = {"X-API-Key": POKEWALLET_KEY}
        # Strip standalone single letters (X, V, M…) so "Charizard X ex"
        # hits the API as "Charizard ex" and actually returns results
        api_q = " ".join(w for w in query.split() if len(w) > 1) or query
        params  = {"q": api_q, "limit": 100}
        async with session.get(f"{POKEWALLET_BASE}/search", headers=headers, params=params) as resp:
            if resp.status == 429:
                return None, _err(
                    "Rate Limit — PokeWallet",
                    f"Hourly or daily search limit reached.\n"
                    f"Remaining this hour: **{_rate_info.get('remaining_hour', '?')}**\n"
                    f"Remaining today: **{_rate_info.get('remaining_day', '?')}**"
                )
            if resp.status == 401:
                return None, _err("Auth Error", "Invalid PokeWallet API key.")
            if resp.status != 200:
                return None, _err(f"API Error {resp.status}")

            # Store rate limit info from response headers
            _rate_info = {
                "remaining_hour": resp.headers.get("X-RateLimit-Remaining-Hour", "?"),
                "remaining_day":  resp.headers.get("X-RateLimit-Remaining-Day",  "?"),
                "limit_hour":     resp.headers.get("X-RateLimit-Limit-Hour", "?"),
                "limit_day":      resp.headers.get("X-RateLimit-Limit-Day",  "?"),
            }
            data = await resp.json()

    raw      = data.get("results") or data.get("data") or (data if isinstance(data, list) else [])
    filtered = [c for c in raw if _lang_ok(c, lang) and _name_matches(c, query)]
    return filtered, None


def _err(title: str, desc: str = "") -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=discord.Color.red())


bot.add_view(PersistentSearchView())
bot.run(DISCORD_TOKEN)
