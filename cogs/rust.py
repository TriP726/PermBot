import math
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal, Dict, Any, List
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Select, Modal, TextInput

from utils.helpers import load_json, save_json

RUST_DATA_FILE = "data/rust_data.json"

def get_rust_data() -> Dict[str, Any]:
    return load_json(RUST_DATA_FILE, default={
        "guilds": {},       # guild_id: {"main_server": str, "raid_role_id": int}
        "targets": {}       # guild_id: {target_id: {"name": str, "grid": str, "boom": str, "notes": str, "author": str}}
    })

def save_rust_data(data: Dict[str, Any]):
    save_json(RUST_DATA_FILE, data)


# ==========================================
# CONSTANTS & RAID DATA (EMOJI MATCHED)
# ==========================================

RAID_TABLE: Dict[str, Dict[str, Any]] = {
    "Wooden Door": {"rockets": 1, "c4": 1, "satchels": 2, "explo": 18, "hp": 200, "emoji": "🪵"},
    "Sheet Metal Door": {"rockets": 2, "c4": 1, "satchels": 4, "explo": 63, "hp": 250, "emoji": "⚙️"},
    "Garage Door": {"rockets": 3, "c4": 2, "satchels": 9, "explo": 150, "hp": 600, "emoji": "🚪"},
    "Armored Door": {"rockets": 4, "c4": 2, "satchels": 12, "explo": 200, "hp": 800, "emoji": "🛡️"},
    "Ladder Hatch": {"rockets": 3, "c4": 2, "satchels": 9, "explo": 150, "hp": 250, "emoji": "🪜"},
    "Wood Wall / Foundation": {"rockets": 2, "c4": 1, "satchels": 3, "explo": 49, "hp": 250, "emoji": "🪵"},
    "Stone Wall / Foundation": {"rockets": 4, "c4": 2, "satchels": 10, "explo": 185, "hp": 500, "emoji": "🪨"},
    "Sheet Metal Wall": {"rockets": 8, "c4": 4, "satchels": 23, "explo": 400, "hp": 1000, "emoji": "⚙️"},
    "Armored Wall / Foundation": {"rockets": 15, "c4": 8, "satchels": 46, "explo": 799, "hp": 2000, "emoji": "🛡️"},
    "High External Wood Wall": {"rockets": 3, "c4": 2, "satchels": 6, "explo": 98, "hp": 500, "emoji": "🪵"},
    "High External Stone Wall": {"rockets": 4, "c4": 2, "satchels": 10, "explo": 185, "hp": 500, "emoji": "🪨"},
    "Auto Turret": {"rockets": 1, "c4": 1, "satchels": 3, "explo": 40, "hp": 1000, "emoji": "🔫"},
    "SAM Site": {"rockets": 1, "c4": 1, "satchels": 4, "explo": 40, "hp": 1000, "emoji": "🚀"},
    "Reinforced Glass Window": {"rockets": 3, "c4": 2, "satchels": 9, "explo": 150, "hp": 500, "emoji": "🖼️"},
    "Sheet Metal Floor Grill": {"rockets": 3, "c4": 2, "satchels": 9, "explo": 150, "hp": 250, "emoji": "🏁"}
}

BOOM_COSTS = {
    "rocket": {"sulfur": 1400, "charcoal": 1950, "metal": 100, "pipes": 2, "lowgrade": 30, "gp": 650, "explosives": 10},
    "c4": {"sulfur": 2200, "charcoal": 3000, "metal": 200, "cloth": 5, "lowgrade": 60, "techtrash": 2, "gp": 1000, "explosives": 20},
    "satchel": {"sulfur": 480, "charcoal": 720, "metal": 80, "cloth": 10, "rope": 1, "gp": 240, "beancans": 4},
    "explo_556": {"sulfur": 25, "charcoal": 30, "metal": 10, "gp": 10}
}

def get_rce_wipe_timestamp(wipe_type: str = "weekly") -> int:
    now = datetime.now(timezone.utc)
    if wipe_type == "weekly":
        days_ahead = (3 - now.weekday()) % 7
        target_date = now.date() + timedelta(days=days_ahead)
        target_time = datetime(target_date.year, target_date.month, target_date.day, 18, 0, 0, tzinfo=timezone.utc)
        if target_time <= now:
            target_time += timedelta(days=7)
        return int(target_time.timestamp())
    else:
        year, month = now.year, now.month
        def last_thu(y, m):
            next_m = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
            last_day = next_m - timedelta(days=1)
            offset = (last_day.weekday() - 3) % 7
            d = last_day - timedelta(days=offset)
            return datetime(d.year, d.month, d.day, 18, 0, 0, tzinfo=timezone.utc)
        t = last_thu(year, month)
        if t <= now:
            t = last_thu(year + 1, 1) if month == 12 else last_thu(year, month + 1)
        return int(t.timestamp())


# ==========================================
# 1. INTERACTIVE RAID CALCULATOR UI
# ==========================================

class RaidCalcView(View):
    def __init__(self, selected_structure: str = "Garage Door", quantity: int = 1):
        super().__init__(timeout=180)
        self.selected_structure = selected_structure
        self.quantity = quantity
        self.update_components()

    def update_components(self):
        self.clear_items()

        # Row 0: Target Dropdown with Rust Emojis
        options = []
        for name, data in RAID_TABLE.items():
            options.append(discord.SelectOption(
                label=name,
                value=name,
                default=(name == self.selected_structure),
                emoji=data["emoji"]
            ))

        select = Select(placeholder="🎯 Select target wall or door...", options=options, row=0)
        select.callback = self.on_select_structure
        self.add_item(select)

        # Row 1: Quantity Steppers
        b_sub1 = Button(label="-1", style=discord.ButtonStyle.secondary, emoji="➖", row=1)
        b_sub1.callback = lambda i: self.adjust_qty(i, -1)
        self.add_item(b_sub1)

        b_add1 = Button(label="+1", style=discord.ButtonStyle.primary, emoji="➕", row=1)
        b_add1.callback = lambda i: self.adjust_qty(i, 1)
        self.add_item(b_add1)

        b_add4 = Button(label="+4", style=discord.ButtonStyle.primary, row=1)
        b_add4.callback = lambda i: self.adjust_qty(i, 4)
        self.add_item(b_add4)

        b_add10 = Button(label="+10", style=discord.ButtonStyle.primary, row=1)
        b_add10.callback = lambda i: self.adjust_qty(i, 10)
        self.add_item(b_add10)

        b_reset = Button(label="Reset", style=discord.ButtonStyle.danger, emoji="🔄", row=1)
        b_reset.callback = self.on_reset
        self.add_item(b_reset)

        # Row 2: Custom input + Return
        b_custom = Button(label="Set Custom Qty", style=discord.ButtonStyle.secondary, emoji="🔢", row=2)
        b_custom.callback = self.on_custom_qty
        self.add_item(b_custom)

        b_home = Button(label="Return to Hub", style=discord.ButtonStyle.success, emoji="🏠", row=2)
        b_home.callback = self.on_return_hub
        self.add_item(b_home)

    def build_embed(self) -> discord.Embed:
        data = RAID_TABLE[self.selected_structure]
        qty = self.quantity

        r_qty = data["rockets"] * qty
        c_qty = data["c4"] * qty
        s_qty = data["satchels"] * qty
        e_qty = data["explo"] * qty

        r_sulfur = r_qty * BOOM_COSTS["rocket"]["sulfur"]
        c_sulfur = c_qty * BOOM_COSTS["c4"]["sulfur"]
        s_sulfur = s_qty * BOOM_COSTS["satchel"]["sulfur"]
        e_sulfur = e_qty * BOOM_COSTS["explo_556"]["sulfur"]

        embed = discord.Embed(
            title=f"💥 Boom Cost: {qty}x {self.selected_structure}",
            description=f"🧱 **Target HP:** `{data['hp'] * qty:,}` Total HP\n"
                        f"Select targets and scale quantities instantly using the tools below.",
            color=discord.Color.dark_red()
        )

        embed.add_field(
            name="🚀 Rockets",
            value=f"• **{r_qty:,}** Rockets\n• 🟨 `{r_sulfur:,}` Sulfur\n• ⚙️ `{r_qty * 2:,}` Pipes",
            inline=True
        )
        embed.add_field(
            name="🧨 Timed C4",
            value=f"• **{c_qty:,}** C4\n• 🟨 `{c_sulfur:,}` Sulfur\n• 💻 `{c_qty * 2:,}` Tech Trash",
            inline=True
        )
        embed.add_field(
            name="💣 Satchels",
            value=f"• **{s_qty:,}** Satchels\n• 🟨 `{s_sulfur:,}` Sulfur\n• 🧵 `{s_qty * 10:,}` Cloth",
            inline=True
        )
        embed.add_field(
            name="🔫 Explosive 5.56",
            value=f"• **{e_qty:,}** Rounds\n• 🟨 `{e_sulfur:,}` Sulfur\n• 🛠️ `~{math.ceil(e_qty / 128)}` Guns",
            inline=True
        )
        embed.add_field(
            name="🔥 Eco Raid Combo Recommendation",
            value=f"👉 Use **{max(1, r_qty - 1)} Rockets** + **{data['explo'] // 2} Explo Ammo** to cleanly splash open structures and finish soft sides.",
            inline=False
        )

        embed.set_footer(text="Rust Console Edition Accurate Values")
        return embed

    async def on_select_structure(self, interaction: discord.Interaction):
        self.selected_structure = interaction.data["values"][0]
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def adjust_qty(self, interaction: discord.Interaction, delta: int):
        self.quantity = max(1, self.quantity + delta)
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_reset(self, interaction: discord.Interaction):
        self.quantity = 1
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_custom_qty(self, interaction: discord.Interaction):
        modal = CustomQuantityModal(self)
        await interaction.response.send_modal(modal)

    async def on_return_hub(self, interaction: discord.Interaction):
        view = RustMainHubView()
        await interaction.response.edit_message(embed=view.build_hub_embed(interaction.guild), view=view)


class CustomQuantityModal(Modal, title="Set Custom Quantity"):
    qty_input = TextInput(label="Enter Quantity", placeholder="e.g. 15", min_length=1, max_length=4, required=True)

    def __init__(self, parent_view: RaidCalcView):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.qty_input.value.strip())
            self.parent_view.quantity = max(1, val)
        except ValueError:
            pass
        self.parent_view.update_components()
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


# ==========================================
# 2. INTERACTIVE CRAFTING CONVERTER UI
# ==========================================

class CraftConverterView(View):
    def __init__(self, selected_item: str = "Rockets", quantity: int = 10):
        super().__init__(timeout=180)
        self.selected_item = selected_item
        self.quantity = quantity
        self.update_components()

    def update_components(self):
        self.clear_items()

        options = [
            discord.SelectOption(label="Rockets", value="Rockets", default=(self.selected_item == "Rockets"), emoji="🚀"),
            discord.SelectOption(label="Timed C4", value="Timed C4", default=(self.selected_item == "Timed C4"), emoji="🧨"),
            discord.SelectOption(label="Satchel Charges", value="Satchels", default=(self.selected_item == "Satchels"), emoji="💣"),
            discord.SelectOption(label="Explosive 5.56 (x100)", value="Explo 5.56", default=(self.selected_item == "Explo 5.56"), emoji="🔫"),
        ]
        select = Select(placeholder="Choose explosive item...", options=options, row=0)
        select.callback = self.on_select_item
        self.add_item(select)

        # Quantity Steppers
        for val in [-5, 5, 10, 20]:
            label = f"{val}" if val < 0 else f"+{val}"
            b = Button(label=label, style=discord.ButtonStyle.primary if val > 0 else discord.ButtonStyle.secondary, row=1)
            b.callback = lambda i, v=val: self.adjust_qty(i, v)
            self.add_item(b)

        b_home = Button(label="Return to Hub", style=discord.ButtonStyle.success, emoji="🏠", row=2)
        b_home.callback = self.on_return_hub
        self.add_item(b_home)

    def build_embed(self) -> discord.Embed:
        qty = self.quantity
        embed = discord.Embed(
            title=f"🟨 Craft Cost: {qty:,}x {self.selected_item}",
            description="Complete raw materials, gunpowder, and component crafting cost breakdown:",
            color=discord.Color.gold()
        )

        if self.selected_item == "Rockets":
            c = BOOM_COSTS["rocket"]
            embed.add_field(name="🟨 Raw Sulfur", value=f"`{c['sulfur'] * qty:,}`", inline=True)
            embed.add_field(name="⬛ Raw Charcoal", value=f"`{c['charcoal'] * qty:,}`", inline=True)
            embed.add_field(name="💨 Gunpowder (GP)", value=f"`{c['gp'] * qty:,}`", inline=True)
            embed.add_field(name="🧨 Explosives", value=f"`{c['explosives'] * qty:,}`", inline=True)
            embed.add_field(name="⚙️ Metal Pipes", value=f"`{c['pipes'] * qty:,}`", inline=True)
            embed.add_field(name="🛢️ Low Grade", value=f"`{c['lowgrade'] * qty:,}`", inline=True)

        elif self.selected_item == "Timed C4":
            c = BOOM_COSTS["c4"]
            embed.add_field(name="🟨 Raw Sulfur", value=f"`{c['sulfur'] * qty:,}`", inline=True)
            embed.add_field(name="⬛ Raw Charcoal", value=f"`{c['charcoal'] * qty:,}`", inline=True)
            embed.add_field(name="💨 Gunpowder (GP)", value=f"`{c['gp'] * qty:,}`", inline=True)
            embed.add_field(name="🧨 Explosives", value=f"`{c['explosives'] * qty:,}`", inline=True)
            embed.add_field(name="💻 Tech Trash", value=f"`{c['techtrash'] * qty:,}`", inline=True)
            embed.add_field(name="🧵 Cloth", value=f"`{c['cloth'] * qty:,}`", inline=True)

        elif self.selected_item == "Satchels":
            c = BOOM_COSTS["satchel"]
            embed.add_field(name="🟨 Raw Sulfur", value=f"`{c['sulfur'] * qty:,}`", inline=True)
            embed.add_field(name="⬛ Raw Charcoal", value=f"`{c['charcoal'] * qty:,}`", inline=True)
            embed.add_field(name="💨 Gunpowder (GP)", value=f"`{c['gp'] * qty:,}`", inline=True)
            embed.add_field(name="🥫 Beancans", value=f"`{c['beancans'] * qty:,}`", inline=True)
            embed.add_field(name="🧵 Rope & Cloth", value=f"`{qty:,}` Rope / `{qty * 10:,}` Cloth", inline=True)
            embed.add_field(name="⚙️ Metal Frags", value=f"`{qty * 80:,}`", inline=True)

        else: # Explo 5.56
            rounds = qty * 100
            embed.add_field(name="🔫 Total Rounds", value=f"`{rounds:,}` Rounds", inline=True)
            embed.add_field(name="🟨 Raw Sulfur", value=f"`{rounds * 25:,}`", inline=True)
            embed.add_field(name="⬛ Charcoal", value=f"`{rounds * 30:,}`", inline=True)
            embed.add_field(name="💨 Gunpowder", value=f"`{rounds * 10:,}`", inline=True)
            embed.add_field(name="⚙️ Metal Frags", value=f"`{rounds * 10:,}`", inline=True)

        embed.set_footer(text="Scale quantities up/down using the dynamic layout")
        return embed

    async def on_select_item(self, interaction: discord.Interaction):
        self.selected_item = interaction.data["values"][0]
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def adjust_qty(self, interaction: discord.Interaction, delta: int):
        self.quantity = max(1, self.quantity + delta)
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_return_hub(self, interaction: discord.Interaction):
        view = RustMainHubView()
        await interaction.response.edit_message(embed=view.build_hub_embed(interaction.guild), view=view)


# ==========================================
# 3. EMERGENCY RAID ALERT MODAL & LAUNCHER
# ==========================================

class RaidAlertModal(Modal, title="🚨 Sound Emergency Raid Alarm"):
    grid = TextInput(label="Map Grid Coordinate", placeholder="e.g. M14 or G7", min_length=2, max_length=5, required=True)
    situation = TextInput(
        label="Intel & Situation Details",
        style=discord.TextStyle.paragraph,
        placeholder="e.g., 4 deep, shooting core with rockets, high wall compound breached!",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        data = get_rust_data()
        g_id = str(interaction.guild_id)

        role_id = data["guilds"].get(g_id, {}).get("raid_role_id")
        ping = f"<@&{role_id}>" if role_id else "@everyone"
        server = data["guilds"].get(g_id, {}).get("main_server", "Official Server")

        embed = discord.Embed(
            title="🚨🚨 CLAN BASE UNDER RAID ALERT 🚨🚨",
            description=f"🗺️ **GRID COORDINATE:** `{self.grid.value.upper()}`\n"
                        f"🎮 **SERVER:** `{server}`\n"
                        f"👤 **CALLER:** {interaction.user.mention}\n\n"
                        f"⚠️ **TACTICAL INTEL:**\n{self.situation.value}",
            color=discord.Color.dark_red()
        )
        embed.set_footer(text="CONSOLE CLAN OPERATIONS CENTER • GET ON IMMEDIATELY")
        await interaction.channel.send(content=f"⚠️ {ping} **CLAN BASE ACTIVE RAID ALERT IN GRID {self.grid.value.upper()}!**", embed=embed)


# ==========================================
# 4. ENEMY TARGET DOSSIER MODALS
# ==========================================

class AddTargetModal(Modal, title="Scout Enemy Raid Target"):
    target_name = TextInput(label="Target Clan / Base Name", placeholder="e.g. OT Clan Main Base", required=True)
    grid = TextInput(label="Map Grid", placeholder="e.g. L14", min_length=2, max_length=5, required=True)
    boom_estimate = TextInput(label="Boom Estimate to Core", placeholder="e.g. 24 Rockets or 12 C4", required=True)
    notes = TextInput(
        label="Defenses & Scouting Intel",
        style=discord.TextStyle.paragraph,
        placeholder="Active compound auto-turrets, 2 external TCs, main players are EU timezone",
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        data = get_rust_data()
        g_id = str(interaction.guild_id)
        targets = data["targets"].setdefault(g_id, {})

        tid = str(len(targets) + 1)
        targets[tid] = {
            "name": self.target_name.value,
            "grid": self.grid.value.upper(),
            "boom": self.boom_estimate.value,
            "notes": self.notes.value or "None provided",
            "author": interaction.user.display_name
        }
        save_rust_data(data)

        embed = discord.Embed(
            title=f"🎯 Target #{tid} Logged: {self.target_name.value}",
            description=f"🗺️ **Grid:** `{self.grid.value.upper()}`\n💥 **Boom Needed:** `{self.boom_estimate.value}`\n📝 **Notes:** {self.notes.value or 'None'}",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Logged by {interaction.user.display_name} • View all with /rust")
        await interaction.response.send_message(embed=embed)


# ==========================================
# 5. MASTER RUST COMMAND CENTER HUB VIEW
# ==========================================

class RustMainHubView(View):
    def __init__(self):
        super().__init__(timeout=None)

    def build_hub_embed(self, guild: Optional[discord.Guild] = None) -> discord.Embed:
        w_ts = get_rce_wipe_timestamp("weekly")
        m_ts = get_rce_wipe_timestamp("monthly")

        embed = discord.Embed(
            title="🎮 Rust Console Operations Hub",
            description="Manage your raid targets, convert sulfur pools, calculate boom costs, and sound emergency raid sirens dynamically below.",
            color=discord.Color.purple()
        )
        embed.add_field(
            name="💥 Raid Calculators",
            value="• **Raid Calc:** Wall & door boom tables\n• **Craft Cost:** Raw sulfur converter",
            inline=True
        )
        embed.add_field(
            name="🗺️ Timers & Intel",
            value=f"• **Weekly Wipe:** <t:{w_ts}:R>\n• **Monthly Wipe:** <t:{m_ts}:R>",
            inline=True
        )
        embed.add_field(
            name="🚨 Operational Combat Tools",
            value="• **Sound Alarm:** Notify clan during raids\n• **Dossier:** Manage enemy base scouting reports",
            inline=False
        )
        embed.set_footer(text="PermBot RCE System • Interactive Master Dashboard")
        return embed

    @discord.ui.button(label="Raid Calculator", emoji="💥", style=discord.ButtonStyle.primary, row=0, custom_id="hub:raidcalc")
    async def on_raidcalc(self, interaction: discord.Interaction, button: Button):
        view = RaidCalcView()
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="Boom Craft Cost", emoji="🟨", style=discord.ButtonStyle.primary, row=0, custom_id="hub:craft")
    async def on_craft(self, interaction: discord.Interaction, button: Button):
        view = CraftConverterView()
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="Sound Raid Alarm", emoji="🚨", style=discord.ButtonStyle.danger, row=1, custom_id="hub:raidalert")
    async def on_raidalert(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(RaidAlertModal())

    @discord.ui.button(label="Scout Base Target", emoji="🎯", style=discord.ButtonStyle.secondary, row=1, custom_id="hub:target_add")
    async def on_target_add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddTargetModal())

    @discord.ui.button(label="View Target Dossier", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="hub:target_list")
    async def on_target_list(self, interaction: discord.Interaction, button: Button):
        data = get_rust_data()
        targets = data["targets"].get(str(interaction.guild_id), {})
        if not targets:
            return await interaction.response.send_message("📂 No raid targets are currently recorded in the dossier.", ephemeral=True)

        embed = discord.Embed(title="🎯 Active Enemy Raid Target Dossier", color=discord.Color.dark_red())
        for tid, t in targets.items():
            embed.add_field(
                name=f"#{tid} • {t['name']} (Grid: `{t['grid']}`)",
                value=f"💥 **Estimated Boom:** `{t['boom']}`\n📝 **Notes:** {t['notes']}\n👤 *Scouted by {t.get('author', 'Member')}*",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==========================================
# RUST COG & COMMAND HANDLERS
# ==========================================

class Rust(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(RustMainHubView())

    # ---------------------------------------------------------
    # MAIN HUB COMMAND
    # ---------------------------------------------------------
    @commands.hybrid_command(name="rust", description="Launch the interactive Rust Console Clan Hub.")
    async def rust(self, ctx: commands.Context):
        view = RustMainHubView()
        embed = view.build_hub_embed(ctx.guild)
        await ctx.send(embed=embed, view=view)

    # ---------------------------------------------------------
    # HYBRID INTERACTIVE COMMANDS
    # ---------------------------------------------------------
    @commands.hybrid_command(name="raidcalc", description="Dynamic door, wall & boom calculator.")
    async def raidcalc(self, ctx: commands.Context):
        view = RaidCalcView()
        await ctx.send(embed=view.build_embed(), view=view)

    @commands.hybrid_command(name="craft", description="Calculate sulfur & GP craft costs dynamically.")
    async def craft(self, ctx: commands.Context):
        view = CraftConverterView()
        await ctx.send(embed=view.build_embed(), view=view)

    @commands.hybrid_command(name="raidalert", description="Launch the emergency base raid alarm.")
    async def raidalert(self, ctx: commands.Context):
        if ctx.interaction:
            await ctx.interaction.response.send_modal(RaidAlertModal())
        else:
            await ctx.send("💡 Please use `/raidalert` to sound the alarm via interactive pop-up.")

    @commands.hybrid_command(name="rcewipe", description="Show official D11 wipe schedules and times.")
    async def rcewipe(self, ctx: commands.Context):
        w_ts = get_rce_wipe_timestamp("weekly")
        m_ts = get_rce_wipe_timestamp("monthly")
        embed = discord.Embed(
            title="🗓️ Official RCE Wipe Timers",
            description=f"**📅 Weekly Wipe:** <t:{w_ts}:F> (<t:{w_ts}:R>)\n"
                        f"**🗓️ Monthly Wipe:** <t:{m_ts}:F> (<t:{m_ts}:R>)\n\n"
                        f"🌐 *Wipes occur every Thursday simultaneously at 18:00 UTC (2:00 PM ET / 11:00 AM PT)*",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="rcesetup", description="Configure main RCE server and raid alert ping role.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(
        main_server="Name of the main server your clan plays on",
        raid_role="Role to ping during emergency raid alerts (optional)"
    )
    async def rcesetup(self, ctx: commands.Context, main_server: str, raid_role: Optional[discord.Role] = None):
        data = get_rust_data()
        g_id = str(ctx.guild.id)

        data["guilds"].setdefault(g_id, {})
        data["guilds"][g_id]["main_server"] = main_server
        if raid_role:
            data["guilds"][g_id]["raid_role_id"] = raid_role.id
        save_rust_data(data)

        role_str = raid_role.mention if raid_role else "@everyone"
        await ctx.send(f"✅ RCE Clan settings updated!\n• **Main Server:** `{main_server}`\n• **Raid Ping Role:** {role_str}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Rust(bot))
