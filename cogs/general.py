import time
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Select
from typing import Optional, Dict, Any, List

# ==========================================
# CATEGORY DEFINITIONS & COMMAND METADATA
# ==========================================

HELP_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "music": {
        "label": "Music & Playback",
        "emoji": "🎵",
        "description": "Voice playback, queue controls, seek & DSP audio filters",
        "admin_only": False,
        "commands": [
            ("!play / /play <query|file>", "Play a local track, search query, or attached audio/video file (up to 50MB)."),
            ("!playnext / /playnext <query|file>", "Insert a track or attachment to play immediately after current song."),
            ("!skip / /skip", "Skip the currently playing track to the next song in queue."),
            ("!pause / /pause", "Pause current audio playback."),
            ("!resume / /resume", "Resume paused audio playback."),
            ("!stop / /stop", "Stop playback, clear queue, and disconnect bot from voice."),
            ("!seek / /seek <timestamp>", "Seek to a timestamp (e.g. `1:30` or `90`). Dropdown is also in Player UI."),
            ("!nowplaying / /nowplaying (!np)", "Display rich player embed with progress bar, DSP, and status."),
            ("!queue / /queue", "View all tracks waiting in the current guild queue."),
            ("!shuffle / /shuffle [scope]", "Shuffle active queue, all library (`all`), or a specific folder name."),
            ("!loop / /loop [mode]", "Toggle or set loop mode (`off`, `track`, `queue`)."),
            ("!clear / /clear", "Clear all waiting tracks from the queue."),
            ("!remove / /remove <index>", "Remove a specific track by its position number in queue.")
        ]
    },
    "library": {
        "label": "Library, Playlists & Lyrics",
        "emoji": "📂",
        "description": "Local library explorer, playlists, and LRCLIB lyrics",
        "admin_only": False,
        "commands": [
            ("!library / /library [folder]", "Browse tracks stored in the bot's local audio library."),
            ("!folders / /folders", "List all category folders and track counts in the music directory."),
            ("!search / /search <query>", "Search for local tracks matching a keyword or title."),
            ("!lyrics / /lyrics [title]", "Fetch synced/plain lyrics from LRCLIB for current or queried track."),
            ("!playlist create <name>", "Create a new saved custom playlist."),
            ("!playlist play <name>", "Load and play all tracks from a saved custom playlist."),
            ("!playlist add <name> <song>", "Add a song to a saved custom playlist."),
            ("!playlist list", "View all created custom playlists."),
            ("!playlist delete <name>", "Delete a saved custom playlist.")
        ]
    },
    "applications": {
        "label": "Rust Clan Applications",
        "emoji": "📋",
        "description": "Recruitment panel, resume modals & applicant review tickets",
        "admin_only": True,
        "commands": [
            ("!apppanel / /apppanel [channel]", "Deploy the persistent 'Apply / Resume' panel button in a channel."),
            ("!appsetstaff / /appsetstaff <@role>", "Set the staff/reviewer role permitted to review application tickets."),
            ("!appsetrole / /appsetrole <@role>", "Set the recruit/member role granted automatically upon ticket acceptance."),
            ("!appclose / /appclose", "Close and delete the current application review ticket.")
        ]
    },
    "tickets": {
        "label": "Support Ticket System",
        "emoji": "🎫",
        "description": "General server support tickets and transcript management",
        "admin_only": True,
        "commands": [
            ("!ticketpanel / /ticketpanel [channel]", "Deploy the interactive persistent support ticket panel button."),
            ("!ticketclose / /ticketclose", "Close, archive, and delete the active support ticket channel."),
            ("!ticketadd / /ticketadd <@user>", "Add a member to the current private ticket channel."),
            ("!ticketremove / /ticketremove <@user>", "Remove a member from the current private ticket channel.")
        ]
    },
    "moderation": {
        "label": "Moderation & Voice Controls",
        "emoji": "🛡️",
        "description": "Server moderation, chat locks, timeouts, and voice control",
        "admin_only": True,
        "commands": [
            ("!mute / /mute <@user>", "Server Voice Mute targeted member in voice channels."),
            ("!unmute / /unmute <@user>", "Remove Server Voice Mute from targeted member."),
            ("!deaf / /deaf <@user>", "Server Voice Deafen targeted member in voice channels."),
            ("!undeaf / /undeaf <@user>", "Remove Server Voice Deafen from targeted member."),
            ("!kys / /kys [@user]", "Disconnect yourself from voice (or disconnect target user if admin)."),
            ("!lock / /lock [#channel]", "Lock channel (disables message and thread creation for @everyone)."),
            ("!unlock / /unlock [#channel]", "Unlock channel (restores default @everyone overrides)."),
            ("!timeout / /timeout <@user> [dur]", "Mute user in text chat (defaults to 10 minutes if omitted)."),
            ("!untimeout / /untimeout <@user>", "Remove active chat timeout from a member."),
            ("!kick / /kick <@user> [reason]", "Kick a member from the server."),
            ("!ban / /ban <@user> [reason]", "Ban a member from the server."),
            ("!unban / /unban <user_id>", "Unban a member by their Discord User ID."),
            ("!purge / /purge <amount>", "Bulk delete specified number of messages in channel."),
            ("!warn / /warn <@user> <reason>", "Issue a recorded warning to a member."),
            ("!warnings / /warnings <@user>", "View warning history for a member."),
            ("!clearwarns / /clearwarns <@user>", "Clear all active warnings for a member.")
        ]
    },
    "roles": {
        "label": "Roles & Panels",
        "emoji": "🎭",
        "description": "Interactive DM role panel builder and admin role assignment",
        "admin_only": True,
        "commands": [
            ("!rolepanel / /rolepanel", "Launch the interactive DM setup wizard to build custom reaction/button role panels."),
            ("!role / /role <@user> <@role>", "Manually assign, remove, or toggle a role on a member.")
        ]
    },
    "tempvoice": {
        "label": "Temporary Voice Hub",
        "emoji": "🔊",
        "description": "Dynamic 'Join to Create' temporary voice channels",
        "admin_only": True,
        "commands": [
            ("!tempvoicesetup / /tempvoicesetup", "Deploy the 'Join to Create' voice generator hub.")
        ]
    },
    "utility": {
        "label": "Utility & Member Commands",
        "emoji": "⚙️",
        "description": "General tools, AFK responder, QR generator, polls, and info",
        "admin_only": False,
        "commands": [
            ("!afk / /afk [reason]", "Set AFK status. Notifies users who mention you; clears on your next message."),
            ("!ping / /ping", "Check bot latency and Discord API heartbeat."),
            ("!userinfo / /userinfo [@user]", "Display user account info, creation date, join date, and roles."),
            ("!serverinfo / /serverinfo", "Display guild statistics, member count, boost level, and features."),
            ("!avatar / /avatar [@user]", "Display high-resolution avatar of a member."),
            ("!qrcode / /qrcode <text_or_url>", "Generate a dynamic scannable QR code image."),
            ("!poll / /poll <question> | <opt1> | <opt2>", "Create an interactive reaction-based poll.")
        ]
    },
    "admin": {
        "label": "Admin, Security & System",
        "emoji": "🔧",
        "description": "Diagnostics, anti-raid security, backups, and 24/7 mode",
        "admin_only": True,
        "commands": [
            ("!247 / /247", "Toggle 24/7 voice persistence in current server."),
            ("!stats / /stats", "View hardware telemetry (CPU, RAM, Uptime, Python version, Latency)."),
            ("!antiraid / /antiraid [on|off]", "Configure automatic join-burst raid mitigation."),
            ("!backup / /backup", "Generate a compressed zip backup of bot data stores.")
        ]
    }
}


# ==========================================
# HELP SELECT MENU VIEW
# ==========================================

class HelpCategorySelect(Select):
    def __init__(self, is_admin: bool):
        self.is_admin = is_admin
        options = []

        for key, data in HELP_CATEGORIES.items():
            if data["admin_only"] and not is_admin:
                continue
            options.append(
                discord.SelectOption(
                    label=data["label"],
                    value=key,
                    description=data["description"][:100],
                    emoji=data["emoji"]
                )
            )

        super().__init__(
            placeholder="📖 Select a category to view commands...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        chosen_key = self.values[0]
        cat_data = HELP_CATEGORIES.get(chosen_key)

        if not cat_data:
            return await interaction.response.send_message("❌ Category not found.", ephemeral=True)

        embed = discord.Embed(
            title=f"{cat_data['emoji']} {cat_data['label']} Commands",
            description=f"*{cat_data['description']}*\n\n",
            color=discord.Color.purple()
        )

        for cmd_syntax, cmd_desc in cat_data["commands"]:
            embed.add_field(
                name=f"`{cmd_syntax}`",
                value=cmd_desc,
                inline=False
            )

        embed.set_footer(text="Tip: Commands support both !prefix and /slash syntax • PermBot")
        await interaction.response.edit_message(embed=embed)


class HelpView(View):
    def __init__(self, is_admin: bool):
        super().__init__(timeout=120)
        self.add_item(HelpCategorySelect(is_admin=is_admin))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ==========================================
# GENERAL COG & HELP COMMANDS
# ==========================================

class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def is_user_admin(self, ctx_or_interaction) -> bool:
        if isinstance(ctx_or_interaction, commands.Context):
            return ctx_or_interaction.author.guild_permissions.administrator if ctx_or_interaction.guild else False
        elif isinstance(ctx_or_interaction, discord.Interaction):
            return ctx_or_interaction.user.guild_permissions.administrator if ctx_or_interaction.guild else False
        return False

    @commands.hybrid_command(name="help", description="View the PermBot command menu and documentation.")
    @app_commands.describe(command_name="Specific command or category to look up")
    async def help_command(self, ctx: commands.Context, command_name: Optional[str] = None):
        is_admin = self.is_user_admin(ctx)

        # If specific command/category is queried
        if command_name:
            query = command_name.lower().strip("!/")

            # Check if query matches a category
            if query in HELP_CATEGORIES:
                cat_data = HELP_CATEGORIES[query]
                if cat_data["admin_only"] and not is_admin:
                    return await ctx.send("❌ You do not have permission to view administrator command categories.", ephemeral=True)

                embed = discord.Embed(
                    title=f"{cat_data['emoji']} {cat_data['label']} Commands",
                    description=f"*{cat_data['description']}*\n",
                    color=discord.Color.purple()
                )
                for cmd_syntax, cmd_desc in cat_data["commands"]:
                    embed.add_field(name=f"`{cmd_syntax}`", value=cmd_desc, inline=False)
                return await ctx.send(embed=embed)

            # Check if query matches an individual command
            for cat_key, cat_data in HELP_CATEGORIES.items():
                if cat_data["admin_only"] and not is_admin:
                    continue
                for cmd_syntax, cmd_desc in cat_data["commands"]:
                    if query in cmd_syntax.lower():
                        embed = discord.Embed(
                            title=f"📖 Command Help: `{cmd_syntax}`",
                            description=cmd_desc,
                            color=discord.Color.purple()
                        )
                        embed.add_field(name="Category", value=f"{cat_data['emoji']} {cat_data['label']}", inline=True)
                        embed.add_field(name="Syntax", value=f"`!{query}` or `/{query}`", inline=True)
                        return await ctx.send(embed=embed)

            return await ctx.send(f"❌ Command or category `{command_name}` was not found.", ephemeral=True)

        # Main Overview Embed
        embed = discord.Embed(
            title="🛡️ PermBot Command Central",
            description="Welcome to **PermBot**! Select a category from the dropdown menu below to view detailed command lists and syntax.\n\n"
                        "💡 **Quick Info:**\n"
                        "• All commands support both prefix (`!`) and slash (`/`) execution.\n"
                        "• Audio player includes a 4-row sticky control dashboard, DSP presets, and live seek dropdown.\n"
                        "• Upload any audio or video file up to 50MB directly with `!play`.",
            color=discord.Color.purple()
        )

        for cat_key, cat_data in HELP_CATEGORIES.items():
            if cat_data["admin_only"] and not is_admin:
                continue
            embed.add_field(
                name=f"{cat_data['emoji']} {cat_data['label']}",
                value=f"{cat_data['description']}\n*({len(cat_data['commands'])} commands)*",
                inline=True
            )

        embed.set_footer(text="Select a category below to explore • PermBot")
        view = HelpView(is_admin=is_admin)
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="ping", description="Check bot latency and Discord API heartbeat.")
    async def ping(self, ctx: commands.Context):
        start = time.perf_counter()
        msg = await ctx.send("🏓 Pinging API...")
        end = time.perf_counter()

        ws_ping = round(self.bot.latency * 1000)
        round_trip = round((end - start) * 1000)

        embed = discord.Embed(
            title="🏓 Pong!",
            color=discord.Color.green() if ws_ping < 120 else discord.Color.gold()
        )
        embed.add_field(name="API Heartbeat", value=f"`{ws_ping}ms`", inline=True)
        embed.add_field(name="Roundtrip Latency", value=f"`{round_trip}ms`", inline=True)
        await msg.edit(content=None, embed=embed)


async def setup(bot: commands.Bot):
    # Remove default help command to prevent collisions
    bot.help_command = None
    await bot.add_cog(General(bot))
