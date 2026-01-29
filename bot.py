import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncpg
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://wynnextras.com"
DATABASE_URL = os.getenv("DATABASE_URL")

# Database connection pool
db_pool: asyncpg.Pool = None

RAID_TYPES = ["NOTG", "NOL", "TCC", "TNA"]
RAID_NAMES = {
    "NOTG": "Nest of the Grootslangs",
    "NOL": "Orphion's Nexus of Light",
    "TCC": "The Canyon Colossus",
    "TNA": "The Nameless Anomaly"
}

RAID_EMOJIS = {
    "NOTG": "<:notg:1466160820638584885>",
    "NOL": "<:nol:1466160862296543458>",
    "TCC": "<:tcc:1466160902037438627>",
    "TNA": "<:tna:1466160934937432409>",
}

# Raid emoji IDs for reactions
RAID_EMOJI_IDS = {
    "NOTG": 1466160820638584885,
    "NOL": 1466160862296543458,
    "TCC": 1466160902037438627,
    "TNA": 1466160934937432409,
}

RARITY_ORDER = {"Mythic": 0, "Fabled": 1, "Legendary": 2}

# Rarity embed colors
RARITY_COLORS = {
    "Mythic": 0x5C005C,     # Dark purple
    "Fabled": 0xFF5555,     # Red
    "Legendary": 0x55FFFF,  # Light blue/cyan
}

# Animated aspect emojis
ASPECT_EMOJIS = {
    "warrior": "<a:aspect_warrior:1466159515488489605>",
    "mage": "<a:aspect_mage:1466159736058806345>",
    "archer": "<a:aspect_archer:1466159282742497475>",
    "assassin": "<a:aspect_assassin:1466159697416421387>",
    "shaman": "<a:aspect_shaman:1466159561823227955>",
}

# Class emojis (static)
CLASS_EMOJIS = {
    "warrior": "<:class_warrior:1466120334850654250>",
    "mage": "<:class_mage:1466120277678227550>",
    "archer": "<:class_archer:1466120313270964316>",
    "assassin": "<:class_assassin:1466120777324560697>",
    "shaman": "<:class_shaman:1466120243767414936>",
}

# Class item emojis (for non-maxed aspects)
CLASS_ITEM_EMOJIS = {
    "warrior": "<:warrior_item:1466156214151942345>",
    "mage": "<:mage_item:1466156170522792149>",
    "archer": "<:archer_item:1466156130865647626>",
    "assassin": "<:assassin_item:1466156151111553277>",
    "shaman": "<:shaman_item:1466156194493104457>",
}

# Max amounts for each rarity
MAX_AMOUNTS = {"Mythic": 15, "Fabled": 75, "Legendary": 150}

# Valid current dungeons (API has old removed dungeons we don't want to show)
VALID_DUNGEONS = {
    # Normal dungeons
    "Decrepit Sewers",
    "Eldritch Outlook",
    "Fallen Factory",
    "Galleon's Graveyard",
    "Ice Barrows",
    "Infested Pit",
    "Lost Sanctuary",
    "Sand-Swept Tomb",
    "Timelost Sanctum",
    "Undergrowth Ruins",
    "Underworld Crypt",
    # Corrupted dungeons
    "Corrupted Decrepit Sewers",
    "Corrupted Galleon's Graveyard",
    "Corrupted Ice Barrows",
    "Corrupted Infested Pit",
    "Corrupted Lost Sanctuary",
    "Corrupted Sand-Swept Tomb",
    "Corrupted Undergrowth Ruins",
    "Corrupted Underworld Crypt",
}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# === User Linking (PostgreSQL) ===
async def init_db():
    """Initialize database connection pool and create tables."""
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS linked_users (
                    discord_id BIGINT PRIMARY KEY,
                    player_name TEXT NOT NULL
                )
            ''')
        print("Database connected and initialized!")
    else:
        print("WARNING: DATABASE_URL not set, user linking will not persist!")


async def get_linked_player(discord_id: int) -> str | None:
    if not db_pool:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT player_name FROM linked_users WHERE discord_id = $1',
            discord_id
        )
        return row['player_name'] if row else None


async def set_linked_player(discord_id: int, player_name: str):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO linked_users (discord_id, player_name)
            VALUES ($1, $2)
            ON CONFLICT (discord_id) DO UPDATE SET player_name = $2
        ''', discord_id, player_name)


async def remove_linked_player(discord_id: int) -> str | None:
    if not db_pool:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            'DELETE FROM linked_users WHERE discord_id = $1 RETURNING player_name',
            discord_id
        )
        return row['player_name'] if row else None


# === API Functions ===
async def fetch_gambits():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/gambit") as resp:
            return await resp.json() if resp.status == 200 else None


async def fetch_loot_pool(raid_type: str):
    import time
    now = time.time()

    # Check cache first
    if raid_type in _loot_pool_cache:
        cache_time = _loot_pool_cache_time.get(raid_type, 0)
        if (now - cache_time) < LOOT_POOL_CACHE_TTL:
            return _loot_pool_cache[raid_type]

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/raid/loot-pool?raidType={raid_type}") as resp:
            if resp.status == 200:
                data = await resp.json()
                _loot_pool_cache[raid_type] = data
                _loot_pool_cache_time[raid_type] = now
                return data
            return None


async def fetch_player_aspects(player_name: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/aspects/list") as resp:
            if resp.status != 200:
                return None
            players = await resp.json()

        player_uuid = None
        for player in players:
            if player.get("playerName", "").lower() == player_name.lower():
                player_uuid = player.get("playerUuid")
                break

        if not player_uuid:
            return None

        async with session.get(f"{BASE_URL}/aspects?playerUuid={player_uuid}") as resp:
            return await resp.json() if resp.status == 200 else None


async def fetch_player_uuid(player_name: str) -> str | None:
    """Get player UUID from Mojang API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.mojang.com/users/profiles/minecraft/{player_name}") as resp:
            if resp.status == 200:
                data = await resp.json()
                raw_uuid = data.get("id", "")
                # Format UUID with hyphens
                if len(raw_uuid) == 32:
                    return f"{raw_uuid[:8]}-{raw_uuid[8:12]}-{raw_uuid[12:16]}-{raw_uuid[16:20]}-{raw_uuid[20:]}"
            return None


# Cache for aspect class mapping (name -> class)
_aspect_class_cache: dict[str, str] = {}
_aspect_cache_time: float = 0
ASPECT_CACHE_TTL = 3600  # 1 hour

# Cache for loot pools (raid_type -> data)
_loot_pool_cache: dict[str, dict] = {}
_loot_pool_cache_time: dict[str, float] = {}
LOOT_POOL_CACHE_TTL = 300  # 5 minutes


async def get_aspect_class_mapping() -> dict[str, str]:
    """Fetch aspect -> class mapping from Wynncraft API, with caching."""
    global _aspect_class_cache, _aspect_cache_time

    import time
    now = time.time()

    # Return cached data if still valid
    if _aspect_class_cache and (now - _aspect_cache_time) < ASPECT_CACHE_TTL:
        return _aspect_class_cache

    mapping = {}
    classes = ["warrior", "mage", "archer", "assassin", "shaman"]

    async with aiohttp.ClientSession() as session:
        for class_name in classes:
            try:
                async with session.get(f"https://api.wynncraft.com/v3/aspects/{class_name}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for aspect_name, aspect_data in data.items():
                            mapping[aspect_name] = class_name
            except Exception as e:
                print(f"Error fetching aspects for {class_name}: {e}")

    if mapping:
        _aspect_class_cache = mapping
        _aspect_cache_time = now

    return mapping


def get_aspect_class(aspect_name: str, class_mapping: dict[str, str]) -> str | None:
    """Get class for an aspect from the mapping."""
    return class_mapping.get(aspect_name)


async def fetch_aspects_by_uuid(uuid: str):
    """Fetch player aspects from WynnExtras API using UUID."""
    # Remove hyphens from UUID for API call
    clean_uuid = uuid.replace("-", "")
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/aspects?playerUuid={clean_uuid}") as resp:
            return await resp.json() if resp.status == 200 else None


async def fetch_wynncraft_player(uuid: str) -> dict | None:
    """Fetch full player data from Wynncraft API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.wynncraft.com/v3/player/{uuid}?fullResult") as resp:
            if resp.status == 200:
                return await resp.json()
            return None


# Rank colors/badges
RANK_COLORS = {
    "administrator": 0xFF5555,
    "moderator": 0xFFAA00,
    "champion": 0x00AAAA,
    "hero": 0xAA00AA,
    "vipplus": 0x55FFFF,
    "vip": 0x55FF55,
}

RANK_DISPLAY = {
    "administrator": "Admin",
    "moderator": "Mod",
    "champion": "Champion",
    "hero": "Hero",
    "vipplus": "VIP+",
    "vip": "VIP",
}

CLASS_EMOJIS_PV = {
    "WARRIOR": "<:class_warrior:1466120334850654250>",
    "MAGE": "<:class_mage:1466120277678227550>",
    "ARCHER": "<:class_archer:1466120313270964316>",
    "ASSASSIN": "<:class_assassin:1466120777324560697>",
    "SHAMAN": "<:class_shaman:1466120243767414936>",
}


# === Score Calculation ===
# New formula from WynnExtras mod
GLOBAL_EXPONENT = 1.08
NORMALIZATION_FACTOR = 0.92

# Drop probability per rarity
DROP_PROBS = {
    "mythic": 0.08,
    "fabled": 0.46,
    "legendary": 0.46,
}

# Weight per rarity
RARITY_WEIGHTS = {
    "mythic": 1.25,
    "fabled": 1.00,
    "legendary": 0.95,
}

# Raid-specific multipliers
RAID_MULTIPLIERS = {
    "TCC": 0.85,
    "TNA": 1.00,
    "NOTG": 1.22,
    "NOL": 1.30,
}


def get_remaining_to_max(rarity: str, amount: int) -> int:
    """Get how many more aspects needed to max."""
    max_amt = MAX_AMOUNTS.get(rarity, 999)
    return max(0, max_amt - amount)


def calculate_aspect_score(rarity: str, amount: int) -> float:
    """Calculate score contribution for a single aspect using new formula."""
    rarity_lower = rarity.lower()
    max_amt = MAX_AMOUNTS.get(rarity, 999)

    if amount >= max_amt:
        return 0.0  # Already maxed, no score

    total_remaining = max_amt - amount
    drop_prob = DROP_PROBS.get(rarity_lower, 0.46)
    weight = RARITY_WEIGHTS.get(rarity_lower, 1.0)

    # contribution = (totalRemaining / dropProb * weight) ^ GLOBAL_EXPONENT
    expected_pulls = total_remaining / drop_prob
    weighted = expected_pulls * weight
    return weighted ** GLOBAL_EXPONENT


def calculate_pool_score(pool_aspects: list, player_aspects: dict, raid_type: str = None) -> float:
    """Calculate total score for a loot pool based on player progress."""
    total_score = 0.0

    for aspect in pool_aspects:
        name = aspect.get("name", "")
        rarity = aspect.get("rarity", "")

        # Get player's current amount for this aspect
        player_amount = player_aspects.get(name, 0)

        # Add score contribution
        total_score += calculate_aspect_score(rarity, player_amount)

    # Apply raid multiplier and normalization factor
    raid_multiplier = RAID_MULTIPLIERS.get(raid_type, 1.0) if raid_type else 1.0
    return total_score * raid_multiplier * NORMALIZATION_FACTOR


def sort_aspects_by_rarity(aspects: list) -> list:
    return sorted(aspects, key=lambda a: RARITY_ORDER.get(a.get("rarity", ""), 99))


def get_aspect_emoji(required_class: str | None) -> str:
    """Get the appropriate aspect emoji based on required class."""
    if required_class and required_class.lower() in ASPECT_EMOJIS:
        return ASPECT_EMOJIS[required_class.lower()]
    # Default to warrior for aspects without class requirement
    return ASPECT_EMOJIS["warrior"]


def get_weekly_reset_times() -> tuple[int, int]:
    """Get Unix timestamps for last Friday 19:00 CET and next Friday 19:00 CET."""
    # CET is UTC+1, CEST is UTC+2. Use UTC+1 for simplicity (winter time)
    cet = timezone(timedelta(hours=1))
    now = datetime.now(cet)

    # Find last Friday
    days_since_friday = (now.weekday() - 4) % 7
    if days_since_friday == 0 and now.hour < 19:
        days_since_friday = 7  # It's Friday but before 19:00, use last week

    last_friday = now - timedelta(days=days_since_friday)
    last_friday = last_friday.replace(hour=19, minute=0, second=0, microsecond=0)

    # Next Friday is 7 days after last Friday
    next_friday = last_friday + timedelta(days=7)

    return int(last_friday.timestamp()), int(next_friday.timestamp())


async def fetch_all_mythics() -> list[dict]:
    """Fetch mythic aspects from all raids (parallel)."""
    import asyncio

    # Fetch all raids in parallel
    results = await asyncio.gather(*[fetch_loot_pool(raid_type) for raid_type in RAID_TYPES])

    all_mythics = []
    for raid_type, data in zip(RAID_TYPES, results):
        if data:
            for aspect in data.get("aspects", []):
                if aspect.get("rarity") == "Mythic":
                    aspect_copy = aspect.copy()
                    aspect_copy["raid"] = raid_type
                    all_mythics.append(aspect_copy)
    return all_mythics


# === Bot Events ===
@bot.event
async def on_ready():
    # Initialize database connection
    await init_db()

    print(f"Logged in as {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print(f"Guilds: {[g.name for g in bot.guilds]}")

    # Set bot status
    await bot.change_presence(activity=discord.Game(name="Using WynnExtras"))

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync: {e}")


# === Commands ===
@bot.tree.command(name="gambits", description="Get today's gambits")
async def gambits(interaction: discord.Interaction):
    await interaction.response.defer()
    data = await fetch_gambits()
    if not data:
        await interaction.followup.send("No gambits available for today.", ephemeral=True)
        return

    embed = discord.Embed(title="🎲 Today's Gambits", color=0xFFD700)
    for gambit in data.get("gambits", []):
        embed.add_field(name=gambit["name"], value=gambit["description"], inline=False)
    await interaction.followup.send(embed=embed)


# =============================================================================
# LootPoolView - COMMENTED OUT FOR FUTURE USE
# This view provides a dropdown selector for raids and camps (coming soon).
# Currently /lootpool uses the same behavior as /raidpool (shows overview).
# Uncomment this class when camp loot pools are implemented.
# =============================================================================
# class LootPoolView(discord.ui.View):
#     """
#     View with two dropdowns:
#     - Select Raid: Shows raid loot pools (NOTG, NOL, TCC, TNA)
#     - Select Camp: Coming soon - will show lootrun camp pools
#
#     Usage:
#         await interaction.followup.send(embed=embed, view=LootPoolView(original_user_id=interaction.user.id))
#     """
#     def __init__(self, original_user_id: int = None):
#         super().__init__(timeout=60)
#         self.original_user_id = original_user_id
#
#         # Raid select dropdown
#         raid_select = discord.ui.Select(
#             placeholder="Select Raid...",
#             options=[
#                 discord.SelectOption(label="Nest of the Grootslangs", value="NOTG", emoji=discord.PartialEmoji(name="notg", id=1466160820638584885)),
#                 discord.SelectOption(label="Orphion's Nexus of Light", value="NOL", emoji=discord.PartialEmoji(name="nol", id=1466160862296543458)),
#                 discord.SelectOption(label="The Canyon Colossus", value="TCC", emoji=discord.PartialEmoji(name="tcc", id=1466160902037438627)),
#                 discord.SelectOption(label="The Nameless Anomaly", value="TNA", emoji=discord.PartialEmoji(name="tna", id=1466160934937432409)),
#             ]
#         )
#         raid_select.callback = self.raid_select_callback
#         self.add_item(raid_select)
#
#         # Camp select dropdown (disabled - coming soon)
#         camp_select = discord.ui.Select(
#             placeholder="Select Camp (coming soon)",
#             options=[
#                 discord.SelectOption(label="Coming Soon", value="placeholder", emoji=discord.PartialEmoji(name="lootrun", id=1466173956884136188)),
#             ],
#             disabled=True
#         )
#         self.add_item(camp_select)
#
#     async def raid_select_callback(self, interaction: discord.Interaction):
#         if self.original_user_id and interaction.user.id != self.original_user_id:
#             await interaction.response.send_message("Only the person who used the command can use these buttons.", ephemeral=True)
#             return
#         raid_type = self.children[0].values[0]
#         await interaction.response.defer()
#         await show_raid_pool_edit(interaction, raid_type, original_user_id=self.original_user_id)
# =============================================================================


class RaidButtonsView(discord.ui.View):
    def __init__(self, original_user_id: int = None):
        super().__init__(timeout=300)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if self.original_user_id and interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Only the person who used the command can use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="NOTG", style=discord.ButtonStyle.primary, custom_id="raid_notg", emoji=discord.PartialEmoji(name="notg", id=1466160820638584885))
    async def notg_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await interaction.response.defer()
        await show_raid_pool_edit(interaction, "NOTG", original_user_id=self.original_user_id)

    @discord.ui.button(label="NOL", style=discord.ButtonStyle.primary, custom_id="raid_nol", emoji=discord.PartialEmoji(name="nol", id=1466160862296543458))
    async def nol_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await interaction.response.defer()
        await show_raid_pool_edit(interaction, "NOL", original_user_id=self.original_user_id)

    @discord.ui.button(label="TCC", style=discord.ButtonStyle.primary, custom_id="raid_tcc", emoji=discord.PartialEmoji(name="tcc", id=1466160902037438627))
    async def tcc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await interaction.response.defer()
        await show_raid_pool_edit(interaction, "TCC", original_user_id=self.original_user_id)

    @discord.ui.button(label="TNA", style=discord.ButtonStyle.primary, custom_id="raid_tna", emoji=discord.PartialEmoji(name="tna", id=1466160934937432409))
    async def tna_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await interaction.response.defer()
        await show_raid_pool_edit(interaction, "TNA", original_user_id=self.original_user_id)


async def show_aspects_overview(interaction: discord.Interaction, edit: bool = False, original_user_id: int = None):
    """Show the weekly loot pools overview."""
    # Get reset timestamps
    last_reset, next_reset = get_weekly_reset_times()

    # Fetch all mythic aspects
    mythics = await fetch_all_mythics()

    # Build main embed
    embed = discord.Embed(
        title="Weekly Loot Pools",
        description=f"**Updates at:** <t:{next_reset}:F>",
        color=0x5C005C  # Mythic purple
    )

    # Group mythics by raid, each raid in its own field
    if mythics:
        # Fetch class mapping to get flame emojis
        class_mapping = await get_aspect_class_mapping()

        for raid_type in RAID_TYPES:
            raid_mythics = [m for m in mythics if m.get("raid") == raid_type]
            if raid_mythics:
                aspect_lines = []
                for m in raid_mythics:
                    aspect_name = m['name']
                    aspect_class = get_aspect_class(aspect_name, class_mapping)
                    flame_emoji = get_aspect_emoji(aspect_class)
                    aspect_lines.append(f"{flame_emoji} {aspect_name}")

                field_name = f"{RAID_EMOJIS[raid_type]} {raid_type}"
                embed.add_field(name=field_name, value="\n".join(aspect_lines), inline=False)

    # Send or edit message with buttons
    if edit:
        await interaction.edit_original_response(embeds=[embed], view=RaidButtonsView(original_user_id=original_user_id))
    else:
        await interaction.followup.send(embed=embed, view=RaidButtonsView(original_user_id=original_user_id))


@bot.tree.command(name="aspects", description="View weekly aspect loot pools")
async def aspects(interaction: discord.Interaction):
    await interaction.response.defer()
    await show_aspects_overview(interaction, original_user_id=interaction.user.id)


@bot.tree.command(name="raidpool", description="Get the loot pool for a specific raid")
@app_commands.describe(raid="Select a raid type (optional)")
@app_commands.choices(raid=[
    app_commands.Choice(name="Nest of the Grootslangs (NOTG)", value="NOTG"),
    app_commands.Choice(name="Orphion's Nexus of Light (NOL)", value="NOL"),
    app_commands.Choice(name="The Canyon Colossus (TCC)", value="TCC"),
    app_commands.Choice(name="The Nameless Anomaly (TNA)", value="TNA"),
])
async def raidpool(interaction: discord.Interaction, raid: app_commands.Choice[str] = None):
    await interaction.response.defer()

    # If no raid specified, show the overview
    if raid is None:
        await show_aspects_overview(interaction, original_user_id=interaction.user.id)
        return

    await show_raid_pool(interaction, raid.value, followup=True, original_user_id=interaction.user.id)


class LinkAccountModal(discord.ui.Modal, title="Link Minecraft Account"):
    username = discord.ui.TextInput(
        label="Minecraft Username",
        placeholder="Enter your Minecraft username...",
        min_length=3,
        max_length=16,
        required=True
    )

    def __init__(self, raid_type: str = None, original_user_id: int = None):
        super().__init__()
        self.raid_type = raid_type
        self.original_user_id = original_user_id

    async def on_submit(self, interaction: discord.Interaction):
        player_name = self.username.value.strip()

        # Try to fetch player to verify they exist
        uuid = await fetch_player_uuid(player_name)
        if not uuid:
            await interaction.response.send_message(
                f"Could not find player **{player_name}**. Please check the spelling.",
                ephemeral=True
            )
            return

        # Link the account
        await set_linked_player(interaction.user.id, player_name)

        # Send success message and refresh the view
        await interaction.response.send_message(
            f"Successfully linked to **{player_name}**!",
            ephemeral=True
        )

        # Refresh the raid pool view to show the new linked state
        if self.raid_type:
            await show_raid_pool_edit(interaction, self.raid_type, original_user_id=self.original_user_id)


class BackToOverviewView(discord.ui.View):
    # Filter modes: "all", "maxed", "non_maxed"
    def __init__(self, raid_type: str = None, filter_mode: str = "all", is_linked: bool = False, original_user_id: int = None):
        super().__init__(timeout=300)
        self.raid_type = raid_type
        self.filter_mode = filter_mode
        self.is_linked = is_linked
        self.original_user_id = original_user_id
        self._build_buttons()

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if self.original_user_id and interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Only the person who used the command can use these buttons.", ephemeral=True)
            return False
        return True

    def _build_buttons(self):
        # Back button
        back_btn = discord.ui.Button(label="Back to Overview", style=discord.ButtonStyle.secondary)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

        # Only show filter buttons if we have a raid type
        if self.raid_type:
            if self.is_linked:
                # User is linked - show filter button
                if self.filter_mode == "all":
                    non_maxed_btn = discord.ui.Button(label="Only Non-Maxed", style=discord.ButtonStyle.danger)
                    non_maxed_btn.callback = self.non_maxed_callback
                    self.add_item(non_maxed_btn)
                else:
                    all_btn = discord.ui.Button(label="Show All", style=discord.ButtonStyle.primary)
                    all_btn.callback = self.all_callback
                    self.add_item(all_btn)
            else:
                # User not linked - show link button
                link_btn = discord.ui.Button(label="Link Account", style=discord.ButtonStyle.success, emoji="🔗")
                link_btn.callback = self.link_callback
                self.add_item(link_btn)

    async def back_callback(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.defer()
        await show_aspects_overview_edit(interaction, original_user_id=self.original_user_id)

    async def non_maxed_callback(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.defer()
        await show_raid_pool_edit(interaction, self.raid_type, filter_mode="non_maxed", original_user_id=self.original_user_id)

    async def all_callback(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.defer()
        await show_raid_pool_edit(interaction, self.raid_type, filter_mode="all", original_user_id=self.original_user_id)

    async def link_callback(self, interaction: discord.Interaction):
        # Open modal for linking account
        modal = LinkAccountModal(raid_type=self.raid_type, original_user_id=self.original_user_id)
        await interaction.response.send_modal(modal)


async def show_aspects_overview_edit(interaction: discord.Interaction, original_user_id: int = None):
    """Show the weekly loot pools overview (edit version)."""
    last_reset, next_reset = get_weekly_reset_times()
    mythics = await fetch_all_mythics()

    embed = discord.Embed(
        title="Weekly Loot Pools",
        description=f"**Updates at:** <t:{next_reset}:F>",
        color=0x5C005C
    )

    if mythics:
        # Fetch class mapping to get flame emojis
        class_mapping = await get_aspect_class_mapping()

        for raid_type in RAID_TYPES:
            raid_mythics = [m for m in mythics if m.get("raid") == raid_type]
            if raid_mythics:
                aspect_lines = []
                for m in raid_mythics:
                    aspect_name = m['name']
                    aspect_class = get_aspect_class(aspect_name, class_mapping)
                    flame_emoji = get_aspect_emoji(aspect_class)
                    aspect_lines.append(f"{flame_emoji} {aspect_name}")

                field_name = f"{RAID_EMOJIS[raid_type]} {raid_type}"
                embed.add_field(name=field_name, value="\n".join(aspect_lines), inline=False)

    await interaction.edit_original_response(embeds=[embed], view=RaidButtonsView(original_user_id=original_user_id))


async def show_raid_pool_edit(interaction: discord.Interaction, raid_type: str, filter_mode: str = "all", original_user_id: int = None):
    """Show loot pool for a specific raid (edit version)."""
    data = await fetch_loot_pool(raid_type)
    linked_player = await get_linked_player(interaction.user.id)
    is_linked = bool(linked_player)

    if not data:
        await interaction.edit_original_response(content=f"No loot pool available for {raid_type}.", embeds=[], view=BackToOverviewView(raid_type, is_linked=is_linked, original_user_id=original_user_id))
        return

    aspects_list = sort_aspects_by_rarity(data.get("aspects", []))
    score_text = None
    player_aspects = {}

    if linked_player:
        player_data = await fetch_player_aspects(linked_player)
        if player_data:
            for pa in player_data.get("aspects", []):
                name = pa.get("name", "")
                amount = pa.get("amount", 0)
                player_aspects[name] = amount

            pool_score = calculate_pool_score(aspects_list, player_aspects, raid_type)
            if pool_score == 0:
                score_text = "**Your Score:** MAXED"
            else:
                score_text = f"**Your Score:** {pool_score:.2f}"
    else:
        score_text = "*Use /link to see your score!*"

    # Fetch class mapping from Wynncraft API
    class_mapping = await get_aspect_class_mapping()

    # Build title with filter indicator
    title = f"{RAID_EMOJIS.get(raid_type, '<:lootrun:1466173956884136188>')} {RAID_NAMES.get(raid_type, raid_type)} Loot Pool"
    if filter_mode == "maxed":
        title += " (Maxed Only)"
    elif filter_mode == "non_maxed":
        title += " (Non-Maxed Only)"

    embed = discord.Embed(
        title=title,
        description=score_text,
        color=0x8B008B
    )

    if not aspects_list:
        embed.description = "No aspects in the loot pool."
        await interaction.edit_original_response(embeds=[embed], view=BackToOverviewView(raid_type, filter_mode, is_linked, original_user_id=original_user_id))
        return

    embeds = [embed]
    for rarity in ["Mythic", "Fabled", "Legendary"]:
        rarity_aspects = [a for a in aspects_list if a.get("rarity") == rarity]
        if not rarity_aspects:
            continue

        aspect_lines = []
        for aspect in rarity_aspects:
            aspect_name = aspect.get("name", "")
            aspect_rarity = aspect.get("rarity", "").lower()
            required_class = get_aspect_class(aspect_name, class_mapping)

            # Check if user has maxed this aspect
            is_maxed = False
            if aspect_name in player_aspects:
                max_threshold = ASPECT_MAX_THRESHOLDS.get(aspect_rarity, 150)
                player_amount = player_aspects[aspect_name]
                is_maxed = player_amount >= max_threshold

            # Apply filter
            if filter_mode == "maxed" and not is_maxed:
                continue
            if filter_mode == "non_maxed" and is_maxed:
                continue

            # Use different emoji for maxed vs not maxed
            if is_maxed:
                emoji = get_aspect_emoji(required_class)  # Maxed - animated flame
            else:
                # Not maxed - class item emoji
                emoji = CLASS_ITEM_EMOJIS.get(required_class.lower() if required_class else "", "") or get_aspect_emoji(required_class)

            aspect_lines.append(f"{emoji} {aspect_name}")

        # Only add embed if there are aspects to show
        if aspect_lines:
            rarity_embed = discord.Embed(
                title=f"{rarity} Aspects",
                description="\n".join(aspect_lines),
                color=RARITY_COLORS.get(rarity, 0x808080)
            )
            embeds.append(rarity_embed)

    # If filtering and no aspects found
    if filter_mode == "maxed" and len(embeds) == 1:
        embed.description = (score_text + "\n\n" if score_text else "") + "*No maxed aspects in this pool.*"
    elif filter_mode == "non_maxed" and len(embeds) == 1:
        embed.description = (score_text + "\n\n" if score_text else "") + "*All aspects in this pool are maxed!*"

    await interaction.edit_original_response(embeds=embeds, view=BackToOverviewView(raid_type, filter_mode, is_linked, original_user_id=original_user_id))


async def show_raid_pool(interaction: discord.Interaction, raid_type: str, followup: bool = True, edit: bool = False, filter_mode: str = "all", original_user_id: int = None):
    """Show loot pool for a specific raid."""
    linked_player = await get_linked_player(interaction.user.id)
    is_linked = bool(linked_player)

    data = await fetch_loot_pool(raid_type)
    if not data:
        if edit:
            await interaction.edit_original_response(content=f"No loot pool available for {raid_type}.", embeds=[], view=None)
        else:
            await interaction.followup.send(f"No loot pool available for {raid_type}.", ephemeral=True)
        return

    aspects_list = sort_aspects_by_rarity(data.get("aspects", []))
    score_text = None
    player_aspects = {}

    if linked_player:
        player_data = await fetch_player_aspects(linked_player)
        if player_data:
            for pa in player_data.get("aspects", []):
                player_aspects[pa.get("name", "")] = pa.get("amount", 0)

            pool_score = calculate_pool_score(aspects_list, player_aspects, raid_type)
            if pool_score == 0:
                score_text = "**Your Score:** MAXED"
            else:
                score_text = f"**Your Score:** {pool_score:.2f}"
    else:
        score_text = "*Use /link to see your score!*"

    # Fetch class mapping from Wynncraft API
    class_mapping = await get_aspect_class_mapping()

    # Build title with filter indicator
    title = f"{RAID_EMOJIS.get(raid_type, '<:lootrun:1466173956884136188>')} {RAID_NAMES.get(raid_type, raid_type)} Loot Pool"
    if filter_mode == "maxed":
        title += " (Maxed Only)"
    elif filter_mode == "non_maxed":
        title += " (Non-Maxed Only)"

    embed = discord.Embed(
        title=title,
        description=score_text,
        color=0x8B008B
    )

    if not aspects_list:
        embed.description = "No aspects in the loot pool."
        if edit:
            await interaction.edit_original_response(embed=embed, view=BackToOverviewView(raid_type, filter_mode, is_linked, original_user_id=original_user_id))
        else:
            await interaction.followup.send(embed=embed, view=BackToOverviewView(raid_type, filter_mode, is_linked, original_user_id=original_user_id))
        return

    # Create separate embeds per rarity with aspects listed vertically
    embeds = [embed]
    for rarity in ["Mythic", "Fabled", "Legendary"]:
        rarity_aspects = [a for a in aspects_list if a.get("rarity") == rarity]
        if not rarity_aspects:
            continue

        # Build text with each aspect on its own line
        aspect_lines = []
        for aspect in rarity_aspects:
            aspect_name = aspect.get("name", "")
            aspect_rarity = aspect.get("rarity", "").lower()
            required_class = get_aspect_class(aspect_name, class_mapping)

            # Check if user has maxed this aspect
            is_maxed = False
            if aspect_name in player_aspects:
                max_threshold = ASPECT_MAX_THRESHOLDS.get(aspect_rarity, 150)
                player_amount = player_aspects[aspect_name]
                is_maxed = player_amount >= max_threshold

            # Apply filter
            if filter_mode == "maxed" and not is_maxed:
                continue
            if filter_mode == "non_maxed" and is_maxed:
                continue

            # Use different emoji for maxed vs not maxed
            if is_maxed:
                emoji = get_aspect_emoji(required_class)  # Maxed - animated flame
            else:
                # Not maxed - class item emoji
                emoji = CLASS_ITEM_EMOJIS.get(required_class.lower() if required_class else "", "") or get_aspect_emoji(required_class)

            aspect_lines.append(f"{emoji} {aspect_name}")

        # Only add embed if there are aspects to show
        if aspect_lines:
            rarity_embed = discord.Embed(
                title=f"{rarity} Aspects",
                description="\n".join(aspect_lines),
                color=RARITY_COLORS.get(rarity, 0x808080)
            )
            embeds.append(rarity_embed)

    # If filtering and no aspects found
    if filter_mode == "maxed" and len(embeds) == 1:
        embed.description = (score_text + "\n\n" if score_text else "") + "*No maxed aspects in this pool.*"
    elif filter_mode == "non_maxed" and len(embeds) == 1:
        embed.description = (score_text + "\n\n" if score_text else "") + "*All aspects in this pool are maxed!*"

    if edit:
        await interaction.edit_original_response(embeds=embeds, view=BackToOverviewView(raid_type, filter_mode, is_linked, original_user_id=original_user_id))
    else:
        await interaction.followup.send(embeds=embeds, view=BackToOverviewView(raid_type, filter_mode, is_linked, original_user_id=original_user_id))


@bot.tree.command(name="lootpool", description="View loot pools for raids")
@app_commands.describe(pool="Select a raid loot pool")
@app_commands.choices(pool=[
    app_commands.Choice(name="Nest of the Grootslangs (NOTG)", value="NOTG"),
    app_commands.Choice(name="Orphion's Nexus of Light (NOL)", value="NOL"),
    app_commands.Choice(name="The Canyon Colossus (TCC)", value="TCC"),
    app_commands.Choice(name="The Nameless Anomaly (TNA)", value="TNA"),
])
async def lootpool(interaction: discord.Interaction, pool: app_commands.Choice[str] = None):
    """View loot pools - works exactly like /raidpool."""
    await interaction.response.defer()

    # If no pool specified, show the overview (same as /raidpool)
    if pool is None:
        await show_aspects_overview(interaction, original_user_id=interaction.user.id)
        return

    await show_raid_pool(interaction, pool.value, followup=True, original_user_id=interaction.user.id)


# === Profile Viewer ===
class ProfileView(discord.ui.View):
    TABS = ["General", "Raids", "Rankings", "Dungeons", "Profs", "Aspects", "Misc"]

    def __init__(self, player_data: dict, uuid: str, current_tab: str = "General", aspects_data: dict = None, original_user_id: int = None):
        super().__init__(timeout=300)
        self.player_data = player_data
        self.uuid = uuid
        self.current_tab = current_tab
        self.aspects_data = aspects_data
        self.original_user_id = original_user_id
        self._build_buttons()

    def _build_buttons(self):
        for tab in self.TABS:
            if tab == self.current_tab:
                continue  # Hide current tab button
            button = discord.ui.Button(label=tab, style=discord.ButtonStyle.primary)
            button.callback = self._make_callback(tab)
            self.add_item(button)

    def _make_callback(self, tab: str):
        async def callback(interaction: discord.Interaction):
            # Check if user is the original command user
            if self.original_user_id and interaction.user.id != self.original_user_id:
                await interaction.response.send_message("Only the person who used the command can use these buttons.", ephemeral=True)
                return

            await interaction.response.defer()

            # For Aspects tab, we need to fetch data
            aspects_data = self.aspects_data
            if tab == "Aspects" and not aspects_data:
                # Fetch aspects from WynnExtras API
                aspects_data = await fetch_aspects_by_uuid(self.uuid)

            embed = await self._get_embed_async(tab, aspects_data)
            new_view = ProfileView(self.player_data, self.uuid, current_tab=tab, aspects_data=aspects_data, original_user_id=self.original_user_id)
            await interaction.edit_original_response(embed=embed, view=new_view)
        return callback

    async def _get_embed_async(self, tab: str, aspects_data: dict = None) -> discord.Embed:
        if tab == "General":
            return build_general_embed(self.player_data)
        elif tab == "Raids":
            return build_raids_embed(self.player_data)
        elif tab == "Rankings":
            return build_rankings_embed(self.player_data)
        elif tab == "Dungeons":
            return build_dungeons_embed(self.player_data)
        elif tab == "Profs":
            return build_profs_embed(self.player_data)
        elif tab == "Aspects":
            return await build_aspects_embed(self.player_data, aspects_data)
        elif tab == "Misc":
            return build_misc_embed(self.player_data)
        return build_general_embed(self.player_data)


def build_general_embed(data: dict) -> discord.Embed:
    """Build the General tab embed."""
    username = data.get("username", "Unknown")
    online = data.get("online", False)
    server = data.get("server", "Offline")
    rank = data.get("supportRank") or data.get("rank", "Player")
    guild = data.get("guild")

    color = RANK_COLORS.get(rank.lower() if rank else "", 0x808080)
    rank_display = RANK_DISPLAY.get(rank.lower() if rank else "", "Player")

    status = f"🟢 Online ({server})" if online else "⚫ Offline"

    embed = discord.Embed(
        title=f"👤 {username}",
        description=f"**Rank:** {rank_display}\n**Status:** {status}",
        color=color
    )

    if guild:
        guild_name = guild.get("name", "Unknown")
        guild_rank = guild.get("rank", "Member")
        embed.add_field(name="Guild", value=f"{guild_name} ({guild_rank})", inline=False)

    # List characters
    characters = data.get("characters", {})

    if characters:
        char_lines = []
        # Sort by level descending
        sorted_chars = sorted(characters.items(), key=lambda x: x[1].get("level", 0), reverse=True)
        for char_uuid, char_data in sorted_chars[:10]:  # Max 10 characters
            char_type = char_data.get("type", "UNKNOWN")
            level = char_data.get("level", 0)
            total_level = char_data.get("totalLevel", 0)
            emoji = CLASS_EMOJIS_PV.get(char_type, "❓")
            char_lines.append(f"{emoji} {char_type.title()} - Lv.{level} (Total: {total_level})")

        embed.add_field(name="Characters", value="\n".join(char_lines), inline=False)

    return embed


def build_raids_embed(data: dict) -> discord.Embed:
    """Build the Raids tab embed."""
    embed = discord.Embed(title="⚔️ Raids", color=0xFF5555)

    global_data = data.get("globalData", {})
    raids = global_data.get("raids", {})
    raids_list = raids.get("list", {})
    total = raids.get("total", 0)

    raid_info = [
        ("Nest of the Grootslangs", "NOTG"),
        ("Orphion's Nexus of Light", "NOL"),
        ("The Canyon Colossus", "TCC"),
        ("The Nameless Anomaly", "TNA"),
    ]

    raid_text = ""
    for raid_name, raid_code in raid_info:
        count = raids_list.get(raid_name, 0)
        emoji = RAID_EMOJIS.get(raid_code, "")
        raid_text += f"{emoji} **{raid_name}:** {count:,}\n"

    raid_text += f"\n**Total Raids:** {total:,}"
    embed.description = raid_text

    return embed


def build_rankings_embed(data: dict) -> discord.Embed:
    """Build the Rankings tab embed."""
    embed = discord.Embed(title="🥇 Rankings", color=0xFFD700)

    ranking = data.get("ranking", {})

    if not ranking:
        embed.description = "No ranking data available."
        return embed

    # Combat/general rankings
    combat_ranks = {
        "combatGlobalLevel": "Combat Level",
        "totalGlobalLevel": "Total Level",
        "warsCompletion": "Wars",
        "playerContent": "Content",
        "globalPlayerContent": "Global Content",
    }

    # Raid rankings
    raid_ranks = {
        "grootslangCompletion": "NOTG",
        "orphionCompletion": "NOL",
        "colossusCompletion": "TCC",
        "namelessCompletion": "TNA",
    }

    # Profession rankings
    prof_ranks = {
        "fishingLevel": "Fishing",
        "woodcuttingLevel": "Woodcutting",
        "miningLevel": "Mining",
        "farmingLevel": "Farming",
        "scribingLevel": "Scribing",
        "jewelingLevel": "Jeweling",
        "alchemismLevel": "Alchemism",
        "cookingLevel": "Cooking",
        "weaponsmithingLevel": "Weaponsmithing",
        "tailoringLevel": "Tailoring",
        "woodworkingLevel": "Woodworking",
        "armouringLevel": "Armouring",
    }

    def get_rank_prefix(rank: int) -> str:
        if rank == 1:
            return "🥇 "
        elif rank == 2:
            return "🥈 "
        elif rank == 3:
            return "🥉 "
        elif rank <= 100:
            return "🏆 "
        return ""

    combat_text = ""
    for key, name in combat_ranks.items():
        if key in ranking:
            rank = ranking[key]
            prefix = get_rank_prefix(rank)
            combat_text += f"{prefix}**{name}:** #{rank:,}\n"

    if combat_text:
        embed.add_field(name="General", value=combat_text, inline=True)

    raid_text = ""
    for key, name in raid_ranks.items():
        if key in ranking:
            rank = ranking[key]
            prefix = get_rank_prefix(rank)
            raid_text += f"{prefix}**{name}:** #{rank:,}\n"

    if raid_text:
        embed.add_field(name="Raids", value=raid_text, inline=True)

    prof_text = ""
    for key, name in prof_ranks.items():
        if key in ranking:
            rank = ranking[key]
            prefix = get_rank_prefix(rank)
            prof_text += f"{prefix}**{name}:** #{rank:,}\n"

    if prof_text:
        embed.add_field(name="Professions", value=prof_text[:1024], inline=False)

    return embed


def build_profs_embed(data: dict) -> discord.Embed:
    """Build the Professions tab embed showing highest level character's profs."""
    embed = discord.Embed(title="<:prof:1466127084291100981> Professions", color=0x55FF55)

    characters = data.get("characters", {})

    if not characters:
        embed.description = "No characters found."
        return embed

    # Find character with highest total level
    best_char = None
    best_level = 0
    for char_uuid, char_data in characters.items():
        total = char_data.get("totalLevel", 0)
        if total > best_level:
            best_level = total
            best_char = char_data

    if not best_char:
        embed.description = "No character data found."
        return embed

    char_type = best_char.get("type", "UNKNOWN")
    char_level = best_char.get("level", 0)
    emoji = CLASS_EMOJIS_PV.get(char_type, "❓")

    embed.description = f"**{emoji} {char_type.title()}** - Combat Lv.{char_level} (Total: {best_level})\n"

    profs = best_char.get("professions", {})

    if not profs:
        embed.description += "\nNo profession data available."
        return embed

    # Gathering profs
    gathering = ["fishing", "woodcutting", "mining", "farming"]
    # Crafting profs
    crafting = ["scribing", "jeweling", "alchemism", "cooking", "weaponsmithing", "tailoring", "woodworking", "armouring"]

    gathering_text = ""
    for prof in gathering:
        prof_data = profs.get(prof, {})
        level = prof_data.get("level", 0)
        xp = prof_data.get("xpPercent", 0)
        maxed = "⭐ " if level >= 132 else ""
        gathering_text += f"{maxed}**{prof.title()}:** {level}/132 ({xp}%)\n"

    embed.add_field(name="Gathering", value=gathering_text, inline=True)

    crafting_text = ""
    for prof in crafting:
        prof_data = profs.get(prof, {})
        level = prof_data.get("level", 0)
        xp = prof_data.get("xpPercent", 0)
        maxed = "⭐ " if level >= 132 else ""
        crafting_text += f"{maxed}**{prof.title()}:** {level}/132 ({xp}%)\n"

    embed.add_field(name="Crafting", value=crafting_text, inline=True)

    return embed


# Max thresholds for aspects by rarity
ASPECT_MAX_THRESHOLDS = {
    "mythic": 15,
    "fabled": 75,
    "legendary": 150,
}


async def build_aspects_embed(player_data: dict, aspects_data: dict) -> discord.Embed:
    """Build the Aspects tab embed showing maxed aspects per class."""
    username = player_data.get("username", "Unknown")
    embed = discord.Embed(title=f"{ASPECT_EMOJIS['assassin']} {username}'s Aspects", color=0x8B008B)

    if not aspects_data or "aspects" not in aspects_data:
        embed.description = "No aspects data available.\nThis player hasn't uploaded aspects from the WynnExtras mod."
        return embed

    # Get class mapping from Wynncraft API
    class_mapping = await get_aspect_class_mapping()

    player_aspects = aspects_data.get("aspects", [])

    # Count maxed per class
    classes = ["warrior", "mage", "archer", "assassin", "shaman"]
    class_stats = {c: {"total": 0, "maxed": 0, "maxed_names": []} for c in classes}

    # Count total aspects per class from Wynncraft API
    for aspect_name, aspect_class in class_mapping.items():
        if aspect_class in class_stats:
            class_stats[aspect_class]["total"] += 1

    # Check which aspects the player has maxed
    for aspect in player_aspects:
        name = aspect.get("name", "")
        amount = aspect.get("amount", 0)
        rarity = aspect.get("rarity", "").lower()

        aspect_class = class_mapping.get(name)
        if not aspect_class or aspect_class not in class_stats:
            continue

        max_threshold = ASPECT_MAX_THRESHOLDS.get(rarity, 150)
        if amount >= max_threshold:
            class_stats[aspect_class]["maxed"] += 1
            class_stats[aspect_class]["maxed_names"].append(name)

    # Build summary
    total_maxed = sum(s["maxed"] for s in class_stats.values())
    total_aspects = sum(s["total"] for s in class_stats.values())

    embed.description = f"**Total Maxed:** {total_maxed}/{total_aspects}\n"

    # Add field for each class
    for class_name in classes:
        stats = class_stats[class_name]
        emoji = CLASS_EMOJIS.get(class_name, "")
        maxed = stats["maxed"]
        total = stats["total"]

        if total == 0:
            continue

        progress = "⭐ MAXED" if maxed == total else f"{maxed}/{total}"
        embed.add_field(
            name=f"{emoji} {class_name.title()}",
            value=progress,
            inline=True
        )

    return embed


def build_dungeons_embed(data: dict) -> discord.Embed:
    """Build the Dungeons tab embed."""
    embed = discord.Embed(title="<:dungeon_key:1466127009968296028> Dungeons", color=0x00AAAA)

    global_data = data.get("globalData", {})
    dungeons = global_data.get("dungeons", {})

    if not dungeons:
        embed.description = "No dungeon data available."
        return embed

    total = dungeons.get("total", 0)
    dungeon_list = dungeons.get("list", {})

    dungeon_text = f"**Total Completions:** {total:,}\n\n"

    # Normal dungeons
    normal_dungeons = []
    corrupted_dungeons = []

    for name, count in sorted(dungeon_list.items()):
        # Only show valid current dungeons
        if name not in VALID_DUNGEONS:
            continue
        if count > 0:
            if name.startswith("Corrupted"):
                corrupted_dungeons.append(f"**{name}:** {count:,}")
            else:
                normal_dungeons.append(f"**{name}:** {count:,}")

    if normal_dungeons:
        dungeon_text += "\n".join(normal_dungeons) + "\n"

    if corrupted_dungeons:
        dungeon_text += "\n__Corrupted:__\n" + "\n".join(corrupted_dungeons)

    embed.description = dungeon_text[:4096]
    return embed


def build_misc_embed(data: dict) -> discord.Embed:
    """Build the Misc tab embed."""
    embed = discord.Embed(title="📊 Misc Stats", color=0xAAAAAA)

    global_data = data.get("globalData", {})

    stats = []
    stats.append(f"**Wars Completed:** {global_data.get('wars', 0):,}")
    stats.append(f"**Mobs Killed:** {global_data.get('mobsKilled', 0):,}")
    stats.append(f"**Chests Found:** {global_data.get('chestsFound', 0):,}")

    pvp = global_data.get("pvp", {})
    stats.append(f"**PvP Kills:** {pvp.get('kills', 0):,}")
    stats.append(f"**PvP Deaths:** {pvp.get('deaths', 0):,}")

    # Total level and playtime
    stats.append(f"**Total Level:** {global_data.get('totalLevel', 0):,}")
    stats.append(f"**Playtime:** {data.get('playtime', 0):.1f} hours")

    embed.description = "\n".join(stats)
    return embed


@bot.tree.command(name="pv", description="View a player's Wynncraft profile")
@app_commands.describe(player="Minecraft username to look up (leave empty to use linked account)")
async def pv(interaction: discord.Interaction, player: str = None):
    await interaction.response.defer()

    # If no player specified, use linked account
    if not player:
        player = await get_linked_player(interaction.user.id)
        if not player:
            await interaction.followup.send("No player specified and you don't have a linked account. Use `/link` first or specify a player name.", ephemeral=True)
            return

    # Get UUID from Mojang
    uuid = await fetch_player_uuid(player)
    if not uuid:
        await interaction.followup.send(f"Player **{player}** not found.", ephemeral=True)
        return

    # Fetch Wynncraft data
    data = await fetch_wynncraft_player(uuid)
    if not data:
        await interaction.followup.send(f"Could not fetch Wynncraft data for **{player}**. They may have never played Wynncraft.", ephemeral=True)
        return

    # Build initial embed (General tab)
    embed = build_general_embed(data)

    # Send with tab buttons
    await interaction.followup.send(embed=embed, view=ProfileView(data, uuid, original_user_id=interaction.user.id))


@bot.tree.command(name="link", description="Link your Discord to a Minecraft account")
@app_commands.describe(player="Minecraft username to link (leave empty to see current link)")
async def link(interaction: discord.Interaction, player: str = None):
    discord_id = interaction.user.id

    if not player:
        current_link = await get_linked_player(discord_id)
        if current_link:
            embed = discord.Embed(
                title="🔗 Account Linked",
                description=f"Your Discord is linked to **{current_link}**",
                color=0x00FF00
            )
            embed.add_field(name="Commands", value="`/raidpool` - View loot pools with your personalized score\n`/unlink` - Remove link", inline=False)
        else:
            embed = discord.Embed(
                title="🔗 Not Linked",
                description="Use `/link player:YourMinecraftName` to link your account.",
                color=0xFF6600
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    data = await fetch_player_aspects(player)
    if not data:
        await interaction.followup.send(f"Player **{player}** not found. Make sure you've uploaded aspects from the mod.", ephemeral=True)
        return

    player_name = data.get("playerName", player)
    await set_linked_player(discord_id, player_name)

    embed = discord.Embed(
        title="✅ Account Linked!",
        description=f"Your Discord is now linked to **{player_name}**",
        color=0x00FF00
    )
    embed.add_field(name="Next Steps", value="`/raidpool` - View loot pools with your personalized score\n`/unlink` - Remove link", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="unlink", description="Unlink your Discord from your Minecraft account")
async def unlink(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    old_name = await remove_linked_player(interaction.user.id)
    if old_name:
        await interaction.followup.send(f"✅ Unlinked from **{old_name}**.", ephemeral=True)
    else:
        await interaction.followup.send("You don't have a linked account.", ephemeral=True)


@bot.tree.command(name="emojitest", description="Test if animated emojis work")
async def emojitest(interaction: discord.Interaction):
    """Debug command to test emoji rendering."""
    test_msg = f"""
**Testing animated emojis:**
Warrior: {ASPECT_EMOJIS['warrior']}
Mage: {ASPECT_EMOJIS['mage']}
Archer: {ASPECT_EMOJIS['archer']}
Assassin: {ASPECT_EMOJIS['assassin']}
Shaman: {ASPECT_EMOJIS['shaman']}

If you see broken emojis, the bot needs "Use External Emojis" permission.
Bot must also be in the server where these emojis are hosted.
"""
    await interaction.response.send_message(test_msg)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found!")
        exit(1)
    bot.run(token)
