import discord
from discord.ext import commands
import asyncio
import time
import os
import logging
from collections import deque
from typing import Dict, Optional
from utils.helpers import load_json, save_json, delete_after_delay

logger = logging.getLogger("DiscordBot.AntiRaid")
ANTIRAID_PATH = os.path.join("data", "antiraid.json")
MODCONFIG_PATH = os.path.join("data", "modconfig.json")


class AntiRaid(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.join_trackers: Dict[int, deque] = {}  # guild_id: deque([timestamps])

    def get_config(self, guild_id: int) -> dict:
        data = load_json(ANTIRAID_PATH, {})
        default_config = {
            "enabled": True,
            "join_threshold": 5,        # 5 joins
            "window_seconds": 10,       # in 10 seconds
            "action": "kick",           # "kick" or "alert"
            "shield_active": False,
            "min_account_age_days": 3   # Auto-flag accounts < 3 days old
        }
        return data.get(str(guild_id), default_config)

    def save_config(self, guild_id: int, cfg: dict):
        data = load_json(ANTIRAID_PATH, {})
        data[str(guild_id)] = cfg
        save_json(ANTIRAID_PATH, data)

    async def _send_mod_alert(self, guild: discord.Guild, embed: discord.Embed):
        """Sends an urgent raid alert to the configured mod-log channel."""
        mod_data = load_json(MODCONFIG_PATH, {})
        channel_id = mod_data.get(str(guild.id), {}).get("modlog_channel")
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                await channel.send(content="🚨 **@here URGENT: ANTI-RAID SECURITY ALERT**", embed=embed)
                return
        if guild.system_channel:
            await guild.system_channel.send(content="🚨 **ANTI-RAID SECURITY ALERT**", embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        cfg = self.get_config(guild.id)
        if not cfg.get("enabled", True):
            return

        now = time.time()

        # 1. Account Age Gate Check
        min_age_days = cfg.get("min_account_age_days", 0)
        if min_age_days > 0:
            account_age_days = (now - member.created_at.timestamp()) / 86400
            if account_age_days < min_age_days:
                alert = discord.Embed(
                    title="⚠️ Suspicious New Account Joined",
                    description=f"{member.mention} (`{member.name}` - ID: `{member.id}`) joined but their account is younger than `{min_age_days}` days.",
                    color=discord.Color.gold()
                )
                alert.add_field(name="Account Age", value=f"**{account_age_days:.1f} days old**", inline=True)
                alert.add_field(name="Created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
                asyncio.create_task(self._send_mod_alert(guild, alert))

        # 2. Join Flood Tracking
        if guild.id not in self.join_trackers:
            self.join_trackers[guild.id] = deque()

        tracker = self.join_trackers[guild.id]
        tracker.append(now)

        window = cfg.get("window_seconds", 10)
        threshold = cfg.get("join_threshold", 5)

        # Prune old timestamps
        while tracker and tracker[0] < now - window:
            tracker.popleft()

        # 3. Check if flood triggered or shield is active
        if len(tracker) >= threshold and not cfg.get("shield_active", False):
            # Trip the alarm! Activate Raid Shield
            cfg["shield_active"] = True
            self.save_config(guild.id, cfg)

            embed = discord.Embed(
                title="🛡️ RAID SHIELD ENGAGED — LOCKDOWN ACTIVE",
                description=f"**Mass join detected!** `{len(tracker)}` members joined in under `{window}` seconds.\n\n"
                            f"**Automated Action:** The server is now in lockdown. Incoming joins will be automatically handled according to server policy (`{cfg.get('action', 'kick').upper()}`).\n"
                            f"To disable lockdown, run `!raidshield off`.",
                color=discord.Color.red()
            )
            embed.set_footer(text="PermBot Automated Security Guard")
            asyncio.create_task(self._send_mod_alert(guild, embed))

        # If shield is currently engaged
        if cfg.get("shield_active", False):
            if cfg.get("action") == "kick":
                try:
                    await member.kick(reason="[PermBot Anti-Raid] Automatic kick during active server raid lockdown.")
                    logger.info(f"Anti-Raid auto-kicked {member.id} from {guild.name}")
                except Exception as e:
                    logger.error(f"Failed to auto-kick raid user {member.id}: {e}")

    @commands.group(name="raidshield", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def raidshield(self, ctx: commands.Context):
        """Displays current Anti-Raid shield status and settings."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        cfg = self.get_config(ctx.guild.id)
        embed = discord.Embed(title="🛡️ PermBot Anti-Raid Shield Configuration", color=discord.Color.blurple())

        status = "🔴 ACTIVE (LOCKDOWN)" if cfg.get("shield_active") else ("🟢 ENABLED (MONITORING)" if cfg.get("enabled") else "⚪ DISABLED")
        embed.add_field(name="Current Status", value=f"**{status}**", inline=False)
        embed.add_field(name="Join Threshold", value=f"`{cfg.get('join_threshold')} joins` in `{cfg.get('window_seconds')} seconds`", inline=True)
        embed.add_field(name="Shield Action", value=f"`{cfg.get('action', 'kick').upper()}`", inline=True)
        embed.add_field(name="Min Account Age Gate", value=f"`{cfg.get('min_account_age_days')} days`", inline=True)

        embed.add_field(
            name="Available Commands",
            value="`!raidshield on` — Manually engage lockdown\n"
                  "`!raidshield off` — Disengage lockdown\n"
                  "`!raidshield config <joins> <seconds> <kick/alert>` — Set limits\n"
                  "`!raidshield age <days>` — Set minimum account age",
            inline=False
        )

        try:
            await ctx.author.send(embed=embed)
            msg = await ctx.send("🛡️ Sent Anti-Raid configuration to your DMs.")
            asyncio.create_task(delete_after_delay(msg, 8))
        except Exception:
            await ctx.send(embed=embed)

    @raidshield.command(name="on")
    @commands.has_permissions(administrator=True)
    async def raidshield_on(self, ctx: commands.Context):
        """Manually forces the Anti-Raid lockdown shield ON."""
        cfg = self.get_config(ctx.guild.id)
        cfg["shield_active"] = True
        cfg["enabled"] = True
        self.save_config(ctx.guild.id, cfg)
        await ctx.send("🛡️ **Raid Shield is now FORCED ON.** Incoming joins will be automatically handled until turned off (`!raidshield off`).")

    @raidshield.command(name="off")
    @commands.has_permissions(administrator=True)
    async def raidshield_off(self, ctx: commands.Context):
        """Disengages the Anti-Raid lockdown shield."""
        cfg = self.get_config(ctx.guild.id)
        cfg["shield_active"] = False
        self.save_config(ctx.guild.id, cfg)
        if ctx.guild.id in self.join_trackers:
            self.join_trackers[ctx.guild.id].clear()
        await ctx.send("✅ **Raid Shield Lockdown Disengaged.** Server is back in normal monitoring mode.")

    @raidshield.command(name="age")
    @commands.has_permissions(administrator=True)
    async def raidshield_age(self, ctx: commands.Context, days: int):
        """Sets the minimum account age (in days) to flag in mod-logs."""
        cfg = self.get_config(ctx.guild.id)
        cfg["min_account_age_days"] = max(0, days)
        self.save_config(ctx.guild.id, cfg)
        await ctx.send(f"🛡️ Account age flag set to **{cfg['min_account_age_days']} days**.")

    @raidshield.command(name="config")
    @commands.has_permissions(administrator=True)
    async def raidshield_config(self, ctx: commands.Context, joins: int, seconds: int, action: str = "kick"):
        """Configures the raid trigger (e.g., !raidshield config 5 10 kick)."""
        if action.lower() not in ["kick", "alert"]:
            return await ctx.send("❌ Action must be either `kick` or `alert`.")

        cfg = self.get_config(ctx.guild.id)
        cfg["join_threshold"] = max(2, joins)
        cfg["window_seconds"] = max(3, seconds)
        cfg["action"] = action.lower()
        self.save_config(ctx.guild.id, cfg)
        await ctx.send(f"🛡️ Anti-Raid configured: Trigger on **{joins} joins** within **{seconds} seconds** -> Action: **{action.upper()}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaid(bot))
