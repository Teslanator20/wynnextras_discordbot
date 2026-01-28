import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://wynnextras.com"
LINKED_USERS_FILE = "linked_users.json"

RAID_TYPES = ["NOTG", "NOL", "TCC", "TNA"]
RAID_NAMES = {
    "NOTG": "Nest of the Grootslangs",
    "NOL": "Orphion's Nexus of Light",
    "TCC": "The Canyon Colossus",
    "TNA": "The Nameless Anomaly"
}

RAID_EMOJIS = {
    "NOTG": "<:notg:1466085912520691957>",
    "NOL": "<:nol:1466086178913779927>",
    "TCC": "<:tcc:1466086007941365922>",
    "TNA": "<:tna:1466086122655453377>",
}

# Raid emoji IDs for reactions
RAID_EMOJI_IDS = {
    "NOTG": 1466085912520691957,
    "NOL": 1466086178913779927,
    "TCC": 1466086007941365922,
    "TNA": 1466086122655453377,
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
    "warrior": "<a:aspect_warrior:1466068961681735974>",
    "mage": "<a:aspect_mage:1466068918870474868>",
    "archer": "<a:aspect_archer:1466068869822157031>",
    "assassin": "<a:aspect_assassin:1466068898964308071>",
    "shaman": "<a:aspect_shaman:1466068939854446592>",
}

# Class emojis (static)
CLASS_EMOJIS = {
    "warrior": "<:class_warrior:1466120334850654250>",
    "mage": "<:class_mage:1466120277678227550>",
    "archer": "<:class_archer:1466120313270964316>",
    "assassin": "<:class_assassin:1466120777324560697>",
    "shaman": "<:class_shaman:1466120243767414936>",
}

# Max amounts for each rarity
MAX_AMOUNTS = {"Mythic": 15, "Fabled": 75, "Legendary": 150}

# Tier thresholds for each rarity (amount needed to reach each tier)
TIER_THRESHOLDS = {
    "Mythic": [0, 5, 15],       # Tier I: 0-4, Tier II: 5-14, MAX: 15+
    "Fabled": [0, 15, 75],      # Tier I: 0-14, Tier II: 15-74, MAX: 75+
    "Legendary": [0, 5, 30, 150], # Tier I: 0-4, Tier II: 5-29, Tier III: 30-149, MAX: 150+
}

# Weights per remaining aspect based on rarity and current tier
# Higher = more valuable to get
TIER_WEIGHTS = {
    "Mythic": [20.0, 13.55],      # Tier I->II weight, Tier II->MAX weight
    "Fabled": [10.4, 0.65],       # Tier I->II, Tier II->MAX
    "Legendary": [15.0, 1.5, 0.905], # Tier I->II, II->III, III->MAX
}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# === User Linking ===
def load_linked_users() -> dict:
    if os.path.exists(LINKED_USERS_FILE):
        try:
            with open(LINKED_USERS_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_linked_users(data: dict):
    with open(LINKED_USERS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_linked_player(discord_id: int) -> str | None:
    return load_linked_users().get(str(discord_id))


def set_linked_player(discord_id: int, player_name: str):
    users = load_linked_users()
    users[str(discord_id)] = player_name
    save_linked_users(users)


def remove_linked_player(discord_id: int) -> str | None:
    users = load_linked_users()
    old = users.pop(str(discord_id), None)
    save_linked_users(users)
    return old


# === API Functions ===
async def fetch_gambits():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/gambit") as resp:
            return await resp.json() if resp.status == 200 else None


async def fetch_loot_pool(raid_type: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/raid/loot-pool?raidType={raid_type}") as resp:
            return await resp.json() if resp.status == 200 else None


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
def get_current_tier(rarity: str, amount: int) -> int:
    """Get current tier (1-based) for an aspect."""
    thresholds = TIER_THRESHOLDS.get(rarity, [0])
    tier = 1
    for i, threshold in enumerate(thresholds):
        if amount >= threshold:
            tier = i + 1
    return tier


def get_remaining_to_max(rarity: str, amount: int) -> int:
    """Get how many more aspects needed to max."""
    max_amt = MAX_AMOUNTS.get(rarity, 999)
    return max(0, max_amt - amount)


def calculate_aspect_score(rarity: str, amount: int) -> float:
    """Calculate score contribution for a single aspect (how much work remains)."""
    if amount >= MAX_AMOUNTS.get(rarity, 999):
        return 0.0  # Already maxed, no score

    thresholds = TIER_THRESHOLDS.get(rarity, [0])
    weights = TIER_WEIGHTS.get(rarity, [1.0])

    score = 0.0
    current_amount = amount

    # Calculate score for each tier transition
    for i in range(len(thresholds) - 1):
        tier_start = thresholds[i]
        tier_end = thresholds[i + 1] if i + 1 < len(thresholds) else MAX_AMOUNTS.get(rarity, 999)
        weight = weights[i] if i < len(weights) else weights[-1]

        if current_amount < tier_end:
            # Player is in or before this tier
            start = max(current_amount, tier_start)
            remaining_in_tier = tier_end - start
            score += remaining_in_tier * weight

    return score


def calculate_pool_score(pool_aspects: list, player_aspects: dict) -> float:
    """Calculate total score for a loot pool based on player progress."""
    total_score = 0.0

    for aspect in pool_aspects:
        name = aspect.get("name", "")
        rarity = aspect.get("rarity", "")

        # Get player's current amount for this aspect
        player_amount = player_aspects.get(name, 0)

        # Add score contribution
        total_score += calculate_aspect_score(rarity, player_amount)

    return total_score


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
    """Fetch mythic aspects from all raids."""
    all_mythics = []
    for raid_type in RAID_TYPES:
        data = await fetch_loot_pool(raid_type)
        if data:
            for aspect in data.get("aspects", []):
                if aspect.get("rarity") == "Mythic":
                    aspect["raid"] = raid_type
                    all_mythics.append(aspect)
    return all_mythics


# === Bot Events ===
@bot.event
async def on_ready():
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


class LootPoolView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Raid Loot Pool", style=discord.ButtonStyle.primary, emoji="⚔️")
    async def raid_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Select a raid:", view=RaidSelectView(), ephemeral=True)

    @discord.ui.button(label="Lootrun (Coming Soon)", style=discord.ButtonStyle.secondary, emoji="🏃", disabled=True)
    async def lootrun_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Lootrun loot pools are not yet implemented.", ephemeral=True)


class RaidSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a raid...",
            options=[
                discord.SelectOption(label="Nest of the Grootslangs", value="NOTG", emoji="🐍"),
                discord.SelectOption(label="Orphion's Nexus of Light", value="NOL", emoji="✨"),
                discord.SelectOption(label="The Canyon Colossus", value="TCC", emoji="🪨"),
                discord.SelectOption(label="The Nameless Anomaly", value="TNA", emoji="🌀"),
            ]
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        raid_type = self.children[0].values[0]
        await interaction.response.defer()

        data = await fetch_loot_pool(raid_type)
        if not data:
            await interaction.followup.send(f"No loot pool available for {raid_type}.", ephemeral=True)
            return

        aspects = sort_aspects_by_rarity(data.get("aspects", []))

        # Personalized score
        linked_player = get_linked_player(interaction.user.id)
        score_text = None

        if linked_player:
            player_data = await fetch_player_aspects(linked_player)
            if player_data:
                player_aspects = {pa.get("name", ""): pa.get("amount", 0) for pa in player_data.get("aspects", [])}
                pool_score = calculate_pool_score(aspects, player_aspects)
                if pool_score == 0:
                    score_text = "**Your Score:** MAXED"
                else:
                    score_text = f"**Your Score:** {pool_score:.2f}"

        embed = discord.Embed(
            title=f"{RAID_EMOJIS.get(raid_type, '📦')} {RAID_NAMES.get(raid_type, raid_type)} Loot Pool",
            description=score_text,
            color=0x8B008B
        )

        if not aspects:
            embed.description = "No aspects in the loot pool."
            await interaction.followup.send(embed=embed)
            return

        # Create separate embeds per rarity with colored sidebars
        embeds = [embed]
        for rarity in ["Mythic", "Fabled", "Legendary"]:
            rarity_aspects = [a for a in aspects if a.get("rarity") == rarity]
            if not rarity_aspects:
                continue

            rarity_embed = discord.Embed(
                title=f"{rarity} Aspects",
                color=RARITY_COLORS.get(rarity, 0x808080)
            )

            for aspect in rarity_aspects:
                required_class = aspect.get("requiredClass")
                emoji = get_aspect_emoji(required_class)
                rarity_embed.add_field(
                    name=f"{emoji} {aspect['name']}",
                    value="\u200b",
                    inline=True
                )

            embeds.append(rarity_embed)

        await interaction.followup.send(embeds=embeds)


class RaidButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="NOTG", style=discord.ButtonStyle.primary, custom_id="raid_notg")
    async def notg_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await show_raid_pool(interaction, "NOTG")

    @discord.ui.button(label="NOL", style=discord.ButtonStyle.primary, custom_id="raid_nol")
    async def nol_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await show_raid_pool(interaction, "NOL")

    @discord.ui.button(label="TCC", style=discord.ButtonStyle.primary, custom_id="raid_tcc")
    async def tcc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await show_raid_pool(interaction, "TCC")

    @discord.ui.button(label="TNA", style=discord.ButtonStyle.primary, custom_id="raid_tna")
    async def tna_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await show_raid_pool(interaction, "TNA")


async def show_aspects_overview(interaction: discord.Interaction):
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

    # Group mythics by raid, each aspect on its own line
    if mythics:
        mythic_text = ""
        for raid_type in RAID_TYPES:
            raid_mythics = [m for m in mythics if m.get("raid") == raid_type]
            if raid_mythics:
                for m in raid_mythics:
                    mythic_text += f"{RAID_EMOJIS[raid_type]} {m['name']}\n"
                mythic_text += "\n"  # Extra line between raids

        if mythic_text:
            embed.add_field(name="Mythic Aspects", value=mythic_text.strip(), inline=False)

    # Send message with buttons
    await interaction.followup.send(embed=embed, view=RaidButtonsView())


@bot.tree.command(name="aspects", description="View weekly aspect loot pools")
async def aspects(interaction: discord.Interaction):
    await interaction.response.defer()
    await show_aspects_overview(interaction)


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
        await show_aspects_overview(interaction)
        return

    await show_raid_pool(interaction, raid.value, followup=True)


async def show_raid_pool(interaction: discord.Interaction, raid_type: str, followup: bool = True):
    """Show loot pool for a specific raid."""
    data = await fetch_loot_pool(raid_type)
    if not data:
        await interaction.followup.send(f"No loot pool available for {raid_type}.", ephemeral=True)
        return

    aspects_list = sort_aspects_by_rarity(data.get("aspects", []))

    # Check if user is linked and calculate personalized score
    linked_player = get_linked_player(interaction.user.id)
    score_text = None

    if linked_player:
        player_data = await fetch_player_aspects(linked_player)
        if player_data:
            player_aspects = {}
            for pa in player_data.get("aspects", []):
                player_aspects[pa.get("name", "")] = pa.get("amount", 0)

            pool_score = calculate_pool_score(aspects_list, player_aspects)
            if pool_score == 0:
                score_text = "**Your Score:** MAXED"
            else:
                score_text = f"**Your Score:** {pool_score:.2f}"

    embed = discord.Embed(
        title=f"{RAID_EMOJIS.get(raid_type, '📦')} {RAID_NAMES.get(raid_type, raid_type)} Loot Pool",
        description=score_text,
        color=0x8B008B
    )

    if not aspects_list:
        embed.description = "No aspects in the loot pool."
        await interaction.followup.send(embed=embed)
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
            required_class = aspect.get("requiredClass")
            emoji = get_aspect_emoji(required_class)
            aspect_lines.append(f"{emoji} {aspect['name']}")

        rarity_embed = discord.Embed(
            title=f"{rarity} Aspects",
            description="\n".join(aspect_lines),
            color=RARITY_COLORS.get(rarity, 0x808080)
        )

        embeds.append(rarity_embed)

    await interaction.followup.send(embeds=embeds)


@bot.tree.command(name="lootpool", description="View loot pools for raids or lootruns")
async def lootpool(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📦 Loot Pool Viewer",
        description="Select what type of loot pool you want to view:",
        color=0x5865F2
    )
    await interaction.response.send_message(embed=embed, view=LootPoolView())


# === Profile Viewer ===
class ProfileView(discord.ui.View):
    def __init__(self, player_data: dict, uuid: str):
        super().__init__(timeout=300)
        self.player_data = player_data
        self.uuid = uuid

    @discord.ui.button(label="General", style=discord.ButtonStyle.primary)
    async def general_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_general_embed(self.player_data)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Raids", style=discord.ButtonStyle.primary)
    async def raids_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_raids_embed(self.player_data)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Rankings", style=discord.ButtonStyle.primary)
    async def rankings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_rankings_embed(self.player_data)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Dungeons", style=discord.ButtonStyle.primary)
    async def dungeons_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_dungeons_embed(self.player_data)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Profs", style=discord.ButtonStyle.primary)
    async def profs_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_profs_embed(self.player_data)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Misc", style=discord.ButtonStyle.primary)
    async def misc_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_misc_embed(self.player_data)
        await interaction.response.edit_message(embed=embed, view=self)


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
    embed = discord.Embed(title="🏆 Rankings", color=0xFFD700)

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

    combat_text = ""
    for key, name in combat_ranks.items():
        if key in ranking:
            rank = ranking[key]
            trophy = "🏆 " if rank <= 100 else ""
            combat_text += f"{trophy}**{name}:** #{rank:,}\n"

    if combat_text:
        embed.add_field(name="General", value=combat_text, inline=True)

    raid_text = ""
    for key, name in raid_ranks.items():
        if key in ranking:
            rank = ranking[key]
            trophy = "🏆 " if rank <= 100 else ""
            raid_text += f"{trophy}**{name}:** #{rank:,}\n"

    if raid_text:
        embed.add_field(name="Raids", value=raid_text, inline=True)

    prof_text = ""
    for key, name in prof_ranks.items():
        if key in ranking:
            rank = ranking[key]
            trophy = "🏆 " if rank <= 100 else ""
            prof_text += f"{trophy}**{name}:** #{rank:,}\n"

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
        maxed = "🌈 " if level >= 132 else ""
        gathering_text += f"{maxed}**{prof.title()}:** {level}/132 ({xp}%)\n"

    embed.add_field(name="Gathering", value=gathering_text, inline=True)

    crafting_text = ""
    for prof in crafting:
        prof_data = profs.get(prof, {})
        level = prof_data.get("level", 0)
        xp = prof_data.get("xpPercent", 0)
        maxed = "🌈 " if level >= 132 else ""
        crafting_text += f"{maxed}**{prof.title()}:** {level}/132 ({xp}%)\n"

    embed.add_field(name="Crafting", value=crafting_text, inline=True)

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
        player = get_linked_player(interaction.user.id)
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
    await interaction.followup.send(embed=embed, view=ProfileView(data, uuid))


@bot.tree.command(name="link", description="Link your Discord to a Minecraft account")
@app_commands.describe(player="Minecraft username to link (leave empty to see current link)")
async def link(interaction: discord.Interaction, player: str = None):
    discord_id = interaction.user.id
    current_link = get_linked_player(discord_id)

    if not player:
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
    set_linked_player(discord_id, player_name)

    embed = discord.Embed(
        title="✅ Account Linked!",
        description=f"Your Discord is now linked to **{player_name}**",
        color=0x00FF00
    )
    embed.add_field(name="Next Steps", value="`/raidpool` - View loot pools with your personalized score\n`/unlink` - Remove link", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="unlink", description="Unlink your Discord from your Minecraft account")
async def unlink(interaction: discord.Interaction):
    old_name = remove_linked_player(interaction.user.id)
    if old_name:
        await interaction.response.send_message(f"✅ Unlinked from **{old_name}**.", ephemeral=True)
    else:
        await interaction.response.send_message("You don't have a linked account.", ephemeral=True)


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
