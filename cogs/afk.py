import discord
from discord.ext import commands
import os
import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Dict
from utils.helpers import load_json, save_json, delete_after_delay

AFK_PATH = os.path.join("data", "afk.json")


class AFK(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-memory cooldown tracker to avoid mention-spam: {(guild_id, mentioned_user_id): last_alert_time}
        self.mention_cooldowns: Dict[tuple, float] = {}

    def get_afk_data(self) -> dict:
        return load_json(AFK_PATH, {})

    def save_afk_data(self, data: dict):
        save_json(AFK_PATH, data)

    @commands.command(name="afk")
    async def set_afk(self, ctx: commands.Context, *, reason: Optional[str] = "AFK"):
        """Sets your status to AFK. The bot will notify anyone who mentions you."""
        data = self.get_afk_data()
        gid = str(ctx.guild.id)
        uid = str(ctx.author.id)

        if gid not in data:
            data[gid] = {}

        data[gid][uid] = {
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "display_name": ctx.author.display_name
        }
        self.save_afk_data(data)

        # Optional nickname tag: [AFK] Name
        try:
            if not ctx.author.display_name.startswith("[AFK]"):
                new_nick = f"[AFK] {ctx.author.display_name}"[:32]
                await ctx.author.edit(nick=new_nick)
        except Exception:
            pass  # Ignored if bot lacks permission or target is server owner

        embed = discord.Embed(
            description=f"💤 {ctx.author.mention} is now **AFK**:\n*{reason}*",
            color=discord.Color.blurple()
        )
        msg = await ctx.send(embed=embed)
        asyncio.create_task(delete_after_delay(msg, 10))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        gid = str(message.guild.id)
        author_id = str(message.author.id)
        data = self.get_afk_data()

        guild_data = data.get(gid, {})

        # 1. Check if the author is returning from AFK (ignore if they just ran !afk)
        if author_id in guild_data and not message.content.strip().lower().startswith("!afk"):
            afk_info = guild_data.pop(author_id)
            self.save_afk_data(data)

            # Try restoring nickname
            try:
                if message.author.display_name.startswith("[AFK] "):
                    clean_nick = message.author.display_name.replace("[AFK] ", "")
                    await message.author.edit(nick=clean_nick)
            except Exception:
                pass

            # Calculate how long they were AFK
            try:
                afk_time = datetime.fromisoformat(afk_info["timestamp"])
                time_str = discord.utils.format_dt(afk_time, "R")
            except Exception:
                time_str = "a while ago"

            welcome_msg = await message.channel.send(
                f"👋 Welcome back {message.author.mention}! I removed your AFK status *(set {time_str})*."
            )
            asyncio.create_task(delete_after_delay(welcome_msg, 8))

        # 2. Check if the message mentions any users who are currently AFK
        if message.mentions and guild_data:
            now = time.time()
            for mentioned in message.mentions:
                mid = str(mentioned.id)
                if mid in guild_data and mentioned.id != message.author.id:
                    # 10-second anti-spam cooldown per mentioned user
                    cd_key = (message.guild.id, mentioned.id)
                    if now - self.mention_cooldowns.get(cd_key, 0) < 10:
                        continue

                    self.mention_cooldowns[cd_key] = now
                    info = guild_data[mid]
                    reason = info.get("reason", "AFK")

                    try:
                        afk_time = datetime.fromisoformat(info["timestamp"])
                        time_str = discord.utils.format_dt(afk_time, "R")
                    except Exception:
                        time_str = "earlier"

                    embed = discord.Embed(
                        description=f"💤 **{mentioned.display_name}** is currently AFK:\n*{reason}* — ({time_str})",
                        color=discord.Color.gold()
                    )
                    alert_msg = await message.channel.send(embed=embed)
                    asyncio.create_task(delete_after_delay(alert_msg, 12))


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))
